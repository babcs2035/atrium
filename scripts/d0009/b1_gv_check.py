"""d0009 B1（Iteration 36）G-V：1 段目の候補の選択が，`visible=None` と `visible`＝全データ源の集合で全問一致することを CPU だけで確かめる．

使い方（リポジトリの直下．GPU・実機は使わない．config.yaml は b1_config.py で書いた後のもの（routing=bm25_probe・D=24）を読む）:
    uv run python scripts/d0009/b1_gv_check.py --summary-dir /tmp/i36_gv/summary --out results/d0009_b1_exclusion_i36/g_v.json
- 要約（summary）は制御点の /home/denjo/atrium-data/enronqa/summary を読み取りでコピーしたものを使う（sha256 を出力に残す）．
- H の 288 問（受信箱ごとの先頭 2 問）の事前計算のクエリ埋め込みで `SummaryProbeRouter.candidates_with_scores` を 2 通りに呼び，
  候補（上位 D 台の受信箱）と 1 段目の得点が全問で一致するか数える．1 問でも不一致なら終了コード 1．
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from atrium.benchmarks import load_questions
from atrium.config import load_config
from atrium.data_enronqa import QUESTION_FILES
from atrium.manifest import read_placement
from atrium.paths import dataset_paths
from atrium.requester import CachedQueryEmbedder
from atrium.routing import RoutingQuery, SourceProfile, make_router
from atrium.routing.advert import tokenize
from atrium.routing.summary import Bm25ProbeRouter

QUESTIONS_PER_INBOX = 2
MISMATCH_EXAMPLES = 10


def main() -> int:
    """H の全問で `visible=None` と全データ源の `visible` の選択を比べ，g_v.json を書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--data-dir", default="artifacts")
    parser.add_argument("--placement", default="results/d0009_b1_exclusion_r1/placement.json")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    e = cfg.data.require_enronqa()
    paths = dataset_paths(Path(args.data_dir), "enronqa")
    summary_dir = Path(args.summary_dir)
    # make_router は router_dir.parent / "summary" を読むので，要約の親ディレクトリの下の router を渡す
    router = make_router(cfg, summary_dir.parent / "router")
    if not isinstance(router, Bm25ProbeRouter):
        raise TypeError(f"G-V needs routing=bm25_probe in {args.config}, got {router.name}")
    placement = read_placement(Path(args.placement))
    url_of_shard = placement.url_of_shard()
    names = router.artifact.names
    sources = [
        SourceProfile(
            source=n,
            kind="faiss",
            description="",
            encoder=e.encoder,
            centroid=np.zeros(1, dtype=np.float32),
            n_docs=0,
            shard_ids=(n,),
        )
        for n in names
    ]
    device_of_source = {n: url_of_shard[n] for n in names}
    questions = load_questions(
        paths.questions.parent / QUESTION_FILES["holdout"], QUESTIONS_PER_INBOX
    )
    embedder = CachedQueryEmbedder(
        paths.split_query_ids("holdout"),
        {e.encoder: paths.split_query_embeddings("holdout", e.encoder)},
    )
    all_sources = set(names)
    mismatched: list[dict[str, Any]] = []
    for q in questions:
        query = RoutingQuery(
            q.qid, q.question, embedder.embed(q), tokens=tuple(tokenize(q.question))
        )
        plain = router.candidates_with_scores(query, sources, device_of_source, None)
        full = router.candidates_with_scores(query, sources, device_of_source, all_sources)
        if plain != full:
            mismatched.append(
                {
                    "qid": q.qid,
                    "candidates_equal": plain[0] == full[0],
                    "scores_equal": plain[1] == full[1],
                }
            )
    result = {
        "gate": "G-V",
        "routing": router.name,
        "probe_d": router.probe_d,
        "n_questions": len(questions),
        "n_mismatched": len(mismatched),
        "mismatched_examples": mismatched[:MISMATCH_EXAMPLES],
        "passed": bool(questions) and not mismatched,
        "summary_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(summary_dir.iterdir())
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False), "utf-8")
    print(
        json.dumps({k: v for k, v in result.items() if k != "summary_sha256"}, ensure_ascii=False)
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
