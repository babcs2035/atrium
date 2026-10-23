"""owner_answer の候補の選び方の規則を，保存した全候補でオフラインに適用・評価する（p0007 A5）．

入力は `answer.save_candidates: true` で流した実行（results.jsonl の `owner_answer.candidates`）と，`atrium judge --with-candidates`
が書いた候補ごとの判定（candidate_judgements.jsonl）．cross-encoder の得点は `atrium select-features` が後から求めて
ce_features.jsonl に書く（質問と，候補の文脈の上位 5 通の関連度．メールの本文は書かない）．

規則は全て abstain と同じ形で，UNKNOWN でない候補のうち特徴量が最高のものを採る（全て UNKNOWN なら全候補の中で最高）．
同点は候補の並びの先の方（`atrium.merge.choose_abstain` と同じ）．R0 は実機の `abstain` そのもの（密ベクトルの最高得点）．
`--ignore-unknown`（p0007 A6）は UNKNOWN による除外をせず，全候補の中で特徴量が最高のものを採る（R0′〜R6′．通常のプロンプト
（`aggregate: max_score`）で答えさせた候補に使う．R0′ は実機の `max_score` そのもの）．

| 規則 | 特徴量 |
|---|---|
| R0 | 密ベクトルの最高得点（top_score．現行） |
| R1 | ルーターの融合得点 z(dense) + 2·z(bm25)（bm25_probe の probe の得点） |
| R5 | 受信箱の中の BM25 の最大（その受信箱だけの統計） |
| R6 | 受信箱の中の RRF の得点の最大 |
| R2 | 生成の対数確率の平均（AvgLogP） |
| R3 | cross-encoder（bge-reranker-v2-m3）の，質問と文脈の上位 5 通の関連度の最大 |
| R4 | R0〜R3 の 4 つの特徴量の順位の和（考える候補の中の平均順位．小さいほど良い．学習・調整なし） |

`RULE_ORDER` は「単純な順」で，dev の正答率が同点のときに先の規則を選ぶ．

使い方:
    uv run atrium select-features --run-dir results/<run> --data-dir <データの置き場所>
    uv run atrium select-eval --run-dir results/<run> --out-dir results/<out>
    uv run atrium select-eval --run-dir results/<run> --out-dir results/<out> --apply R1 --derived-run results/<run>_R1
    uv run atrium select-eval --run-dir results/<run> --out-dir results/<out> --ignore-unknown
`select-features` は，実機が cross-encoder で選んだ実行（`answer.aggregate: cross_encoder`．p0007 F-6）では，実機の選択とオフラインの R3′ の一致も
ce_live_agreement.json に書く（同点を許す一致を含む）．
`--apply` は選んだ 1 つの規則の正誤を results.jsonl・judgements.jsonl の形で書き（回答の文は書かない），`atrium compare` に渡せるようにする．
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atrium.analysis import read_results, wilson_interval

CANDIDATE_JUDGEMENTS = "candidate_judgements.jsonl"
CE_FEATURES = "ce_features.jsonl"
SELECT_EVAL = "select_eval.json"
CE_LIVE_AGREEMENT = "ce_live_agreement.json"

# 規則 → 特徴量（R4 は順位の和で，ここに無い）
SINGLE_FEATURE = {
    "R0": "top_score",
    "R1": "probe_fused",
    "R5": "bm25_max",
    "R6": "rrf_max",
    "R2": "avg_logprob",
    "R3": "ce_max",
}
RANK_SUM_FEATURES = ("top_score", "probe_fused", "avg_logprob", "ce_max")
# 単純な順（dev の正答率が同点なら先の規則を選ぶ）．追加の計算が要らない順に並べた
RULE_ORDER = ("R0", "R1", "R5", "R6", "R2", "R3", "R4")


@dataclass(frozen=True)
class Candidate:
    """1 つの候補（選び方に要る値だけ）．特徴量が無いときは None（その特徴量では最も低いとみなす）．"""

    source: str
    unknown: bool
    features: Mapping[str, float | None]


def _value(c: Candidate, feature: str) -> float:
    v = c.features.get(feature)
    return float(v) if v is not None and math.isfinite(v) else float("-inf")


def considered(
    candidates: Sequence[Candidate], ignore_unknown: bool = False
) -> tuple[list[int], bool]:
    """（考える候補の番号，全て UNKNOWN だったか）．UNKNOWN でない候補だけを考え，全て UNKNOWN なら全候補を考える．

    ignore_unknown のときは UNKNOWN かどうかを見ず，常に全候補を考える（2 つ目の値は False）．
    """
    if ignore_unknown:
        return list(range(len(candidates))), False
    answered = [i for i, c in enumerate(candidates) if not c.unknown]
    if answered:
        return answered, False
    return list(range(len(candidates))), True


def choose_by_feature(
    candidates: Sequence[Candidate], feature: str, ignore_unknown: bool = False
) -> int:
    """考える候補のうち feature が最高の候補の番号（同点は先の方）．"""
    if not candidates:
        raise ValueError("no candidates to choose from")
    pool, _ = considered(candidates, ignore_unknown)
    return max(pool, key=lambda i: (_value(candidates[i], feature), -i))


def average_ranks(values: Sequence[float]) -> list[float]:
    """大きいほど良い値の順位（1 が最良．同点は平均の順位）．"""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0.0] * len(values)
    pos = 0
    while pos < len(order):
        end = pos
        while end + 1 < len(order) and values[order[end + 1]] == values[order[pos]]:
            end += 1
        rank = (pos + end) / 2 + 1
        for j in range(pos, end + 1):
            ranks[order[j]] = rank
        pos = end + 1
    return ranks


def choose_by_rank_sum(
    candidates: Sequence[Candidate], features: Sequence[str], ignore_unknown: bool = False
) -> int:
    """考える候補の中で，各特徴量の順位（平均の順位）の和が最小の候補の番号（同点は先の方）．"""
    if not candidates:
        raise ValueError("no candidates to choose from")
    pool, _ = considered(candidates, ignore_unknown)
    total = [0.0] * len(pool)
    for feature in features:
        for j, r in enumerate(average_ranks([_value(candidates[i], feature) for i in pool])):
            total[j] += r
    best = min(range(len(pool)), key=lambda j: (total[j], pool[j]))
    return pool[best]


def rule_chooser(rule: str, ignore_unknown: bool = False) -> Callable[[Sequence[Candidate]], int]:
    """規則の名前 → 候補の列から採る番号を返す関数（ignore_unknown なら UNKNOWN で除外しない R0′〜R6′）．"""
    if rule == "R4":
        return lambda cands: choose_by_rank_sum(cands, RANK_SUM_FEATURES, ignore_unknown)
    if rule in SINGLE_FEATURE:
        feature = SINGLE_FEATURE[rule]
        return lambda cands: choose_by_feature(cands, feature, ignore_unknown)
    raise ValueError(f"unknown rule {rule!r}; choose from {RULE_ORDER}")


def read_candidate_judgements(path: Path) -> dict[tuple[str, int], bool | None]:
    """（質問 ID，候補の番号）→ 正誤（判定できなかった候補は None）．"""
    with path.open(encoding="utf-8") as f:
        return {(j["qid"], int(j["index"])): j["correct"] for j in map(json.loads, f) if j}


def read_ce_features(path: Path) -> dict[tuple[str, int], float]:
    """（質問 ID，候補の番号）→ cross-encoder の得点の最大（文脈の無い候補は含めない）．"""
    with path.open(encoding="utf-8") as f:
        return {
            (j["qid"], int(j["index"])): float(j["ce_max"])
            for j in map(json.loads, f)
            if j and j["ce_max"] is not None
        }


def row_candidates(
    row: Mapping[str, Any], ce: Mapping[tuple[str, int], float] | None = None
) -> list[Candidate]:
    """結果の 1 行の候補（cross-encoder の得点があれば ce_max として足す）．"""
    owner = row.get("owner_answer") or {}
    out = []
    for index, c in enumerate(owner.get("candidates") or []):
        features: dict[str, float | None] = {
            name: c.get(name)
            for name in ("top_score", "probe_fused", "bm25_max", "rrf_max", "avg_logprob")
        }
        features["ce_max"] = None if ce is None else ce.get((row["qid"], index))
        out.append(Candidate(str(c["source"]), bool(c["unknown"]), features))
    return out


@dataclass(frozen=True)
class QuestionOutcome:
    """1 問の，ある規則での結果．"""

    qid: str
    correct: bool
    chosen_index: int | None
    # 持ち主の受信箱が候補に入ったか・採った候補が持ち主か（候補が無い問は False）
    owner_in: bool
    chosen_owner: bool


def apply_rule(
    rows: Sequence[Mapping[str, Any]],
    judged: Mapping[tuple[str, int], bool | None],
    rule: str,
    ce: Mapping[tuple[str, int], float] | None = None,
    ignore_unknown: bool = False,
) -> list[QuestionOutcome]:
    """全問に規則を当てはめる．失敗した問・候補の無い問・判定できなかった候補は不正解とする．"""
    choose = rule_chooser(rule, ignore_unknown)
    out = []
    for row in rows:
        cands = row_candidates(row, ce) if row.get("error") is None else []
        owner = str(row["bank"])
        owner_in = any(c.source == owner for c in cands)
        if not cands:
            out.append(QuestionOutcome(row["qid"], False, None, owner_in, False))
            continue
        index = choose(cands)
        correct = judged.get((row["qid"], index)) is True
        out.append(
            QuestionOutcome(row["qid"], correct, index, owner_in, cands[index].source == owner)
        )
    return out


def _accuracy(outcomes: Sequence[QuestionOutcome]) -> dict[str, Any]:
    n = len(outcomes)
    k = sum(o.correct for o in outcomes)
    return {"n": n, "correct": k, "accuracy": k / n if n else 0.0}


def segment_table(outcomes: Sequence[QuestionOutcome]) -> dict[str, Any]:
    """(a) 持ち主が候補に入らない・(b) 入り持ち主の回答を採った・(c) 入ったが別の受信箱の回答を採った，の割合と正答率．"""
    n = len(outcomes)
    segments = {
        "a_owner_out": [o for o in outcomes if not o.owner_in],
        "b_owner_chosen": [o for o in outcomes if o.owner_in and o.chosen_owner],
        "c_owner_in_not_chosen": [o for o in outcomes if o.owner_in and not o.chosen_owner],
    }
    return {
        name: {**_accuracy(items), "share": len(items) / n if n else 0.0}
        for name, items in segments.items()
    }


def candidate_ceilings(
    rows: Sequence[Mapping[str, Any]],
    judged: Mapping[tuple[str, int], bool | None],
    reference: Sequence[QuestionOutcome],
) -> dict[str, Any]:
    """候補の正誤から分かる上限（記述）：

    - any_candidate_correct：候補のどれかが正解の問の割合（選び方が完全なときの上限）
    - owner_candidate_given_owner_in：持ち主が候補に入った問での，持ち主の候補の正答率
    - owner_candidate_in_segment_c：reference の規則で (c) になった問での，持ち主の候補の正答率（(c) の天井の推定）
    """
    owner_correct: dict[str, bool] = {}
    any_correct: dict[str, bool] = {}
    for row in rows:
        owner = str(row["bank"])
        cands = (row.get("owner_answer") or {}).get("candidates") or []
        any_correct[row["qid"]] = any(
            judged.get((row["qid"], i)) is True for i in range(len(cands))
        )
        for i, c in enumerate(cands):
            if c["source"] == owner:
                owner_correct[row["qid"]] = judged.get((row["qid"], i)) is True
    n = len(rows)
    owner_in = [q for q in owner_correct]
    seg_c = [o.qid for o in reference if o.owner_in and not o.chosen_owner]
    return {
        "any_candidate_correct": sum(any_correct.values()) / n if n else 0.0,
        "owner_candidate_given_owner_in": {
            "n": len(owner_in),
            "accuracy": sum(owner_correct[q] for q in owner_in) / max(1, len(owner_in)),
        },
        "owner_candidate_in_segment_c": {
            "n": len(seg_c),
            "accuracy": sum(owner_correct[q] for q in seg_c) / max(1, len(seg_c)),
        },
    }


def select_rule(accuracy_of: Mapping[str, int]) -> str:
    """正解数が最大の規則（同点は RULE_ORDER の先の方）．"""
    rules = [r for r in RULE_ORDER if r in accuracy_of]
    return max(rules, key=lambda r: (accuracy_of[r], -RULE_ORDER.index(r)))


def evaluate(
    run_dir: Path,
    rules: Sequence[str],
    ce_path: Path | None = None,
    ignore_unknown: bool = False,
) -> tuple[dict[str, Any], dict[str, list[QuestionOutcome]]]:
    """規則ごとの正答率・区分・上限と，選んだ規則を返す（cross-encoder の得点が無ければ R3・R4 は評価しない）．

    ignore_unknown のときは UNKNOWN で除外しない R0′〜R6′ を評価する（report の ignore_unknown に残す）．
    """
    rows = read_results(run_dir / "results.jsonl")
    judged = read_candidate_judgements(run_dir / CANDIDATE_JUDGEMENTS)
    ce = read_ce_features(ce_path) if ce_path is not None and ce_path.exists() else None
    usable = [r for r in rules if ce is not None or r not in ("R3", "R4")]
    outcomes = {rule: apply_rule(rows, judged, rule, ce, ignore_unknown) for rule in usable}
    per_rule: dict[str, Any] = {}
    for rule, items in outcomes.items():
        acc = _accuracy(items)
        lo, hi = wilson_interval(acc["correct"], acc["n"])
        per_rule[rule] = {
            **acc,
            "wilson95": [lo, hi],
            "chosen_is_owner": sum(o.chosen_owner for o in items) / max(1, len(items)),
            "segments": segment_table(items),
        }
    report: dict[str, Any] = {
        "run": run_dir.name,
        "n_questions": len(rows),
        "n_failed": sum(r.get("error") is not None for r in rows),
        "ignore_unknown": ignore_unknown,
        "rules_evaluated": usable,
        "rules_skipped_no_ce": [r for r in rules if r not in usable],
        "per_rule": per_rule,
        "selected_rule": select_rule({r: v["correct"] for r, v in per_rule.items()}),
        "rule_order": list(RULE_ORDER),
    }
    if "R0" in outcomes:
        report["ceilings"] = candidate_ceilings(rows, judged, outcomes["R0"])
        report["r0_matches_live_choice"] = _live_agreement(rows, outcomes["R0"])
        report["r0_vs_main_judgement"] = _main_judgement_agreement(run_dir, outcomes["R0"])
    report["unknown_share_by_owner"] = _unknown_shares(rows)
    return report, outcomes


def _live_agreement(rows: Sequence[Mapping[str, Any]], r0: Sequence[QuestionOutcome]) -> float:
    """R0 の選択が，実機の統合（abstain．R0′ なら max_score）が採った候補（owner_answer.chosen_index）と一致する割合．"""
    live = {r["qid"]: (r.get("owner_answer") or {}).get("chosen_index") for r in rows}
    pairs = [(o.chosen_index, live.get(o.qid)) for o in r0 if o.chosen_index is not None]
    return sum(a == b for a, b in pairs) / max(1, len(pairs))


def _main_judgement_agreement(run_dir: Path, r0: Sequence[QuestionOutcome]) -> dict[str, Any]:
    """候補ごとの判定から作った R0 の正誤と，採った回答の判定（judgements.jsonl）の一致．"""
    path = run_dir / "judgements.jsonl"
    if not path.exists():
        return {"available": False}
    with path.open(encoding="utf-8") as f:
        main = {j["qid"]: j["correct"] is True for j in map(json.loads, f) if j}
    shared = [o for o in r0 if o.qid in main]
    return {
        "available": True,
        "n": len(shared),
        "agreement": sum(o.correct == main[o.qid] for o in shared) / max(1, len(shared)),
        "main_accuracy": sum(main.values()) / max(1, len(r0)),
    }


def _unknown_shares(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """持ち主の候補・持ち主以外の候補が UNKNOWN と答えた割合．"""
    owner = other = owner_u = other_u = 0
    for row in rows:
        for c in (row.get("owner_answer") or {}).get("candidates") or []:
            if c["source"] == row["bank"]:
                owner += 1
                owner_u += bool(c["unknown"])
            else:
                other += 1
                other_u += bool(c["unknown"])
    return {
        "owner_unknown": owner_u / max(1, owner),
        "non_owner_unknown": other_u / max(1, other),
    }


def write_derived_run(
    run_dir: Path,
    outcomes: Sequence[QuestionOutcome],
    rule: str,
    out_dir: Path,
    ignore_unknown: bool = False,
) -> None:
    """選んだ規則の正誤を results.jsonl・judgements.jsonl の形で書く（`atrium compare` の入力．回答の文は書かない）．"""
    rows = {r["qid"]: r for r in read_results(run_dir / "results.jsonl")}
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for o in outcomes:
            row = rows[o.qid]
            cands = (row.get("owner_answer") or {}).get("candidates") or []
            f.write(
                json.dumps(
                    {
                        "qid": o.qid,
                        "bank": row["bank"],
                        "error": row.get("error"),
                        "select_rule": rule,
                        "chosen_index": o.chosen_index,
                        "chosen_source": None
                        if o.chosen_index is None
                        else cands[o.chosen_index]["source"],
                    }
                )
                + "\n"
            )
    with (out_dir / "judgements.jsonl").open("w", encoding="utf-8") as f:
        for o in outcomes:
            if rows[o.qid].get("error") is None:
                f.write(
                    json.dumps({"qid": o.qid, "correct": o.correct, "answer_overlap_lcs": None})
                    + "\n"
                )
    (out_dir / "derived_from.json").write_text(
        json.dumps(
            {"run": str(run_dir), "select_rule": rule, "ignore_unknown": ignore_unknown},
            indent=1,
        ),
        encoding="utf-8",
    )


# 実機（aggregate=cross_encoder）とオフラインの cross-encoder の得点が，同点とみなせる差
CE_TIE_TOLERANCE = 1e-4


def live_cross_encoder_agreement(
    rows: Sequence[Mapping[str, Any]], ce: Mapping[tuple[str, int], float]
) -> dict[str, Any]:
    """実機の cross-encoder による選択（aggregate=cross_encoder．owner_answer.chosen_index）と，オフラインの R3′ の一致（p0007 F-6）．

    オフラインの得点 ce（`cross_encoder_features`．`ce_features.jsonl`）から R3′ の選択を作り，実機の選択と比べる．
    - strict：選んだ候補の番号が同じ問の割合
    - tie_tolerant：番号が違っても，実機が選んだ候補のオフラインの得点が最高の得点と CE_TIE_TOLERANCE 以内の問（同点）も一致とする割合
    - max_abs_score_diff：実機とオフラインの候補ごとの得点の差の最大（両方に得点がある候補）
    実機の得点（candidates[].ce_max）が無い問は数えない．
    """
    n = strict = tied = 0
    max_diff = 0.0
    for row in rows:
        owner = row.get("owner_answer") or {}
        raw = owner.get("candidates") or []
        live_index = owner.get("chosen_index")
        if row.get("error") is not None or not raw or live_index is None:
            continue
        if not any("ce_max" in c for c in raw):
            continue
        offline = row_candidates(row, ce)
        best = choose_by_feature(offline, "ce_max", ignore_unknown=True)
        n += 1
        strict += live_index == best
        live_value, best_value = (
            _value(offline[live_index], "ce_max"),
            _value(offline[best], "ce_max"),
        )
        tied += live_index == best or abs(live_value - best_value) <= CE_TIE_TOLERANCE
        for index, c in enumerate(raw):
            have_both = c.get("ce_max") is not None and (row["qid"], index) in ce
            if have_both:
                max_diff = max(max_diff, abs(float(c["ce_max"]) - ce[(row["qid"], index)]))
    return {
        "n": n,
        "strict": strict / n if n else 0.0,
        "tie_tolerant": tied / n if n else 0.0,
        "n_disagree_strict": n - strict,
        "n_disagree_tie_tolerant": n - tied,
        "max_abs_score_diff": max_diff,
        "tie_tolerance": CE_TIE_TOLERANCE,
    }


def cross_encoder_features(
    rows: Sequence[Mapping[str, Any]],
    question_of: Mapping[str, str],
    text_of: Mapping[str, str],
    score: Callable[[str, Sequence[str]], list[float]],
) -> list[dict[str, Any]]:
    """候補ごとの cross-encoder の得点（質問と，文脈の上位 5 通それぞれの関連度の最大と，内積が最高の 1 通の関連度）．

    score は（質問，文の列）→ 関連度の列（`CrossEncoderReranker.scores`）．文は `<題>\\n<本文>`（再ランクと同じ）．
    """
    out = []
    for row in rows:
        for index, c in enumerate((row.get("owner_answer") or {}).get("candidates") or []):
            doc_ids = list(c.get("context_doc_ids") or [])
            values = score(question_of[row["qid"]], [text_of[d] for d in doc_ids])
            out.append(
                {
                    "qid": row["qid"],
                    "index": index,
                    "source": c["source"],
                    "ce_max": max(values) if values else None,
                    "ce_top1": values[0] if values else None,
                }
            )
    return out
