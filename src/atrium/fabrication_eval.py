"""d0009 A2：回答を「正解・わからない・誤答（作り話）」の 3 分類にして，作り話の割合を集計する（既存の結果の追加の採点）．

事前登録は .claude/research/journal.md の「Iteration 21」と backlog B74．規則：

1. 正解は既存の判定（`judgements.jsonl`・`candidate_judgements.jsonl` の `correct`）をそのまま使う．`correct` が null は「判定不能」で，割合の分母から除く．
2. 不正解のうち，UNKNOWN で始まる（`prompts.is_unknown_answer`）か，棄権の判定モデルが NOT_ATTEMPTED とした回答が「わからない」．
3. 不正解のうちそれ以外が「誤答」（作り話）．棄権の判定が取り出せなかった回答は「棄権の判定不能」とし，誤答の下限には入れず上限には入れる．

棄権の判定モデルは qwen3:8b（既存の判定と同じ設定）で，質問と回答だけを渡す（メール・正解は渡さない．正解のとき・UNKNOWN で始まるときは送らない）．
`abstain_judge_run` が `candidate_abstain.jsonl`（全候補）と `abstain.jsonl`（最終回答）を `labels/<run>/` に書き，
`evaluate_fabrication` がそれらと既存の判定から F_non・F_own・F_final・F_rand と H-F1〜H-F3 の判定（verdict.json）を出す．
並列の送り方と `--resume` は `judge.judge_items` を使い，統計は `analysis.mcnemar_exact`・`analysis.wilson_interval`・`verdict` の定数を使う．

使い方（判定モデルの Ollama へ届くこと．操作端末から制御点の Ollama へは SSH の転送を使う）:
    uv run atrium abstain-judge --data-dir artifacts --ollama-url http://127.0.0.1:11435 \\
        --run-dir results/p0007_a6_test --out-dir results/d0009_a2_fabrication/labels [--resume]
    uv run atrium fabrication-eval --stage {table,gate,report} --split {test,dev} ...   # 集計は Ollama 不要
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import mcnemar_exact, read_results, wilson_interval
from atrium.arrays import F64Array
from atrium.config import AtriumConfig
from atrium.judge import (
    JudgeItem,
    JudgeResume,
    cohen_kappa,
    judge_items,
    question_texts,
    unique_items,
)
from atrium.paths import DatasetPaths
from atrium.prompts import is_unknown_answer
from atrium.select_eval import apply_rule, read_candidate_judgements, read_ce_features
from atrium.verdict import ALPHA, BOOTSTRAP_CHUNK, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED

logger = logging.getLogger(__name__)

CANDIDATE_ABSTAIN = "candidate_abstain.jsonl"
ABSTAIN = "abstain.jsonl"
# 棄権の判定の件ごとの状態（abstain の値が付く／付かない理由）
STATUS_LEXICAL = "lexical_unknown"  # UNKNOWN で始まる．判定モデルへは送らない（わからない）
STATUS_MODEL = "model"  # 判定モデルが判定した
STATUS_NOT_SENT = "not_sent"  # 正解か判定不能．棄権の判定は要らない
STATUS_UNPARSED = "unparsed"  # 判定モデルの出力から取り出せなかった（1 回送り直しても）
RETRY_SUFFIX = "#retry"

# 棄権の判定のプロンプト．dev の不正解の候補 60 件で定義どおりに分かれるかを見て 1 回までだけ直し，固定した後に test を採点する
# （固定した文面の SHA-256 は `abstain_prompt_sha256`．journal の Iteration 21 に追記する）
ABSTAIN_PROMPT = (
    "A system answered a question about an email inbox. Decide whether the response ATTEMPTED to "
    "answer the question or NOT_ATTEMPTED.\n\n"
    "NOT_ATTEMPTED: the response only says that the answer is not available (for example: it does "
    "not know, no matching email was found, the emails do not mention it, or the answer cannot be "
    "determined) and states no specific answer to the question, that is, no name, date, number, "
    "event, reason or other concrete content that answers it. A sentence that only says no email "
    'matches the description in the question (such as "There is no email from X with the subject '
    'Y") is NOT_ATTEMPTED.\n\n'
    "ATTEMPTED: the response states, asserts or guesses at least one specific piece of content "
    "that answers the question, even if it is hedged. A response that mixes a statement that "
    'nothing was found with a guess (such as "I cannot find it, but it is probably X") is '
    "ATTEMPTED. A response that asserts about the world, not about the provided emails, that the "
    "event did not happen or that the premise of the question is false is also ATTEMPTED.\n\n"
    "Give a one-sentence reason, then end with a final line that is exactly "
    '"Answer: ATTEMPTED" or "Answer: NOT_ATTEMPTED".\n\n'
    "Question: {question}\n"
    "Response: {answer}\n"
    "Reason:"
)
_ABSTAIN_LINE = re.compile(
    r"^\s*\**Answer\**\s*:\s*\**\s*(NOT[_ \-]?ATTEMPTED|ATTEMPTED)\b", re.I | re.M
)


def abstain_prompt_sha256() -> str:
    """棄権の判定のプロンプトの SHA-256（固定した文面の記録用）．"""
    return hashlib.sha256(ABSTAIN_PROMPT.encode("utf-8")).hexdigest()


def parse_abstain(output: str) -> bool | None:
    """判定モデルの出力の最後の "Answer:" の行から，棄権（NOT_ATTEMPTED）なら True，答えた（ATTEMPTED）なら False を取り出す．"""
    found = _ABSTAIN_LINE.findall(output)
    if not found:
        return None
    return str(found[-1]).upper().startswith("NOT")


# ── 3 分類 ──────────────────────────────────────────────────────────────────

LABEL_CORRECT = "correct"
LABEL_DONT_KNOW = "dont_know"
LABEL_FABRICATED = "fabricated"
LABEL_ABSTAIN_UNPARSED = "abstain_unparsed"
LABEL_UNJUDGED = "unjudged"


@dataclass(frozen=True)
class AbstainLabel:
    """棄権の判定の 1 件（status は STATUS_*．abstain は棄権なら True・答えたなら False・不明なら None）．"""

    status: str
    abstain: bool | None


def classify(correct: bool | None, abstain: AbstainLabel | None) -> tuple[str, bool]:
    """（3 分類のラベル，UNKNOWN の字句で「わからない」になったか）．不正解で棄権の判定が無ければ ValueError．"""
    if correct is None:
        return LABEL_UNJUDGED, False
    if correct:
        return LABEL_CORRECT, False
    if abstain is None or abstain.status == STATUS_NOT_SENT:
        raise ValueError("an incorrect answer has no abstain judgement")
    if abstain.status == STATUS_LEXICAL:
        return LABEL_DONT_KNOW, True
    if abstain.abstain is None:
        return LABEL_ABSTAIN_UNPARSED, False
    return (LABEL_DONT_KNOW if abstain.abstain else LABEL_FABRICATED), False


# ── 棄権の判定（Ollama．atrium abstain-judge） ────────────────────────────────

# `.progress.jsonl` の名前の区別（正誤の採点と同じ run_id・モデルの進捗と混ざらないように）
RAW_SUFFIX = "abstain"


def _abstain_status(correct: bool | None, answer: str) -> str:
    """棄権の判定を判定モデルへ送るかどうかを決める（送るなら STATUS_MODEL）．"""
    if correct is not False:
        return STATUS_NOT_SENT
    return STATUS_LEXICAL if is_unknown_answer(answer) else STATUS_MODEL


async def _judge_with_one_retry(
    items: Sequence[JudgeItem],
    urls: Sequence[str],
    model: str,
    cfg: AtriumConfig,
    resume: JudgeResume | None,
    request_timeout_s: float | None,
) -> dict[str, tuple[bool | None, str]]:
    """全件を判定し，取り出せなかった件は 1 回だけ送り直す（key に RETRY_SUFFIX を付けて別の件として進捗に残す）．"""
    common: dict[str, Any] = {
        "resume": resume,
        "request_timeout_s": request_timeout_s,
        "prompt_template": ABSTAIN_PROMPT,
        "parse": parse_abstain,
    }
    out = await judge_items(items, urls, model, cfg, **common)
    again = [
        JudgeItem(i.key + RETRY_SUFFIX, i.email, i.question, i.gold, i.answer)
        for i in items
        if i.key in out and out[i.key][0] is None
    ]
    if again:
        retried = await judge_items(again, urls, model, cfg, **common)
        for item in again:
            original_key = item.key[: -len(RETRY_SUFFIX)]
            if item.key in retried:
                out[original_key] = retried[item.key]
            else:
                # 送り直せなかった件を「取り出せなかった」に確定させない（未判定として残し，再実行で送り直す）
                del out[original_key]
    return out


def abstain_judge_run(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    run_dir: Path,
    out_dir: Path,
    urls: Sequence[str],
    model: str,
    resume: bool = False,
    request_timeout_s: float | None = None,
) -> dict[str, Any]:
    """実行の不正解の回答の棄権を判定し，out_dir/<run>/candidate_abstain.jsonl・abstain.jsonl を書く．

    対象は candidate_judgements.jsonl（あれば）・judgements.jsonl で不正解の回答（UNKNOWN で始まるものは判定モデルへ送らず，
    正解・判定不能は送らない）．同じ質問の同じ回答の文字列は 1 回だけ送る．未判定が残ったら（resume のとき）出力を書かず n_unjudged を返す．
    """
    rows = [r for r in read_results(run_dir / "results.jsonl") if r.get("error") is None]
    questions = question_texts(paths)
    final_judged: dict[str, bool | None] = {
        j["qid"]: j["correct"]
        for j in map(json.loads, (run_dir / "judgements.jsonl").read_text("utf-8").splitlines())
    }
    cand_path = run_dir / "candidate_judgements.jsonl"
    cand_judged = read_candidate_judgements(cand_path) if cand_path.exists() else {}

    # 件の一覧：（key，質問 ID，回答，正誤）．最終回答の key は qid，候補の key は "<qid>#<番号>"
    entries: list[tuple[str, str, str, bool | None]] = []
    for r in rows:
        if "final_answer" in r:
            entries.append((r["qid"], r["qid"], str(r["final_answer"]), final_judged.get(r["qid"])))
        for index, cand in enumerate((r.get("owner_answer") or {}).get("candidates") or []):
            entries.append(
                (
                    f"{r['qid']}#{index}",
                    r["qid"],
                    str(cand["answer"]),
                    cand_judged.get((r["qid"], index)),
                )
            )
    qid_of = {key: qid for key, qid, _, _ in entries}
    to_send = [
        JudgeItem(key, "", questions[qid], "", answer)
        for key, qid, answer, correct in entries
        if _abstain_status(correct, answer) == STATUS_MODEL
    ]
    unique, alias = unique_items(to_send, qid_of)
    raw_dir = paths.root / "judge_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{run_dir.name}.{model.replace(':', '_')}.{RAW_SUFFIX}"
    progress = JudgeResume.open(raw_dir / f"{stem}.progress.jsonl") if resume else None
    judged = asyncio.run(
        _judge_with_one_retry(unique, urls, model, cfg, progress, request_timeout_s)
    )
    n_unjudged = sum(item.key not in judged for item in unique)
    if n_unjudged:
        logger.error("%s: %d / %d unjudged; rerun with --resume", run_dir, n_unjudged, len(unique))
        return {"n_judge_calls": len(unique), "n_unjudged": n_unjudged}

    def label_of(key: str, answer: str, correct: bool | None) -> AbstainLabel:
        status = _abstain_status(correct, answer)
        if status == STATUS_LEXICAL:
            return AbstainLabel(STATUS_LEXICAL, True)
        if status == STATUS_NOT_SENT:
            return AbstainLabel(STATUS_NOT_SENT, None)
        verdict = judged[alias[key]][0]
        return AbstainLabel(STATUS_UNPARSED if verdict is None else STATUS_MODEL, verdict)

    target = out_dir / run_dir.name
    target.mkdir(parents=True, exist_ok=True)
    source_of = {
        (r["qid"], i): str(c["source"])
        for r in rows
        for i, c in enumerate((r.get("owner_answer") or {}).get("candidates") or [])
    }
    with (
        (target / ABSTAIN).open("w", encoding="utf-8") as fa,
        (target / CANDIDATE_ABSTAIN).open("w", encoding="utf-8") as fc,
    ):
        for key, qid, answer, correct in entries:
            label = label_of(key, answer, correct)
            record: dict[str, Any] = {"qid": qid, "status": label.status, "abstain": label.abstain}
            if "#" in key:
                index = int(key.rsplit("#", 1)[1])
                fc.write(
                    json.dumps({**record, "index": index, "source": source_of[(qid, index)]}) + "\n"
                )
            else:
                fa.write(json.dumps(record) + "\n")
    raw_path = raw_dir / f"{stem}.jsonl"
    with raw_path.open("w", encoding="utf-8") as f:
        for key, (verdict, raw) in judged.items():
            f.write(json.dumps({"qid": key, "abstain": verdict, "output": raw}) + "\n")
    statuses = Counter(label_of(k, a, c).status for k, _, a, c in entries)
    return {"n_judge_calls": len(unique), "n_unjudged": 0, "status_counts": dict(statuses)}


def abstain_judge_table(
    cfg: AtriumConfig,
    urls: Sequence[str],
    model: str,
    table_path: Path,
    out_path: Path,
    request_timeout_s: float | None = None,
) -> dict[str, Any]:
    """人手での確かめの表（id・question・answer の jsonl）を別のモデルで判定し，out_path に id・abstain を書く（参照用の κ）．"""
    table = [json.loads(line) for line in table_path.read_text("utf-8").splitlines() if line]
    items = [JudgeItem(t["id"], "", t["question"], "", t["answer"]) for t in table]
    judged = asyncio.run(_judge_with_one_retry(items, urls, model, cfg, None, request_timeout_s))
    with out_path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps({"id": item.key, "abstain": judged[item.key][0]}) + "\n")
    return {"model": model, "n": len(items), "unparsed": sum(v[0] is None for v in judged.values())}


# ── 集計 ────────────────────────────────────────────────────────────────────

# 候補・最終回答の 3 分類の数え方の列（UNKNOWN の字句で「わからない」になった件は別の列で数える）
COLUMNS = ("correct", "dk_lexical", "dk_model", "fabricated", "unparsed", "unjudged")
_COL = {name: i for i, name in enumerate(COLUMNS)}
# 揺れの幅（journal の Iteration 21「比べる 2 つの実行の関係」）
JITTER_MARGIN = 0.02


@dataclass(frozen=True)
class Stat:
    """質問ごとの（分子，分母）．割合は質問をまたいだ分子の和 / 分母の和（質問を単位にした再抽出に合わせる）．"""

    num: F64Array
    den: F64Array

    def value(self) -> float:
        """全質問での割合（分母が 0 なら nan）．"""
        d = float(self.den.sum())
        return float(self.num.sum()) / d if d else math.nan


def read_labels(path: Path) -> dict[tuple[str, int], AbstainLabel]:
    """candidate_abstain.jsonl → （質問 ID，候補の番号）→ 棄権の判定．"""
    with path.open(encoding="utf-8") as f:
        return {
            (j["qid"], int(j["index"])): AbstainLabel(j["status"], j["abstain"])
            for j in map(json.loads, f)
            if j
        }


def read_final_labels(path: Path) -> dict[str, AbstainLabel]:
    """abstain.jsonl → 質問 ID → 最終回答の棄権の判定．"""
    with path.open(encoding="utf-8") as f:
        return {j["qid"]: AbstainLabel(j["status"], j["abstain"]) for j in map(json.loads, f) if j}


def _vector(label: str, lexical: bool) -> list[float]:
    """3 分類のラベル 1 件 → COLUMNS の one-hot．"""
    v = [0.0] * len(COLUMNS)
    if label == LABEL_CORRECT:
        v[_COL["correct"]] = 1
    elif label == LABEL_DONT_KNOW:
        v[_COL["dk_lexical" if lexical else "dk_model"]] = 1
    elif label == LABEL_FABRICATED:
        v[_COL["fabricated"]] = 1
    elif label == LABEL_ABSTAIN_UNPARSED:
        v[_COL["unparsed"]] = 1
    else:
        v[_COL["unjudged"]] = 1
    return v


@dataclass(frozen=True)
class RunTables:
    """1 つの実行（候補を持つ A5・A6）の質問ごとの数え上げ（行は共通の質問の順）．

    owner・other：持ち主・持ち主以外の候補の COLUMNS ごとの件数（質問数 × 列）．
    chosen：規則ごとの，選んだ回答の one-hot（質問数 × 列）と，区分 (a)(b)(c)（0・1・2．無ければ -1）・持ち主を採ったか．
    """

    owner: F64Array
    other: F64Array
    chosen: dict[str, F64Array]
    segment: dict[str, list[int]]
    chosen_owner: dict[str, list[bool]]


def _candidate_vector(
    qid: str,
    index: int,
    judged: Mapping[tuple[str, int], bool | None],
    labels: Mapping[tuple[str, int], AbstainLabel],
) -> list[float]:
    label, lexical = classify(judged.get((qid, index)), labels.get((qid, index)))
    return _vector(label, lexical)


def build_run_tables(
    run_dir: Path,
    labels_dir: Path,
    qids: Sequence[str],
    rules: Mapping[str, tuple[str, bool]],
) -> RunTables:
    """共通の質問 qids について，候補の 3 分類の件数と，規則（名前 → （select_eval の規則，UNKNOWN を無視するか））ごとの選んだ回答を数える．"""
    rows = {r["qid"]: r for r in read_results(run_dir / "results.jsonl")}
    judged = read_candidate_judgements(run_dir / "candidate_judgements.jsonl")
    labels = read_labels(labels_dir / run_dir.name / CANDIDATE_ABSTAIN)
    ce_path = run_dir / "ce_features.jsonl"
    ce = read_ce_features(ce_path)
    subset = [rows[q] for q in qids]
    owner = np.zeros((len(qids), len(COLUMNS)))
    other = np.zeros((len(qids), len(COLUMNS)))
    for n, row in enumerate(subset):
        for index, cand in enumerate((row.get("owner_answer") or {}).get("candidates") or []):
            target = owner if cand["source"] == row["bank"] else other
            target[n] += _candidate_vector(row["qid"], index, judged, labels)
    chosen: dict[str, F64Array] = {}
    segment: dict[str, list[int]] = {}
    chosen_owner: dict[str, list[bool]] = {}
    for name, (rule, ignore_unknown) in rules.items():
        outcomes = apply_rule(subset, judged, rule, ce, ignore_unknown)
        table = np.zeros((len(qids), len(COLUMNS)))
        for n, o in enumerate(outcomes):
            if o.chosen_index is None:
                table[n] = _vector(LABEL_UNJUDGED, False)
            else:
                table[n] = _candidate_vector(o.qid, o.chosen_index, judged, labels)
        chosen[name] = table
        segment[name] = [0 if not o.owner_in else (1 if o.chosen_owner else 2) for o in outcomes]
        chosen_owner[name] = [o.chosen_owner for o in outcomes]
    return RunTables(owner, other, chosen, segment, chosen_owner)


def build_central_table(run_dir: Path, labels_dir: Path, qids: Sequence[str]) -> F64Array:
    """中央の実行（候補を持たない）の最終回答の one-hot（結果に無い質問・失敗した質問は判定不能）．"""
    rows = {r["qid"]: r for r in read_results(run_dir / "results.jsonl")}
    judged = {
        j["qid"]: j["correct"]
        for j in map(json.loads, (run_dir / "judgements.jsonl").read_text("utf-8").splitlines())
    }
    labels = read_final_labels(labels_dir / run_dir.name / ABSTAIN)
    table = np.zeros((len(qids), len(COLUMNS)))
    for n, qid in enumerate(qids):
        if qid not in rows or rows[qid].get("error") is not None or qid not in judged:
            table[n] = _vector(LABEL_UNJUDGED, False)
        else:
            label, lexical = classify(judged[qid], labels.get(qid))
            table[n] = _vector(label, lexical)
    return table


def column_stats(table: F64Array) -> dict[str, Stat]:
    """one-hot（または件数）の表 → 指標の（分子，分母）．valid は判定不能を除いた件数．

    F は誤答の割合の下限（棄権の判定不能は入れない），F_upper は上限（入れる），F_lexmax は字句だけの最大値
    （UNKNOWN の字句以外の不正解を全て誤答とする）．
    """
    c = {name: table[:, i] for name, i in _COL.items()}
    valid = table.sum(axis=1) - c["unjudged"]
    fab_lexmax = c["dk_model"] + c["fabricated"] + c["unparsed"]
    return {
        "F": Stat(c["fabricated"], valid),
        "F_upper": Stat(c["fabricated"] + c["unparsed"], valid),
        "F_lexmax": Stat(fab_lexmax, valid),
        "dont_know": Stat(c["dk_lexical"] + c["dk_model"], valid),
        "correct": Stat(c["correct"], valid),
        "unjudged": Stat(c["unjudged"], valid + c["unjudged"]),
        "abstain_unparsed": Stat(c["unparsed"], valid + c["unjudged"]),
    }


def random_choice_stat(tables: Sequence[F64Array]) -> Stat:
    """F_rand：各問で候補を一様に選んだときの誤答の期待値（問ごとの候補の誤答の割合．判定不能の候補は除く）の平均．"""
    total: F64Array = np.sum(np.stack(list(tables)), axis=0)
    valid = total.sum(axis=1) - total[:, _COL["unjudged"]]
    has = valid > 0
    num = np.where(has, total[:, _COL["fabricated"]] / np.where(has, valid, 1.0), 0.0)
    return Stat(num, has.astype(np.float64))


def bootstrap_interval(
    terms: Sequence[tuple[float, Stat]],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """Σ 符号 × 割合 の 95% 区間（質問を単位にした対応ありのブートストラップ．同じ質問の再抽出を全ての項で使う）．"""
    n = len(terms[0][1].num)
    rng = np.random.default_rng(seed)
    parts: list[F64Array] = []
    for start in range(0, samples, BOOTSTRAP_CHUNK):
        idx = rng.integers(0, n, size=(min(BOOTSTRAP_CHUNK, samples - start), n))
        total = np.zeros(idx.shape[0])
        for sign, stat in terms:
            den = stat.den[idx].sum(axis=1)
            ratio = np.divide(
                stat.num[idx].sum(axis=1), den, out=np.full(den.shape, np.nan), where=den > 0
            )
            total += sign * ratio
        parts.append(total)
    values = np.concatenate(parts)
    return (
        float(np.nanpercentile(values, 100 * ALPHA / 2)),
        float(np.nanpercentile(values, 100 * (1 - ALPHA / 2))),
    )


def _with_interval(stat: Stat) -> dict[str, Any]:
    """割合・分母・95% 区間（ブートストラップ）．"""
    lo, hi = bootstrap_interval([(1.0, stat)])
    return {"value": stat.value(), "ci95": [lo, hi], "n": float(stat.den.sum())}


def _summarize(table: F64Array) -> dict[str, Any]:
    """1 つの表（候補または選んだ回答）の指標．F は区間付き，それ以外は値だけ．"""
    stats = column_stats(table)
    out: dict[str, Any] = {name: stat.value() for name, stat in stats.items()}
    out["F"] = _with_interval(stats["F"])
    out["counts"] = {name: float(table[:, i].sum()) for name, i in _COL.items()}
    return out


def _segment_table(table: F64Array, segment: Sequence[int]) -> dict[str, Any]:
    """選んだ回答を区分 (a) 持ち主が候補に入らない・(b) 入り持ち主を採った・(c) 入ったが別を採った，ごとに誤答の割合を出す．"""
    seg = np.asarray(segment)
    out: dict[str, Any] = {}
    for code, name in enumerate(("a_owner_out", "b_owner_chosen", "c_owner_in_not_chosen")):
        part = table[seg == code]
        valid = int(part.sum() - part[:, _COL["unjudged"]].sum())
        fab = int(part[:, _COL["fabricated"]].sum())
        lo, hi = wilson_interval(fab, valid)
        out[name] = {
            "n_questions": len(part),
            "n_valid": valid,
            "fabricated": fab,
            "F": fab / valid if valid else None,
            "wilson95": [lo, hi],
        }
    return out


def _chosen_owner_split(table: F64Array, chosen_owner: Sequence[bool]) -> dict[str, Any]:
    """選んだ回答の誤答を，持ち主の候補から来たか・持ち主以外の候補から来たかに分ける．"""
    flag = np.asarray(chosen_owner)
    return {
        name: {
            "n_valid": int(part.sum() - part[:, _COL["unjudged"]].sum()),
            "fabricated": int(part[:, _COL["fabricated"]].sum()),
        }
        for name, part in (("from_owner", table[flag]), ("from_non_owner", table[~flag]))
    }


def judge_hypotheses(a5: RunTables, a6: RunTables, rule_a5: str, rule_a6: str) -> dict[str, Any]:
    """H-F1〜H-F3（journal Iteration 21「判定の規則」）の区間と判定．"""
    non5, non6 = column_stats(a5.other)["F"], column_stats(a6.other)["F"]
    lo1, hi1 = bootstrap_interval([(1.0, non6), (-1.0, non5)])
    fin5, fin6 = column_stats(a5.chosen[rule_a5])["F"], column_stats(a6.chosen[rule_a6])["F"]
    lo2, hi2 = bootstrap_interval([(1.0, fin6), (-1.0, fin5)])
    rand6 = random_choice_stat([a6.owner, a6.other])
    lo3, hi3 = bootstrap_interval([(1.0, fin6), (-1.0, rand6)])
    if lo2 > 0:
        h2 = "increased"
    elif lo2 >= -JITTER_MARGIN and hi2 <= JITTER_MARGIN:
        h2 = "no_change_beyond_jitter"
    else:
        h2 = "undetermined"
    only_a6 = only_a5 = 0
    f5 = a5.chosen[rule_a5][:, _COL["fabricated"]]
    f6 = a6.chosen[rule_a6][:, _COL["fabricated"]]
    both = (a5.chosen[rule_a5][:, _COL["unjudged"]] == 0) & (
        a6.chosen[rule_a6][:, _COL["unjudged"]] == 0
    )
    only_a6 = int(((f6 == 1) & (f5 == 0) & both).sum())
    only_a5 = int(((f5 == 1) & (f6 == 0) & both).sum())
    return {
        "H-F1": {
            "statement": "F_non(A6) − F_non(A5) の 95% 区間の下端 > 0",
            "difference": non6.value() - non5.value(),
            "ci95": [lo1, hi1],
            "holds": bool(lo1 > 0),
        },
        "H-F2": {
            "statement": f"Δ = F_final(A6 {rule_a6}) − F_final(A5 {rule_a5})．下端 > 0 なら増えた，"
            f"区間が [−{JITTER_MARGIN}, +{JITTER_MARGIN}] に収まれば揺れの幅を超えては変わらない，それ以外は決まらない",
            "difference": fin6.value() - fin5.value(),
            "ci95": [lo2, hi2],
            "result": h2,
            "mcnemar": {
                "only_a6_fabricated": only_a6,
                "only_a5_fabricated": only_a5,
                "p": mcnemar_exact(only_a6, only_a5),
            },
        },
        "H-F3": {
            "statement": f"F_final(A6 {rule_a6}) − F_rand(A6) の 95% 区間の上端 < 0",
            "difference": fin6.value() - rand6.value(),
            "ci95": [lo3, hi3],
            "holds": bool(hi3 < 0),
        },
    }


# 主の規則（名前 → （select_eval の規則，UNKNOWN で除外しない））．A5 は UNKNOWN を許すプロンプトで，R3 は UNKNOWN を除外する
RULES_A5: dict[str, tuple[str, bool]] = {"R3": ("R3", False), "R0": ("R0", False)}
RULES_A6: dict[str, tuple[str, bool]] = {"R3p": ("R3", True), "R0p": ("R0", True)}
MAIN_A5, MAIN_A6 = "R3", "R3p"


def common_questions(a5_dir: Path, a6_dir: Path) -> list[str]:
    """A5・A6 の両方で成功した質問（A5 の結果の並び．質問ごとに最後の行を使う）．"""
    ok6 = {r["qid"] for r in read_results(a6_dir / "results.jsonl") if r.get("error") is None}
    return [
        r["qid"]
        for r in read_results(a5_dir / "results.jsonl")
        if r.get("error") is None and r["qid"] in ok6
    ]


def evaluate_fabrication(
    a5_dir: Path,
    a6_dir: Path,
    central_dir: Path | None,
    labels_dir: Path,
    gate: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """（fabrication.json の中身，verdict.json の verdict_original）．gate が無い・通過していないときは判定を保留する．"""
    qids = common_questions(a5_dir, a6_dir)
    a5 = build_run_tables(a5_dir, labels_dir, qids, RULES_A5)
    a6 = build_run_tables(a6_dir, labels_dir, qids, RULES_A6)
    report: dict[str, Any] = {
        "n_common_questions": len(qids),
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED, "unit": "question"},
        "abstain_prompt_sha256": abstain_prompt_sha256(),
        "runs": {},
    }
    for name, run, tables, chosen_rules in (
        ("A5", a5_dir, a5, RULES_A5),
        ("A6", a6_dir, a6, RULES_A6),
    ):
        both = tables.owner + tables.other
        report["runs"][name] = {
            "run": run.name,
            "candidates_non_owner": _summarize(tables.other),
            "candidates_owner": _summarize(tables.owner),
            "candidates_all": _summarize(both),
            "F_rand": _with_interval(random_choice_stat([tables.owner, tables.other])),
            "selected": {
                rule: {
                    **_summarize(tables.chosen[rule]),
                    "segments": _segment_table(tables.chosen[rule], tables.segment[rule]),
                    "by_chosen_source": _chosen_owner_split(
                        tables.chosen[rule], tables.chosen_owner[rule]
                    ),
                }
                for rule in chosen_rules
            },
        }
    if central_dir is not None:
        report["runs"]["C1_RRF_CE"] = {
            "run": central_dir.name,
            "selected": {"final": _summarize(build_central_table(central_dir, labels_dir, qids))},
        }
    hypotheses = judge_hypotheses(a5, a6, MAIN_A5, MAIN_A6)
    gate_passed = bool(gate and gate.get("passed"))
    verdict: dict[str, Any] = {
        "gate_G-A2": gate,
        "status": "judged" if gate_passed else "withheld",
        "hypotheses": hypotheses if gate_passed else None,
        "range_only": None
        if gate_passed
        else {
            name: {
                "F_non": [
                    r["candidates_non_owner"]["F"]["value"],
                    r["candidates_non_owner"]["F_upper"],
                ],
                "F_final": [
                    r["selected"][MAIN_A5 if name == "A5" else MAIN_A6]["F"]["value"],
                    r["selected"][MAIN_A5 if name == "A5" else MAIN_A6]["F_upper"],
                ],
            }
            for name, r in report["runs"].items()
            if name in ("A5", "A6")
        },
    }
    report["hypotheses_if_gate_passed"] = hypotheses
    return report, verdict


def direction_agreement(dev: Mapping[str, Any], test_verdict: Mapping[str, Any]) -> dict[str, Any]:
    """dev の H-F1〜H-F3 の差の向き（符号）が，test の verdict.json と同じか．test の判定が保留なら比べない．"""
    hypotheses = test_verdict.get("hypotheses")
    if hypotheses is None:
        return {"compared": False, "reason": "test verdict is withheld"}
    return {
        "compared": True,
        **{
            name: bool(np.sign(dev[name]["difference"]) == np.sign(hypotheses[name]["difference"]))
            for name in ("H-F1", "H-F2", "H-F3")
        },
    }


# ── 人手での確かめ（ゲート G-A2） ────────────────────────────────────────────

MANUAL_SEED = 12
MANUAL_PER_STRATUM = 25
GATE_MIN_AGREEMENT = 0.90
GATE_MIN_KAPPA = 0.70


def build_manual_table(
    runs: Mapping[str, Path],
    labels_dir: Path,
    qids: Sequence[str],
    questions: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """test から層化抽出した表（分類を隠した table と，答え合わせの key）．

    層は 実行 × 持ち主・持ち主以外 × 判定モデルの分類（わからない・誤答）の 8 層で，各 25 件（足りなければ全て）．
    判定モデルが分類した件（UNKNOWN の字句で「わからない」にした件は判定モデルが分類していないので除く）から抜く．順序は混ぜる．
    """
    pool: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for run_name, run_dir in runs.items():
        rows = {r["qid"]: r for r in read_results(run_dir / "results.jsonl")}
        labels = read_labels(labels_dir / run_dir.name / CANDIDATE_ABSTAIN)
        judged = read_candidate_judgements(run_dir / "candidate_judgements.jsonl")
        for qid in sorted(qids):
            row = rows[qid]
            for index, cand in enumerate((row.get("owner_answer") or {}).get("candidates") or []):
                label = labels[(qid, index)]
                if judged.get((qid, index)) is not False or label.status != STATUS_MODEL:
                    continue
                if label.abstain is None:
                    continue
                group = "owner" if cand["source"] == row["bank"] else "non_owner"
                klass = LABEL_DONT_KNOW if label.abstain else LABEL_FABRICATED
                pool.setdefault((run_name, group, klass), []).append(
                    {
                        "run": run_name,
                        "qid": qid,
                        "index": index,
                        "group": group,
                        "model_label": klass,
                        "answer": str(cand["answer"]),
                    }
                )
    rng = np.random.default_rng(MANUAL_SEED)
    picked: list[dict[str, Any]] = []
    for stratum in sorted(pool):
        items = pool[stratum]
        take = rng.permutation(len(items))[:MANUAL_PER_STRATUM]
        picked.extend(items[int(i)] for i in take)
    order = rng.permutation(len(picked))
    table: list[dict[str, Any]] = []
    key: list[dict[str, Any]] = []
    for new_id, i in enumerate(order):
        item = picked[int(i)]
        ident = f"m{new_id:03d}"
        table.append({"id": ident, "question": questions[item["qid"]], "answer": item["answer"]})
        key.append({"id": ident, **{k: v for k, v in item.items() if k != "answer"}})
    return table, key


def gate_report(
    key: Sequence[Mapping[str, Any]],
    manual: Mapping[str, bool],
    reference: Mapping[str, bool | None] | None = None,
) -> dict[str, Any]:
    """人手の分類（id → 棄権なら True）と判定モデルの分類の一致率・κ．ゲートは一致率 ≥ 0.90 かつ κ ≥ 0.70．

    reference（qwen3:14b の分類．id → 棄権か）があれば，人手との κ を併記する（ゲートには使わない）．
    """
    ids = [k["id"] for k in key if k["id"] in manual]
    model = [k["model_label"] == LABEL_DONT_KNOW for k in key if k["id"] in manual]
    hand = [manual[i] for i in ids]
    agreement = sum(a == b for a, b in zip(model, hand, strict=True)) / len(ids) if ids else 0.0
    kappa = cohen_kappa(model, hand)
    by_stratum: dict[str, Any] = {}
    for k in key:
        if k["id"] in manual:
            s = by_stratum.setdefault(
                f"{k['run']}/{k['group']}/{k['model_label']}", {"n": 0, "agree": 0}
            )
            s["n"] += 1
            s["agree"] += (k["model_label"] == LABEL_DONT_KNOW) == manual[k["id"]]
    report: dict[str, Any] = {
        "n": len(ids),
        "agreement": agreement,
        "kappa": kappa,
        "min_agreement": GATE_MIN_AGREEMENT,
        "min_kappa": GATE_MIN_KAPPA,
        "passed": bool(agreement >= GATE_MIN_AGREEMENT and kappa >= GATE_MIN_KAPPA),
        "by_stratum": by_stratum,
        "manual_abstain_share": sum(hand) / len(hand) if hand else None,
    }
    if reference is not None:
        shared = [i for i in ids if reference.get(i) is not None]
        ref_values = [bool(reference[i]) for i in shared]
        hand_values = [manual[i] for i in shared]
        model_values = [
            next(k["model_label"] == LABEL_DONT_KNOW for k in key if k["id"] == i) for i in shared
        ]
        report["reference_qwen3_14b"] = {
            "n": len(shared),
            "kappa_vs_manual": cohen_kappa(ref_values, hand_values),
            "kappa_vs_qwen3_8b": cohen_kappa(ref_values, model_values),
            "note": "ゲートには使わない（参考）",
        }
    return report
