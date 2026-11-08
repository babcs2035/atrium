"""d0009 M1（Iteration 26）：実行の全候補から R3′ の最終回答を選び，採点できる派生の実行（回答の文を含む）を書く．

使い方（リポジトリの直下で．select-features の後）:
    uv run python scripts/d0009/m1_derive.py <out_dir> <run_dir>... [--check-against <既存の R3p の run_dir>...]
R3′ は `atrium select-eval --apply R3 --ignore-unknown` と同じ規則（cross-encoder の得点 ce_max が最高の候補．UNKNOWN で除外しない．
同点は先の候補）で，`atrium.select_eval` の `row_candidates`・`choose_by_feature` をそのまま使う．既存の `write_derived_run` は
回答の文を書かないので，ここでは採った候補の回答を final_answer として書き，`atrium judge` がそのまま最終回答を採点できるようにする．
run_dir を複数渡すと，その順に results.jsonl をつなぐ（H と H2 を 1 つの質問集合 Q にする）．
--check-against に既存の派生の実行（例: results/d0009_a3_p1_R3p）を渡すと，選んだ候補の番号がその chosen_index と一致する問の割合を出す．
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from atrium.analysis import read_results
from atrium.select_eval import CE_FEATURES, choose_by_feature, read_ce_features, row_candidates


def derive_rows(run_dir: Path) -> list[dict[str, Any]]:
    """1 つの実行の全問について，R3′ で選んだ候補の回答を final_answer にした行を返す．"""
    ce = read_ce_features(run_dir / CE_FEATURES)
    out: list[dict[str, Any]] = []
    for row in read_results(run_dir / "results.jsonl"):
        cands = row_candidates(row, ce) if row.get("error") is None else []
        item: dict[str, Any] = {
            "qid": row["qid"],
            "bank": row["bank"],
            "error": row.get("error"),
            "select_rule": "R3",
            "chosen_index": None,
            "chosen_source": None,
            "final_answer": "",
            "n_candidates": len(cands),
        }
        if cands:
            index = choose_by_feature(cands, "ce_max", ignore_unknown=True)
            raw = row["owner_answer"]["candidates"][index]
            item |= {
                "chosen_index": index,
                "chosen_source": str(raw["source"]),
                "final_answer": str(raw["answer"]),
            }
        out.append(item)
    return out


def main() -> None:
    """派生の実行を out_dir に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir")
    parser.add_argument("run_dir", nargs="+")
    parser.add_argument("--check-against", nargs="*", default=[])
    args = parser.parse_args()
    rows = [item for run in args.run_dir for item in derive_rows(Path(run))]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for item in rows:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    (out_dir / "derived_from.json").write_text(
        json.dumps({"runs": args.run_dir, "select_rule": "R3", "ignore_unknown": True}, indent=1),
        encoding="utf-8",
    )
    summary: dict[str, Any] = {
        "n": len(rows),
        "n_error": sum(r["error"] is not None for r in rows),
        "n_no_candidates": sum(r["error"] is None and r["chosen_index"] is None for r in rows),
    }
    if args.check_against:
        chosen = {r["qid"]: r["chosen_index"] for r in rows}
        existing = {
            r["qid"]: r["chosen_index"]
            for path in args.check_against
            for r in read_results(Path(path) / "results.jsonl")
        }
        shared = [q for q in existing if q in chosen]
        same = sum(chosen[q] == existing[q] for q in shared)
        summary |= {"check_n": len(shared), "check_same_chosen_index": same}
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
