"""p0007 F-7：P2P-R3′（m = 4）の実行の区分 (a)(b)(c)・費用・オフラインとの照合・A6（m = 3）との比較を数える（記述用．判定は atrium compare）．

使い方:
    uv run python results/p0007_f7_live/f7_report.py <f7_run_dir> <comparator_run_dir> --a6 <a6_run_dir> [--offline results/p0007_a4_build_summary]
<f7_run_dir> は answer.aggregate=cross_encoder・save_candidates=false の実行（採った回答の判定 judgements.jsonl を含む）．
<a6_run_dir> は A6 の同じ分割の実行（m = 3．候補ごとの判定・ce_features を含む）．出力は標準出力の JSON．
区分は持ち主（行の bank）が候補に入ったか・採った候補が持ち主かで決める．正答率の分母は全問（失敗・判定不能は不正解．失敗した問は候補が無いので (a)）．
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import merge_judgements, read_results
from atrium.select_eval import (
    CANDIDATE_JUDGEMENTS,
    CE_FEATURES,
    apply_rule,
    read_candidate_judgements,
    read_ce_features,
)

CE_TOLERANCE = 1e-3  # 同じ文脈・同じモデルでも GPU の丸めで得点が少し違いうる


def f7_outcomes(run_dir: Path) -> dict[str, dict[str, Any]]:
    """質問 ID → 正誤・持ち主が候補に入ったか・採った候補が持ち主か・採った番号（失敗した問は候補なし）．"""
    rows = merge_judgements(read_results(run_dir / "results.jsonl"), run_dir / "judgements.jsonl")
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        oa = r.get("owner_answer") or {}
        sources = oa.get("candidate_sources") or []
        failed = r.get("error") is not None or not sources
        out[r["qid"]] = {
            "correct": bool(r.get("correct", False)) and not failed,
            "owner_in": (not failed) and str(r["bank"]) in sources,
            "chosen_owner": (not failed) and oa.get("chosen_source") == str(r["bank"]),
            "chosen_source": None if failed else oa.get("chosen_source"),
            "n_candidates": 0 if failed else len(sources),
        }
    return out


def a6_outcomes(a6_dir: Path) -> dict[str, dict[str, Any]]:
    """A6 の同じ分割の R3′（m = 3）の質問 ID → 同じ形の結果（候補ごとの判定・ce_features から当てはめる）．"""
    rows = read_results(a6_dir / "results.jsonl")
    judged = read_candidate_judgements(a6_dir / CANDIDATE_JUDGEMENTS)
    ce = read_ce_features(a6_dir / CE_FEATURES)
    outcomes = apply_rule(rows, judged, "R3", ce, ignore_unknown=True)
    by_qid = {r["qid"]: r for r in rows}
    return {
        o.qid: {
            "correct": o.correct,
            "owner_in": o.owner_in,
            "chosen_owner": o.chosen_owner,
            # 候補の並びは実行ごとに違いうるので，採った候補は番号ではなく受信箱の名前で持つ
            "chosen_source": (
                None
                if o.chosen_index is None
                else by_qid[o.qid]["owner_answer"]["candidates"][o.chosen_index]["source"]
            ),
        }
        for o in outcomes
    }


def comparator_correct(run_dir: Path) -> dict[str, bool]:
    """比べる相手の実行の質問 ID → 正誤（判定の無い問は不正解）．"""
    rows = merge_judgements(read_results(run_dir / "results.jsonl"), run_dir / "judgements.jsonl")
    return {r["qid"]: bool(r.get("correct", False)) for r in rows}


def segments(
    outcomes: Mapping[str, Mapping[str, Any]], other: Mapping[str, bool]
) -> dict[str, Any]:
    """区分ごとの割合・P2P と相手の正答率・差への寄与（割合 × 正答率の差）．"""
    n = len(outcomes)
    groups = {
        "a_owner_out": [q for q, o in outcomes.items() if not o["owner_in"]],
        "b_owner_chosen": [q for q, o in outcomes.items() if o["owner_in"] and o["chosen_owner"]],
        "c_owner_in_not_chosen": [
            q for q, o in outcomes.items() if o["owner_in"] and not o["chosen_owner"]
        ],
    }
    out: dict[str, Any] = {}
    for name, qids in groups.items():
        share = len(qids) / n
        p2p = sum(outcomes[q]["correct"] for q in qids) / max(1, len(qids))
        cen = sum(other.get(q, False) for q in qids) / max(1, len(qids))
        out[name] = {
            "n": len(qids),
            "share": share,
            "p2p_accuracy": p2p,
            "comparator_accuracy": cen,
            "contribution_to_diff": share * (p2p - cen),
        }
    out["total"] = {
        "n": n,
        "p2p_accuracy": sum(o["correct"] for o in outcomes.values()) / n,
        "comparator_accuracy": sum(other.get(q, False) for q in outcomes) / n,
    }
    return out


def transitions(
    m4: Mapping[str, Mapping[str, Any]], m3: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """同じ質問の m = 3（A6）から m = 4 への区分の移動と，採用した候補・正誤の一致．"""

    def segment(o: Mapping[str, Any]) -> str:
        if not o["owner_in"]:
            return "a"
        return "b" if o["chosen_owner"] else "c"

    shared = [q for q in m4 if q in m3]
    counts: dict[str, int] = {}
    for q in shared:
        key = f"{segment(m3[q])}->{segment(m4[q])}"
        counts[key] = counts.get(key, 0) + 1
    same_source = sum(m4[q]["chosen_source"] == m3[q]["chosen_source"] for q in shared)
    gain = sum(m4[q]["correct"] and not m3[q]["correct"] for q in shared)
    loss = sum(m3[q]["correct"] and not m4[q]["correct"] for q in shared)
    return {
        "n_shared": len(shared),
        "segment_transitions_m3_to_m4": dict(sorted(counts.items())),
        "chosen_source_same": same_source / max(1, len(shared)),
        "correct_only_m4": gain,
        "correct_only_m3": loss,
    }


def percentiles(values: Sequence[float]) -> dict[str, float] | None:
    """p50・p95・平均（空なら None）．"""
    if not values:
        return None
    return {
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "mean": float(np.mean(values)),
    }


def costs(run_dir: Path) -> dict[str, Any]:
    """1 問あたりの要求数・生成回数・外へ出た文書・質問者へ渡った文書・待ち時間・処理量（待ち時間には GPU の共有の交絡がある）．"""
    rows = read_results(run_dir / "results.jsonl")
    main_meta = run_dir / "run_meta.main.json"
    meta = json.loads(
        (main_meta if main_meta.exists() else run_dir / "run_meta.json").read_text(encoding="utf-8")
    )
    ok = [r for r in rows if r.get("error") is None]
    decode = [a["decode_s"] for r in ok for a in r.get("node_answers", [])]
    wall = meta["finished_at"] - meta["started_at"]
    return {
        "n": len(rows),
        "failed": len(rows) - len(ok),
        "requests_total_mean": float(np.mean([r["owner_answer"]["n_requests_total"] for r in ok])),
        "generations_mean": float(np.mean([r["owner_answer"]["n_generations"] for r in ok])),
        "docs_exposed_mean": float(
            np.mean([sum(r.get("docs_exposed_by_source", {}).values()) for r in ok])
        ),
        "ce_docs_received_mean": float(
            np.mean([r["owner_answer"]["ce_docs_received"] for r in ok])
        ),
        "e2e_s": percentiles([r["timings"]["e2e_s"] for r in ok]),
        "ce_s": percentiles([r["timings"]["ce_s"] for r in ok]),
        "generate_s": percentiles([r["timings"]["generate_s"] for r in ok]),
        "decode_s_mean": float(np.mean(decode)) if decode else None,
        "wall_s": wall,
        "throughput_q_per_s": len(rows) / wall,
    }


def sim_vs_live(run_dir: Path, offline_dir: Path, a6_dir: Path) -> dict[str, Any]:
    """(i) オフラインの bm25_probe_m3 の 3 受信箱が m = 4 の候補に含まれるか，(iii) 共通の 3 候補の cross-encoder の得点と A6 の ce_features の差，A6 の候補の包含."""
    rows = [r for r in read_results(run_dir / "results.jsonl") if r.get("error") is None]
    out: dict[str, Any] = {"n_rows": len(rows)}
    selection = json.loads((offline_dir / "offline_selection.json").read_text(encoding="utf-8"))
    known = [r for r in rows if r["qid"] in selection["bm25_probe_m3"]]
    out["offline_available_n"] = len(known)
    if known:
        # save_candidates: false の実行は融合得点を残さないので，オフラインの上位 3 受信箱が m = 4 の候補に含まれるかで比べる
        contained = [
            set(selection["bm25_probe_m3"][r["qid"]]) <= set(r["owner_answer"]["candidate_sources"])
            for r in known
        ]
        out["offline_m3_contained_in_m4_candidates"] = sum(contained) / len(contained)
    ce = read_ce_features(a6_dir / CE_FEATURES)
    a6_rows = {
        r["qid"]: r for r in read_results(a6_dir / "results.jsonl") if r.get("error") is None
    }
    ce_diffs: list[float] = []
    subset = 0
    compared = 0
    for r in rows:
        a6 = a6_rows.get(r["qid"])
        if a6 is None:
            continue
        compared += 1
        s4 = r["owner_answer"]["candidate_sources"]
        s3 = a6["owner_answer"]["candidate_sources"]
        subset += set(s3) <= set(s4)
        for i, src in enumerate(s3):
            if src in s4 and (r["qid"], i) in ce:
                j = s4.index(src)
                live = r["owner_answer"]["ce_max"][j]
                if live is not None:
                    ce_diffs.append(abs(float(live) - ce[(r["qid"], i)]))
    out["a6_candidates_contained_in_m4_candidates"] = subset / max(1, compared)
    out["ce_shared_candidates_n"] = len(ce_diffs)
    out["ce_abs_diff_max"] = max(ce_diffs) if ce_diffs else None
    out["ce_within_tolerance"] = sum(d <= CE_TOLERANCE for d in ce_diffs) / max(1, len(ce_diffs))
    return out


def main() -> None:
    """引数を読み，区分・費用・照合・A6 との比較を JSON で出す．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("comparator")
    parser.add_argument("--a6", required=True)
    parser.add_argument("--offline", default="results/p0007_a4_build_summary")
    args = parser.parse_args()
    run_dir, a6_dir = Path(args.run_dir), Path(args.a6)
    other = comparator_correct(Path(args.comparator))
    m4 = f7_outcomes(run_dir)
    m3 = a6_outcomes(a6_dir)
    report = {
        "run": run_dir.name,
        "comparator": Path(args.comparator).name,
        "segments_m4": segments(m4, other),
        "segments_m3_same_questions": segments({q: m3[q] for q in m4 if q in m3}, other),
        "transitions": transitions(m4, m3),
        "costs": costs(run_dir),
        "sim_vs_live": sim_vs_live(run_dir, Path(args.offline), a6_dir),
    }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
