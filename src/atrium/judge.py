"""EnronQA の自由記述の回答の採点（判定モデル．p0004 §8.6）．制御点のコンテナで `atrium judge*` から実行する．

判定のプロンプトは EnronQA の原論文（Ryan et al., arXiv:2505.00263，CC BY 4.0）の付録 B.6「LLM as a Judge」と同じである．
判定モデルは正解のメールを見たうえで，回答が正解と一致するかを判定する．判定モデルの出力の最後の "Answer:" の行が
yes・true・correct などで始まれば正解とする（incorrect・not などで始まれば不正解．どちらでもなければ判定できなかったとする）．

- `judge`：実行の results.jsonl の回答を採点し，judgements.jsonl（質問 ID・正誤・判定できたか・回答と正解メールの
  最長共通部分列の比率だけ．本文と判定の理由は含めない）を実行のディレクトリに書く．
- `judge-validate`：データに付属する別解（正解と一致すると原論文の 70B の判定モデルが判定したもの）と誤答を採点し，
  別解を正解・誤答を不正解とする割合を測る（§8.6 の 1）．
- `judge-kappa`：本実験の出力から層化抽出した回答を，より大きい参照モデルで採点し直し，Cohen の κ を出す（§8.6 の 2）．

判定モデルへの要求は，複数の Ollama（制御点と wafl500〜509 の GPU）へ均等に振り分けて並列に送る．
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from atrium import llm
from atrium.config import AtriumConfig, LlmConfig
from atrium.paths import DatasetPaths
from atrium.routing.advert import tokenize

logger = logging.getLogger(__name__)

JUDGEMENTS = "judgements.jsonl"
# 1 台の Ollama に同時に送る要求の数（OLLAMA_NUM_PARALLEL と同じ）
PER_URL_PARALLEL = 2
# 失敗した要求を次の Ollama へ振り替えて送り直す回数．1 台の一時的な失敗で全件の採点が止まらないようにする
JUDGE_RETRIES = 2
# Ollama は同じトークンの繰り返しが続くと生成を打ち切り，500 とこの文言を返す（0.35 で確認．2026-10-10 の κ で
# qwen3:14b が 300 件中 2 件で陥った）．温度 0 ではどの Ollama でも同じく打ち切られるので，送り直さずに
# 判定できなかった出力として扱う
_REPEAT_ABORT = "token repeat limit reached"
JUDGE_PROMPT = (
    "Given an email, a question about that email, a gold answer to that question, and a student's "
    "potentially correct or incorrect response, judge whether the answer matches the gold answer.\n\n"
    "---\n\nFollow the following format.\n\n"
    "Email: The email we want to judge the answer to\n"
    "Question: The question we want to judge the answer to\n"
    "Correct Answer: The correct answer to the question\n"
    "Student Answer: The student answer we want to judge\n"
    "Reasoning: Let's think step by step in order to ${{produce the correct}}\n"
    "Answer: Whether the answer is correct\n\n---\n\n"
    "Email: {email}\n"
    "Question: {question}\n"
    "Correct Answer: {gold}\n"
    "Student Answer: {answer}\n"
    "Reasoning: Let's think step by step in order to"
)
_ANSWER_LINE = re.compile(r"^\s*\**Answer\**\s*:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
# 判定の行の語（否定を先に調べる．"incorrect" は "correct" を含むため）
_NEGATIVE = re.compile(r"\b(no|false|incorrect|not correct|not match|does not|doesn't|wrong)\b")
_POSITIVE = re.compile(r"\b(yes|true|correct|matches|match)\b")


@dataclass(frozen=True)
class JudgeItem:
    """採点する 1 件．"""

    key: str
    email: str
    question: str
    gold: str
    answer: str


def parse_verdict(output: str) -> bool | None:
    """判定モデルの出力の最後の "Answer:" の行から正誤を取り出す（取り出せなければ None）．"""
    found = _ANSWER_LINE.findall(output)
    if not found:
        return None
    text = str(found[-1]).strip().lower()
    if _NEGATIVE.search(text):
        return False
    if _POSITIVE.search(text):
        return True
    return None


def lcs_ratio(answer: str, email: str) -> float:
    """回答の語のうち，正解のメールとの最長共通部分列（語の単位）に入る割合．"""
    a = tokenize(answer)
    b = tokenize(email)
    if not a:
        return 0.0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            cur[j] = prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1] / len(a)


def _judge_llm_config(cfg: AtriumConfig) -> LlmConfig:
    """判定は思考モードを使わず，温度 0 で 1 回だけ生成する（他の生成の設定は llm と同じ）．"""
    return cfg.llm.model_copy(update={"think": False, "temperature": 0.0})


async def judge_items(
    items: Sequence[JudgeItem],
    urls: Sequence[str],
    model: str,
    cfg: AtriumConfig,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, tuple[bool | None, str]]:
    """全件を採点する．key → （正誤，判定モデルの出力）．transport はテストで Ollama を模すときだけ渡す．"""
    llm_cfg = _judge_llm_config(cfg)
    semaphores = {url: asyncio.Semaphore(PER_URL_PARALLEL) for url in urls}
    out: dict[str, tuple[bool | None, str]] = {}
    async with httpx.AsyncClient(transport=transport) as client:

        async def ask(i: int, prompt: str) -> llm.LlmResult | None:
            """判定モデルの出力を返す．繰り返しで打ち切られたときは None．"""
            for attempt in range(JUDGE_RETRIES + 1):
                url = urls[(i + attempt) % len(urls)]
                try:
                    async with semaphores[url]:
                        return await llm.chat(
                            client, url, model, [{"role": "user", "content": prompt}], llm_cfg
                        )
                except httpx.HTTPStatusError as exc:
                    if _REPEAT_ABORT in exc.response.text:
                        logger.warning("judge output aborted by repeat limit at %s", url)
                        return None
                    if attempt == JUDGE_RETRIES:
                        raise
                    logger.warning(
                        "judge request to %s failed (%s); retrying on another URL", url, exc
                    )
                except httpx.HTTPError as exc:
                    if attempt == JUDGE_RETRIES:
                        raise
                    logger.warning(
                        "judge request to %s failed (%s); retrying on another URL", url, exc
                    )
            raise AssertionError("unreachable")

        async def one(i: int, item: JudgeItem) -> None:
            prompt = JUDGE_PROMPT.format(
                email=item.email, question=item.question, gold=item.gold, answer=item.answer
            )
            result = await ask(i, prompt)
            out[item.key] = (
                (None, "") if result is None else (parse_verdict(result.content), result.content)
            )
            if len(out) % 100 == 0:
                logger.info("judged %d / %d", len(out), len(items))

        await asyncio.gather(*(one(i, item) for i, item in enumerate(items)))
    return out


def email_lookup(cfg: AtriumConfig, paths: DatasetPaths) -> dict[str, str]:
    """メールの path → 本文（受信箱の chunk から）．"""
    from atrium.data_enronqa import read_inbox

    return {
        d["id"]: d["content"]
        for inbox in cfg.data.require_enronqa().sources
        for d in read_inbox(paths, inbox)
    }


def judge_run(
    cfg: AtriumConfig, paths: DatasetPaths, run_dir: Path, urls: Sequence[str], model: str
) -> dict[str, Any]:
    """実行の回答を採点し，judgements.jsonl を書く（判定モデルの出力は raw_dir にだけ残す）．"""
    from atrium.analysis import read_results
    from atrium.data_enronqa import read_gold

    gold = read_gold(paths)
    emails = email_lookup(cfg, paths)
    questions = question_texts(paths)
    rows = [r for r in read_results(run_dir / "results.jsonl") if r.get("error") is None]
    items = [
        JudgeItem(
            key=r["qid"],
            email=emails[gold[r["qid"]]["path"]],
            question=questions[r["qid"]],
            gold=gold[r["qid"]]["gold_answer"],
            answer=r.get("final_answer", ""),
        )
        for r in rows
        if "final_answer" in r
    ]
    verdicts = asyncio.run(judge_items(items, urls, model, cfg))
    with (run_dir / JUDGEMENTS).open("w", encoding="utf-8") as f:
        for item in items:
            verdict, _ = verdicts[item.key]
            f.write(
                json.dumps(
                    {
                        "qid": item.key,
                        "judge_model": model,
                        "correct": verdict,
                        "answer_overlap_lcs": lcs_ratio(item.answer, item.email),
                    }
                )
                + "\n"
            )
    raw_dir = paths.root / "judge_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    with (raw_dir / f"{run_dir.name}.{model.replace(':', '_')}.jsonl").open(
        "w", encoding="utf-8"
    ) as f:
        for key, (verdict, raw) in verdicts.items():
            f.write(json.dumps({"qid": key, "correct": verdict, "output": raw}) + "\n")
    judged = [v for v, _ in verdicts.values()]
    return {
        "n": len(judged),
        "correct": sum(v is True for v in judged),
        "unparsed": sum(v is None for v in judged),
    }


def question_texts(paths: DatasetPaths) -> dict[str, str]:
    """質問 ID → 質問文（test・dev・train の全ての質問のファイルから）．"""
    from atrium.benchmarks import load_questions
    from atrium.data_enronqa import QUESTION_FILES

    return {
        q.qid: q.question
        for name in QUESTION_FILES.values()
        for q in load_questions(paths.questions.parent / name)
    }


def validate_judge(
    cfg: AtriumConfig, paths: DatasetPaths, urls: Sequence[str], model: str, n: int
) -> dict[str, Any]:
    """付属の別解・誤答を n 件ずつ採点し，正しく判定した割合を返す（§8.6 の 1）．"""
    from atrium.data_enronqa import read_gold

    gold = [g for g in read_gold(paths).values() if g["split"] == "test"]
    rng = np.random.default_rng(cfg.experiment.seed)
    picked = [gold[int(i)] for i in rng.permutation(len(gold))[:n]]
    emails = email_lookup(cfg, paths)
    questions = question_texts(paths)
    items: list[JudgeItem] = []
    for g in picked:
        email = emails[g["path"]]
        question = questions[g["qid"]]
        items.append(
            JudgeItem(
                f"alt:{g['qid']}", email, question, g["gold_answer"], g["alternate_answers"][0]
            )
        )
        items.append(
            JudgeItem(
                f"inc:{g['qid']}", email, question, g["gold_answer"], g["incorrect_answers"][0]
            )
        )
    verdicts = asyncio.run(judge_items(items, urls, model, cfg))
    alt = [verdicts[k][0] for k in verdicts if k.startswith("alt:")]
    inc = [verdicts[k][0] for k in verdicts if k.startswith("inc:")]
    return {
        "model": model,
        "n_alternate": len(alt),
        "n_incorrect": len(inc),
        "alternate_judged_correct": sum(v is True for v in alt) / len(alt),
        "incorrect_judged_incorrect": sum(v is False for v in inc) / len(inc),
        "unparsed": sum(v is None for v in alt + inc),
    }


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float:
    """2 つの判定の Cohen の κ．"""
    n = len(a)
    if n == 0:
        return 0.0
    agree = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if expected == 1 else (agree - expected) / (1 - expected)


def kappa_against_reference(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    run_dirs: Sequence[Path],
    urls: Sequence[str],
    reference_model: str,
    n: int,
) -> dict[str, Any]:
    """本実験の出力を実行ごとに同じ数ずつ抜き出し，参照モデルで採点し直して κ を出す（§8.6 の 2）．"""
    from atrium.analysis import read_results
    from atrium.data_enronqa import read_gold

    gold = read_gold(paths)
    emails = email_lookup(cfg, paths)
    questions = question_texts(paths)
    rng = np.random.default_rng(cfg.experiment.seed)
    per_run = -(-n // len(run_dirs))
    items: list[JudgeItem] = []
    primary: dict[str, bool] = {}
    for run_dir in run_dirs:
        judged = {
            j["qid"]: j["correct"]
            for j in map(
                json.loads, (run_dir / JUDGEMENTS).read_text(encoding="utf-8").splitlines()
            )
            if j["correct"] is not None
        }
        rows = [r for r in read_results(run_dir / "results.jsonl") if r["qid"] in judged]
        for i in rng.permutation(len(rows))[:per_run]:
            r = rows[int(i)]
            key = f"{run_dir.name}:{r['qid']}"
            items.append(
                JudgeItem(
                    key,
                    emails[gold[r["qid"]]["path"]],
                    questions[r["qid"]],
                    gold[r["qid"]]["gold_answer"],
                    r["final_answer"],
                )
            )
            primary[key] = judged[r["qid"]]
    verdicts = asyncio.run(judge_items(items, urls, reference_model, cfg))
    keys = [k for k in primary if verdicts[k][0] is not None]
    a = [primary[k] for k in keys]
    b = [bool(verdicts[k][0]) for k in keys]
    return {
        "reference_model": reference_model,
        "n": len(keys),
        "n_unparsed_reference": len(primary) - len(keys),
        "agreement": sum(x == y for x, y in zip(a, b, strict=True)) / len(keys) if keys else 0.0,
        "kappa": cohen_kappa(a, b),
    }
