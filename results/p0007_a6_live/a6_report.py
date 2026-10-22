"""p0007 A6：選んだ規則（UNKNOWN の除外なしの R0′〜R6′）の区分 (a)(b)(c) の分解（比べる相手と対応あり）・費用・オフラインとの照合を数える（記述用．判定は atrium compare）．

A5 の a5_report.py と同じ数え方で，規則の当てはめだけ ignore_unknown にする．--prev には同じ Router の前の実行（A5 の test）を渡す．

使い方:
    uv run python results/p0007_a6_live/a6_report.py <run_dir> <rule> <comparator_run_dir> [--prev results/p0007_a5_test] [--offline results/p0007_a4_build_summary]
<run_dir> は answer.save_candidates の実行（候補ごとの判定 candidate_judgements.jsonl・ce_features.jsonl を含む）．
出力は標準出力の JSON（`> results/p0007_a6_live/<name>.json` で保存する）．正答率の分母は全問（失敗・判定不能は不正解）．
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
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

FUSED_TOLERANCE = 1e-5


def comparator_correct(run_dir: Path) -> dict[str, bool]:
    """比べる相手の実行の質問 ID → 正誤（判定の無い問は不正解）．"""
    rows = merge_judgements(read_results(run_dir / "results.jsonl"), run_dir / "judgements.jsonl")
    return {r["qid"]: bool(r.get("correct", False)) for r in rows}


def segments(run_dir: Path, rule: str, other: Mapping[str, bool]) -> dict[str, Any]:
    """区分ごとの割合・P2P（rule）と相手の正答率・差への寄与（割合 × 正答率の差）．"""
    rows = read_results(run_dir / "results.jsonl")
    judged = read_candidate_judgements(run_dir / CANDIDATE_JUDGEMENTS)
    ce_path = run_dir / CE_FEATURES
    ce = read_ce_features(ce_path) if ce_path.exists() else None
    outcomes = apply_rule(rows, judged, rule, ce, ignore_unknown=True)
    n = len(outcomes)
    groups = {
        "a_owner_out": [o for o in outcomes if not o.owner_in],
        "b_owner_chosen": [o for o in outcomes if o.owner_in and o.chosen_owner],
        "c_owner_in_not_chosen": [o for o in outcomes if o.owner_in and not o.chosen_owner],
    }
    out: dict[str, Any] = {}
    for name, items in groups.items():
        share = len(items) / n
        p2p = sum(o.correct for o in items) / max(1, len(items))
        cen = sum(other.get(o.qid, False) for o in items) / max(1, len(items))
        out[name] = {
            "n": len(items),
            "share": share,
            "p2p_accuracy": p2p,
            "comparator_accuracy": cen,
            "contribution_to_diff": share * (p2p - cen),
        }
    out["total"] = {
        "p2p_accuracy": sum(o.correct for o in outcomes) / n,
        "comparator_accuracy": sum(other.get(o.qid, False) for o in outcomes) / n,
    }
    return out


def costs(run_dir: Path) -> dict[str, Any]:
    """1 問あたりの要求数・生成回数・待ち時間・処理量と，生成 1 回の decode の時間・対数確率の数．"""
    rows = read_results(run_dir / "results.jsonl")
    # 失敗した質問を再開した実行は run_meta.json が再開の値で上書きされるので，主の実行の控え（run_meta.main.json）を使う
    main_meta = run_dir / "run_meta.main.json"
    meta_path = main_meta if main_meta.exists() else run_dir / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    ok = [r for r in rows if r.get("error") is None]
    e2e = [r["timings"]["e2e_s"] for r in ok]
    decode = [a["decode_s"] for r in ok for a in r.get("node_answers", [])]
    return {
        "n": len(rows),
        "failed": len(rows) - len(ok),
        "requests_total_mean": float(np.mean([r["owner_answer"]["n_requests_total"] for r in ok])),
        "generations_mean": float(np.mean([r["owner_answer"]["n_generations"] for r in ok])),
        "e2e_p50_s": float(np.percentile(e2e, 50)),
        "e2e_p95_s": float(np.percentile(e2e, 95)),
        "wall_s": meta["finished_at"] - meta["started_at"],
        "throughput_q_per_s": len(rows) / (meta["finished_at"] - meta["started_at"]),
        "decode_s_mean": float(np.mean(decode)) if decode else None,
        "docs_exposed_mean": float(
            np.mean([sum(r.get("docs_exposed_by_source", {}).values()) for r in ok])
        ),
    }


def sim_vs_live(run_dir: Path, offline_dir: Path, prev_dir: Path | None) -> dict[str, Any]:
    """(i) 候補の集合とオフラインの bm25_probe_m3 の一致，(ii) 融合得点の差，(v) 前の実行との候補の集合・採った受信箱の一致．"""
    rows = [r for r in read_results(run_dir / "results.jsonl") if r.get("error") is None]
    selection = json.loads((offline_dir / "offline_selection.json").read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    if all(r["qid"] in selection["bm25_probe_m3"] for r in rows):
        same = [
            set(r["owner_answer"]["candidate_sources"]) == set(selection["bm25_probe_m3"][r["qid"]])
            for r in rows
        ]
        fused_file = np.load(offline_dir / "bm25_probe_fused.npz")
        column = {n: i for i, n in enumerate(fused_file["names"].tolist())}
        offline_fused = dict(
            zip(fused_file["qids"].tolist(), fused_file["fused"].astype(np.float64), strict=True)
        )
        diffs = [
            abs(float(c["probe_fused"]) - float(offline_fused[r["qid"]][column[c["source"]]]))
            for r in rows
            for c in r["owner_answer"]["candidates"]
            if c.get("probe_fused") is not None
        ]
        out["candidate_set_agreement"] = sum(same) / len(same)
        out["fused_abs_diff_max"] = max(diffs) if diffs else None
        out["fused_within_tolerance"] = sum(d <= FUSED_TOLERANCE for d in diffs) / max(
            1, len(diffs)
        )
    else:
        out["candidate_set_agreement"] = None  # dev の質問はオフラインの選択に無い
    if prev_dir is not None:
        prev = {
            r["qid"]: r for r in read_results(prev_dir / "results.jsonl") if r.get("error") is None
        }
        shared = [r for r in rows if r["qid"] in prev]
        out["prev_candidate_set_agreement"] = sum(
            set(r["owner_answer"]["candidate_sources"])
            == set(prev[r["qid"]]["owner_answer"]["candidate_sources"])
            for r in shared
        ) / max(1, len(shared))
        out["prev_chosen_source_agreement"] = sum(
            r["owner_answer"]["chosen_source"] == prev[r["qid"]]["owner_answer"]["chosen_source"]
            for r in shared
        ) / max(1, len(shared))
        out["prev_final_answer_identical"] = sum(
            r.get("final_answer") == prev[r["qid"]].get("final_answer") for r in shared
        ) / max(1, len(shared))
    return out


def main() -> None:
    """引数を読み，区分・費用・照合を JSON で出す．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("rule")
    parser.add_argument("comparator")
    parser.add_argument("--prev", default=None)
    parser.add_argument("--offline", default="results/p0007_a4_build_summary")
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    other = comparator_correct(Path(args.comparator))
    report = {
        "run": run_dir.name,
        "rule": args.rule,
        "comparator": Path(args.comparator).name,
        "segments_rule": segments(run_dir, args.rule, other),
        "segments_r0": segments(run_dir, "R0", other),
        "costs": costs(run_dir),
        "sim_vs_live": sim_vs_live(
            run_dir, Path(args.offline), Path(args.prev) if args.prev else None
        ),
    }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
