"""d0009 B2（Iteration 39）：H の 288 問の採点の入力（4,320 組）の長さ（トークン数）を数えて token_lengths.json に書く（CPU・tokenizer だけ．GPU は使わない）．

使い方（リポジトリの直下）:
    uv run python scripts/d0009/b2l_token_lengths.py [--data-dir artifacts] [--out results/d0009_b2_scoring_cost_maxlen1024/token_lengths.json]
- 入力は `atrium.score_bench.load_inputs`（score-bench と同じ）．長さは CrossEncoder が切る前の 1 組（質問 + 文書 + 特殊トークン）のトークン数で，
  bge-reranker-v2-m3 の固定 revision の tokenizer を使う．b2l_verdict.py が 1,024 以下・超えるの分けと G-TR に使う．
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from atrium.hf_revisions import pinned_revision
from atrium.requester import CrossEncoderReranker
from atrium.score_bench import load_inputs

RUN_RESULTS = Path("results/d0009_a3_p1/results.jsonl")
DEFAULT_OUT = Path("results/d0009_b2_scoring_cost_maxlen1024/token_lengths.json")
MAX_LENGTH_UNDER_TEST = 1024
PERCENTILES = (50, 90, 95, 99)


def count_pair_lengths(data_dir: Path) -> list[dict[str, str | int]]:
    """全ての組（質問 ID・候補の番号・文書 ID）の，切る前の長さ（質問 + 文書 + 特殊トークン）を返す．"""
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        CrossEncoderReranker.MODEL, revision=pinned_revision(CrossEncoderReranker.MODEL)
    )
    rows: list[dict[str, str | int]] = []
    for item in load_inputs(data_dir, RUN_RESULTS):
        for ci, candidate in enumerate(item.candidates):
            for doc_id, text in candidate:
                length = len(tokenizer(item.query, text, truncation=False)["input_ids"])
                rows.append({"qid": item.qid, "candidate": ci, "doc_id": doc_id, "length": length})
    return rows


def summarize(rows: list[dict[str, str | int]]) -> dict[str, float | int]:
    """長さの分布と，1,024 を超える組の数．"""
    lengths = np.asarray([int(r["length"]) for r in rows])
    out: dict[str, float | int] = {
        "n_pairs": int(lengths.size),
        "min": int(lengths.min()),
        "max": int(lengths.max()),
        "n_over_1024": int((lengths > MAX_LENGTH_UNDER_TEST).sum()),
        "n_over_2048": int((lengths > 2048).sum()),
        "n_over_4096": int((lengths > 4096).sum()),
        "n_over_8192": int((lengths > 8192).sum()),
    }
    out.update({f"p{p}": float(np.percentile(lengths, p)) for p in PERCENTILES})
    return out


def main() -> int:
    """長さを数えて JSON に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="artifacts")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    rows = count_pair_lengths(Path(args.data_dir))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    result = {"summary": summarize(rows), "pairs": rows}
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
