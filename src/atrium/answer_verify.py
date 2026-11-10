"""保存した候補の回答を検証の LLM で確かめ，Yes / No の対数確率の差 v を求める（d0009 B3 Iteration 28．`atrium verify-features`）．

入力は `answer.save_candidates: true` の実行（results.jsonl の `owner_answer.candidates`）．各候補について，質問者へ渡る同じ
上位 5 通（`context_doc_ids`．`prompts.format_context` と同じ並べ方）・質問・保存した回答を検証のプロンプト（`VERIFY_PROMPT`．
user の 1 通．system なし）で検証の LLM に問い，最初の 1 トークンの `top_logprobs` から

    v = ℓ_Y − ℓ_N

を求める（ℓ_Y・ℓ_N は，前後の空白を除き大文字小文字を区別せずに `yes`・`no` に当たるトークンの対数確率の logsumexp）．
片方が上位 N に無いときは，上位 N の最小の対数確率で代える（上限の値）．両方とも無い，または失敗した候補は v = None
（選択では −∞ として扱う）．文字列の解析には頼らない．

出力 `verify_features.jsonl`（1 行 1 候補．質問 ID・候補の番号・受信箱・v・ℓ_Y・ℓ_N・最初のトークン・`answer_empty`・
`prompt_eval_count`・`total_duration_s`・`error`）．回答の本文は書かない（メールの本文も書かない）．
`--resume` は，得点のある行と再び試しても同じ結果の行（文脈長の超過など）を飛ばし，その他の失敗を再び試す（同じ (qid, index) の後の行が前の行に優先する）．

使い方（gpu4．判定の container．127.0.0.1:11436．`--url` は複数指定でき，候補を順に振り分ける）:
    uv run atrium verify-features --run-dir results/d0009_b3s_dev --out-dir results/d0009_b3v_dev \\
        --data-dir artifacts --model gemma4-12b-it-qat --url http://127.0.0.1:11436 [--resume] [--limit N]
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from atrium import llm
from atrium.config import AtriumConfig, LlmConfig
from atrium.paths import DatasetPaths
from atrium.prompts import format_context
from atrium.store import RetrievedDoc

logger = logging.getLogger(__name__)

VERIFY_FEATURES = "verify_features.jsonl"
# 検証のプロンプト（事前登録で固定．dev を見て変えない．sha256 は試験で固定する）
VERIFY_PROMPT = """Emails:
{context}

Question: {question}
Proposed answer: {answer}

Is the proposed answer to the question fully supported by the emails above? Answer with Yes or No only."""
# 事前登録の呼び出し：温度 0・種 12・1 トークン・文脈長 16384・上位 20 件
VERIFY_SEED = 12
VERIFY_NUM_PREDICT = 1
VERIFY_TOP_LOGPROBS = 20
# 判定の container の OLLAMA_NUM_PARALLEL と同じ
VERIFY_PARALLEL = 2
# 失敗した要求を送り直す回数
VERIFY_RETRIES = 2
# 入力が文脈長を超えると Ollama が 400 とこの文言を返す（入力が同じなら結果も同じなので送り直さない）
CONTEXT_OVERFLOW = "exceeds the available context size"
OVERFLOW_ERROR = "context_overflow"
# yes・no のどちらも最初のトークンの上位に無い（同じ入力なら同じ結果なので送り直さない）
NO_YES_NO_ERROR = "no_yes_no_in_top"
PROGRESS_EVERY = 200


@dataclass(frozen=True)
class VerifyScore:
    """最初のトークンの top_logprobs から求めた検証の得点．v・l_yes・l_no は両方のトークンが上位に無いとき None."""

    v: float | None
    l_yes: float | None
    l_no: float | None
    first_token: str | None
    # 最初のトークン（確率が最大のもの）が yes・no のどちらでもない
    first_token_other: bool


def logsumexp(values: Sequence[float]) -> float:
    """対数確率の和の対数（空なら −inf）．"""
    if not values:
        return float("-inf")
    top = max(values)
    return top + math.log(sum(math.exp(v - top) for v in values))


def verification_score(top: Sequence[tuple[str, float]]) -> VerifyScore:
    """（トークン，対数確率）の上位の列から v = ℓ_Y − ℓ_N を求める．片方が無ければ上位の最小の対数確率で代え，両方無ければ None."""
    if not top:
        return VerifyScore(None, None, None, None, True)
    yes = [lp for tok, lp in top if tok.strip().lower() == "yes"]
    no = [lp for tok, lp in top if tok.strip().lower() == "no"]
    best_token = max(top, key=lambda t: t[1])[0]
    other = best_token.strip().lower() not in ("yes", "no")
    if not yes and not no:
        return VerifyScore(None, None, None, best_token, other)
    floor = min(lp for _, lp in top)
    l_yes = logsumexp(yes) if yes else floor
    l_no = logsumexp(no) if no else floor
    return VerifyScore(l_yes - l_no, l_yes, l_no, best_token, other)


def build_verify_messages(context: str, question: str, answer: str) -> list[dict[str, str]]:
    """検証のプロンプトの user の 1 通（system なし）．"""
    return [
        {
            "role": "user",
            "content": VERIFY_PROMPT.format(context=context, question=question, answer=answer),
        }
    ]


def prompt_sha256() -> str:
    """検証のプロンプトの雛形の sha256（文面が変わっていないことを試験で固定する）．"""
    return hashlib.sha256(VERIFY_PROMPT.encode("utf-8")).hexdigest()


def verify_llm_config(cfg: LlmConfig) -> LlmConfig:
    """検証の呼び出しの設定（think なし・温度 0・1 トークン．文脈長は config の値＝16384）．"""
    return cfg.model_copy(
        update={"think": False, "temperature": 0.0, "num_predict": VERIFY_NUM_PREDICT}
    )


def read_verify_rows(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """verify_features.jsonl → (質問 ID，候補の番号) → 行（同じ候補は後の行が優先）．無ければ空．"""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {(j["qid"], int(j["index"])): j for j in map(json.loads, f) if j}


def is_final_row(row: Mapping[str, Any]) -> bool:
    """得点のある行か，再び試しても同じ結果の行（文脈長の超過・yes/no が上位に無い）．--resume で飛ばす．"""
    return row.get("error") in (None, OVERFLOW_ERROR, NO_YES_NO_ERROR)


@dataclass(frozen=True)
class VerifyJob:
    """検証する候補 1 件．"""

    qid: str
    index: int
    source: str
    messages: list[dict[str, str]]
    answer_empty: bool


def build_jobs(
    rows: Sequence[Mapping[str, Any]],
    question_of: Mapping[str, str],
    docs_of: Mapping[str, RetrievedDoc],
) -> list[VerifyJob]:
    """実行の全候補の検証の入力を作る（失敗した問は飛ばす）．文脈は context_doc_ids の順に format_context で整形する．"""
    jobs = []
    for row in rows:
        if row.get("error") is not None:
            continue
        for index, c in enumerate((row.get("owner_answer") or {}).get("candidates") or []):
            context = format_context([docs_of[d] for d in c.get("context_doc_ids") or []])
            answer = str(c.get("answer") or "")
            jobs.append(
                VerifyJob(
                    row["qid"],
                    index,
                    str(c["source"]),
                    build_verify_messages(context, question_of[row["qid"]], answer),
                    not answer.strip(),
                )
            )
    return jobs


async def verify_job(
    client: httpx.AsyncClient, url: str, model: str, llm_cfg: LlmConfig, job: VerifyJob
) -> dict[str, Any]:
    """1 候補を検証し，verify_features.jsonl の 1 行を返す（失敗しても例外にせず error に残す）．"""
    base = {
        "qid": job.qid,
        "index": job.index,
        "source": job.source,
        "answer_empty": job.answer_empty,
    }
    error = "unknown"
    for _ in range(1 + VERIFY_RETRIES):
        try:
            result = await llm.chat(
                client,
                url,
                model,
                job.messages,
                llm_cfg,
                top_logprobs=VERIFY_TOP_LOGPROBS,
                seed=VERIFY_SEED,
            )
        except httpx.HTTPStatusError as e:
            if CONTEXT_OVERFLOW in e.response.text:
                return {**base, "v": None, "error": OVERFLOW_ERROR}
            error = f"http_{e.response.status_code}"
        except httpx.HTTPError as e:
            error = type(e).__name__
        else:
            score = verification_score(result.first_top_logprobs)
            return {
                **base,
                "v": score.v,
                "l_yes": score.l_yes,
                "l_no": score.l_no,
                "first_token": score.first_token,
                "first_token_other": score.first_token_other,
                "prompt_eval_count": result.prompt_tokens,
                "total_duration_s": result.total_s,
                "error": None if score.v is not None else NO_YES_NO_ERROR,
            }
    return {**base, "v": None, "error": error}


async def run_jobs(
    jobs: Sequence[VerifyJob],
    urls: Sequence[str],
    model: str,
    llm_cfg: LlmConfig,
    out_path: Path,
) -> int:
    """URL ごとに VERIFY_PARALLEL 並列で検証し（候補は番号の順に URL へ振り分ける），1 件ずつ out_path へ追記する．失敗（得点なし）の件数を返す．"""
    semaphores = {url: asyncio.Semaphore(VERIFY_PARALLEL) for url in urls}
    failed = done = 0
    async with httpx.AsyncClient() as client:
        with out_path.open("a", encoding="utf-8") as f:

            async def one(i: int, job: VerifyJob) -> None:
                nonlocal failed, done
                url = urls[i % len(urls)]
                async with semaphores[url]:
                    row = await verify_job(client, url, model, llm_cfg, job)
                f.write(json.dumps(row) + "\n")
                f.flush()
                failed += not is_final_row(row) or row["v"] is None
                done += 1
                if done % PROGRESS_EVERY == 0:
                    logger.info("verified %d / %d (no score so far: %d)", done, len(jobs), failed)

            await asyncio.gather(*(one(i, j) for i, j in enumerate(jobs)))
    return failed


def verify_run(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    run_dir: Path,
    out_dir: Path,
    model: str,
    urls: Sequence[str],
    resume: bool,
    limit: int | None = None,
) -> dict[str, Any]:
    """run_dir の全候補（limit なら先頭の limit 問の候補）を検証し out_dir/verify_features.jsonl に書く．要約を返す．

    urls は Ollama の URL（複数なら候補を順に振り分ける．Iteration 41 で GPU の PC 7 台へ分けるために追加）．
    """
    from atrium.analysis import read_results
    from atrium.data_enronqa import read_inbox
    from atrium.judge import question_texts

    rows = [r for r in read_results(run_dir / "results.jsonl") if r.get("error") is None]
    if limit is not None:
        rows = rows[:limit]
    inboxes = sorted(
        {
            str(c["source"])
            for r in rows
            for c in (r.get("owner_answer") or {}).get("candidates") or []
        }
    )
    docs_of = {
        d["id"]: RetrievedDoc(d["id"], d["title"], d["content"], 0.0)
        for inbox in inboxes
        for d in read_inbox(paths, inbox)
    }
    jobs = build_jobs(rows, question_texts(paths), docs_of)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / VERIFY_FEATURES
    if not resume and out_path.exists():
        raise SystemExit(f"{out_path} exists; pass --resume to continue (nothing is overwritten)")
    done = (
        {k for k, v in read_verify_rows(out_path).items() if is_final_row(v)} if resume else set()
    )
    todo = [j for j in jobs if (j.qid, j.index) not in done]
    logger.info(
        "%s: %d candidates, %d already verified, %d to verify",
        run_dir,
        len(jobs),
        len(done),
        len(todo),
    )
    failed = asyncio.run(run_jobs(todo, urls, model, verify_llm_config(cfg.llm), out_path))
    final = read_verify_rows(out_path)
    return {
        "run": run_dir.name,
        "n_candidates": len(jobs),
        "n_verified_now": len(todo),
        "n_no_score": sum(final.get((j.qid, j.index), {}).get("v") is None for j in jobs),
        "n_failed_now": failed,
        "prompt_sha256": prompt_sha256(),
    }
