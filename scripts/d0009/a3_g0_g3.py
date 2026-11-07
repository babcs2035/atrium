"""d0009 A3（Iteration 22）のゲート G0（検索の決定性）・G3（失敗の数）を保存済みの results.jsonl から計算する．

使い方（リポジトリの直下で．実機・GPU は不要）:
    uv run python scripts/d0009/a3_g0_g3.py   # results/d0009_a3_rerun_variance/g0_g3.json に書く

G0：P の 3 回で各問の候補の受信箱の集合と各候補の context_doc_ids が全問一致，C の 3 回で top_doc_ids が全問一致する問の割合（≥ 0.999）．
G3：各実行の失敗した問の数と割合（流し直しの後．同じ qid の行が複数あれば最後の行を採る．≤ 1%）．
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path("results/d0009_a3_rerun_variance")
RUNS = {"P": ["d0009_a3_p1", "d0009_a3_p2", "d0009_a3_p3"], "C": ["d0009_a3_c1", "d0009_a3_c2", "d0009_a3_c3"]}
G0_THRESHOLD = 0.999
G3_THRESHOLD = 0.01


def last_rows(run: str) -> dict[str, dict[str, Any]]:
    """qid ごとに results.jsonl の最後の行を返す（流し直しは同じファイルへ追記される）．"""
    rows: dict[str, dict[str, Any]] = {}
    with (Path("results") / run / "results.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            rows[row["qid"]] = row
    return rows


def retrieval_signature(kind: str, row: dict[str, Any]) -> str:
    """P は候補の (受信箱, context_doc_ids) の集合，C は top_doc_ids を比較用の文字列にする．"""
    if kind == "C":
        return json.dumps(row.get("top_doc_ids"))
    cands = (row.get("owner_answer") or {}).get("candidates") or []
    return json.dumps(sorted((c["source"], c["context_doc_ids"]) for c in cands))


def main() -> None:
    """G0・G3 を計算して JSON に書く．"""
    result: dict[str, Any] = {"gate": "G0_G3"}
    for kind, runs in RUNS.items():
        tables = {run: last_rows(run) for run in runs}
        common = sorted(set.intersection(*(set(t) for t in tables.values())))
        same = sum(
            len({retrieval_signature(kind, tables[run][q]) for run in runs}) == 1 for q in common
        )
        result[f"G0_{kind}"] = {
            "n_common": len(common),
            "n_identical": same,
            "agree": same / len(common),
            "threshold": G0_THRESHOLD,
            "passed": bool(same / len(common) >= G0_THRESHOLD),
        }
        for run, table in tables.items():
            failed = sorted(q for q, r in table.items() if r.get("error"))
            result.setdefault("G3", {})[run] = {
                "n": len(table),
                "n_failed": len(failed),
                "failure_rate": len(failed) / len(table),
                "failed_qids": failed,
                "passed": bool(len(failed) / len(table) <= G3_THRESHOLD),
            }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "g0_g3.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
