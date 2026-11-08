"""d0009 PF（Iteration 27）：smoke の G-L（llama の direct の形）を数え，<run_dir>/gl.json に書く．

使い方（リポジトリの直下で．smoke の実行の後）:
    uv run python scripts/d0009/pf_gl.py results/d0009_pf_smoke [--first-30-from results/d0009_m1_dev300_g4_partial]
数え方は scripts/d0009/pf_diag.py と同じ：上限に達した = output_tokens >= llm.num_predict，`Answer:` の行 = `prompts._FREE_ANSWER` に一致．
- 失敗 = results.jsonl の error が空でない問の数．
- 上限に達した割合 = node_answers の output_tokens（候補の生成のうち計測値がある分）．
- `Answer:` の行がある割合 = 採った 1 つの候補の生の出力（record["answer"]．save_candidates の候補の回答は取り出し後なので生は残らない）で数える．
G-L の判定は，--first-30-from の実行の先頭 30 問の qid（Iteration 26 の dev300 の先頭 30 問）に絞った値で行い，smoke の全問の値も併記する．
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from atrium import prompts
from atrium.analysis import read_results
from atrium.config import load_config

FAILURES_LIMIT = 1
HIT_CAP_LIMIT = 0.05
ANSWER_LINE_MIN = 0.95
FIRST_N = 30


def count(rows: list[dict[str, Any]], num_predict: int) -> dict[str, Any]:
    """行の集合について G-L の 3 つの数と，出力のトークン数の中央値を数える．"""
    failed = sum(r.get("error") is not None for r in rows)
    ok = [r for r in rows if r.get("error") is None]
    tokens = [
        int(na["output_tokens"])
        for r in ok
        for na in r.get("node_answers") or []
        if na.get("output_tokens") is not None
    ]
    raw = [str(r.get("answer") or "") for r in ok]
    with_line = sum(prompts._FREE_ANSWER.search(a) is not None for a in raw)
    hit = sum(t >= num_predict for t in tokens)
    tokens_sorted = sorted(tokens)
    return {
        "n_questions": len(rows),
        "failed": failed,
        "n_generations_counted": len(tokens),
        "hit_cap": hit,
        "hit_cap_rate": hit / len(tokens) if tokens else None,
        "n_chosen_raw": len(raw),
        "answer_line": with_line,
        "answer_line_rate": with_line / len(raw) if raw else None,
        "median_output_tokens": tokens_sorted[len(tokens_sorted) // 2] if tokens else None,
    }


def main() -> None:
    """smoke の行を数えて gl.json を書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("--first-30-from", default="results/d0009_m1_dev300_g4_partial")
    args = parser.parse_args()
    num_predict = load_config().llm.num_predict
    rows = read_results(Path(args.run_dir) / "results.jsonl")
    first = [r["qid"] for r in read_results(Path(args.first_30_from) / "results.jsonl")][:FIRST_N]
    wanted = set(first)
    subset = [r for r in rows if r["qid"] in wanted]
    result = {
        "num_predict": num_predict,
        "first_30_qids_in_smoke": len(subset),
        "first_30": count(subset, num_predict),
        "all": count(rows, num_predict),
    }
    g = result["first_30"]
    result["G_L"] = {
        "failed_le": FAILURES_LIMIT,
        "hit_cap_le": HIT_CAP_LIMIT,
        "answer_line_ge": ANSWER_LINE_MIN,
        "passes": g["failed"] <= FAILURES_LIMIT
        and (g["hit_cap_rate"] or 0.0) <= HIT_CAP_LIMIT
        and (g["answer_line_rate"] or 0.0) >= ANSWER_LINE_MIN,
    }
    (Path(args.run_dir) / "gl.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
