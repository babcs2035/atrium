"""p0007 A4：実行ごとの正答率・持ち主が候補に入る割合・費用を数える（判定は atrium compare が出す．ここは記述用の集計）．

使い方: uv run python results/p0007_a4_live/a4_report.py <run_dir>...
正答率の分母は全問（判定が無い・失敗した問は不正解）．区間は Wilson の 95%．
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

WILSON_Z = 1.96


def wilson(k: int, n: int) -> tuple[float, float]:
    """二項比率の Wilson の 95% 区間．"""
    p = k / n
    denom = 1 + WILSON_Z**2 / n
    center = (p + WILSON_Z**2 / (2 * n)) / denom
    half = WILSON_Z * math.sqrt(p * (1 - p) / n + WILSON_Z**2 / (4 * n**2)) / denom
    return center - half, center + half


def report(run_dir: Path) -> dict[str, Any]:
    """1 つの実行の集計．"""
    # 再開で追記された行があれば，同じ qid は最後の行を採る（atrium.analysis.read_results と同じ）
    by_qid = {r["qid"]: r for r in map(json.loads, (run_dir / "results.jsonl").read_text().splitlines()) if r}
    rows = list(by_qid.values())
    judged = {
        j["qid"]: bool(j["correct"])
        for j in map(json.loads, (run_dir / "judgements.jsonl").read_text().splitlines())
        if j.get("correct") is not None
    }
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    n = len(rows)
    correct = [judged.get(r["qid"], False) for r in rows]
    out: dict[str, Any] = {
        "run": run_dir.name,
        "n": n,
        "failed": sum(r["error"] is not None for r in rows),
        "n_unjudged": sum(r["qid"] not in judged for r in rows),
        "accuracy": sum(correct) / n,
        "accuracy_wilson95": wilson(sum(correct), n),
    }
    owner_rows = [r for r in rows if "owner_answer" in r]
    if owner_rows:
        inside = [r["bank"] in r["owner_answer"]["candidate_sources"] for r in rows]
        chosen_owner = [r["owner_answer"]["chosen_source"] == r["bank"] for r in rows]
        acc_in = [c for c, i in zip(correct, inside, strict=True) if i]
        acc_out = [c for c, i in zip(correct, inside, strict=True) if not i]
        out |= {
            "owner_in_candidates": sum(inside) / n,
            "accuracy_given_owner_in": sum(acc_in) / max(1, len(acc_in)),
            "accuracy_given_owner_out": sum(acc_out) / max(1, len(acc_out)),
            "chosen_is_owner": sum(chosen_owner) / n,
            "candidate_unknown_share": sum(r["owner_answer"]["n_unknown"] for r in rows)
            / sum(r["owner_answer"]["n_candidates"] for r in rows),
            "chosen_unknown_share": sum(bool(r["owner_answer"]["chosen_unknown"]) for r in rows)
            / n,
            "requests_total_mean": float(np.mean([r["owner_answer"]["n_requests_total"] for r in rows])),
            "generations_mean": float(np.mean([r["owner_answer"]["n_generations"] for r in rows])),
        }
    e2e = [r["timings"]["e2e_s"] for r in rows]
    out |= {
        "e2e_p50_s": float(np.percentile(e2e, 50)),
        "e2e_p95_s": float(np.percentile(e2e, 95)),
        "throughput_q_per_s": n / (meta["finished_at"] - meta["started_at"]),
        "wall_s": meta["finished_at"] - meta["started_at"],
        "docs_exposed_mean": float(np.mean([sum(r.get("docs_exposed_by_source", {}).values()) for r in rows])),
        "probe_recipients_mean": float(np.mean([r.get("probe_recipients", 0) for r in rows])),
        "probe_response_bytes_mean": float(np.mean([r.get("probe_bytes", 0) for r in rows])),
        "probe_s_p50": float(np.percentile([r["timings"].get("probe_s", 0.0) for r in rows], 50)),
        "route_s_p50": float(np.percentile([r["timings"].get("route_s", 0.0) for r in rows], 50)),
        "advert_bytes": meta["advert_bytes"],
        "requester_cpu_s": meta.get("requester_cpu_s"),
        "expert_cpu_util_max": meta.get("expert_cpu_util_max"),
    }
    return out


if __name__ == "__main__":
    print(json.dumps([report(Path(p)) for p in sys.argv[1:]], indent=1, ensure_ascii=False))
