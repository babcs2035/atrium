"""p0007 A4：実機の bm25_probe の選択と，オフライン（build-summary の bm25_probe）の選択の一致を数える（同点を許す照合を含む）．

使い方: uv run python results/p0007_a4_live/sim_vs_live.py <run_dir> <selection_key> <field>
  selection_key: bm25_probe（m = 2．retrieval_only）／ bm25_probe_m3（m = 3．owner_answer）
  field: selected_sources／owner_answer.candidate_sources
同点を許す一致：実機が選んだ各受信箱のオフラインの得点が，オフラインの m 番目の得点 − 1e-5 以上（= 得点の同じ別の受信箱を選んだだけ）．
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

TIE_TOLERANCE = 1e-5
OFFLINE_DIR = Path("results/p0007_a4_build_summary")


def main(run_dir: Path, key: str, field: str) -> None:
    """全問の一致率（厳密・同点を許す）を出力する．"""
    selection = json.loads((OFFLINE_DIR / "offline_selection.json").read_text(encoding="utf-8"))[key]
    fused_file = np.load(OFFLINE_DIR / "bm25_probe_fused.npz")
    qids = fused_file["qids"].tolist()
    names = fused_file["names"].tolist()
    fused = dict(zip(qids, fused_file["fused"].astype(np.float64), strict=True))
    column = {n: i for i, n in enumerate(names)}
    by_qid = {r["qid"]: r for r in map(json.loads, (run_dir / "results.jsonl").read_text().splitlines()) if r}
    rows = list(by_qid.values())
    n = strict = tie_aware = failed = 0
    mismatches: list[str] = []
    for row in rows:
        if row["error"] is not None:
            failed += 1
            continue
        live = row["owner_answer"]["candidate_sources"] if "." in field else row[field]
        expected = selection[row["qid"]]
        n += 1
        if set(live) == set(expected):
            strict += 1
            tie_aware += 1
            continue
        scores = fused[row["qid"]]
        kth = np.sort(scores)[::-1][len(expected) - 1]
        if len(live) == len(expected) and all(scores[column[s]] >= kth - TIE_TOLERANCE for s in live):
            tie_aware += 1
        else:
            mismatches.append(row["qid"])
    print(
        json.dumps(
            {
                "run": str(run_dir),
                "n": n,
                "failed": failed,
                "strict_agreement": strict / n,
                "tie_aware_agreement": tie_aware / n,
                "n_mismatch_beyond_ties": len(mismatches),
                "mismatch_qids": mismatches[:10],
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
