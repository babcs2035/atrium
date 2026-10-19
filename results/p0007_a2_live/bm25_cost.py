# p0007 A2 の費用の測定：ノードのイメージの中で，シャードの読み込みの時間・RSS・1 回の検索の時間を fusion=none と rrf で測る．
# 使い方（各ノードで）: docker run --rm -i -v <REMOTE_DIR>/shards:/shards <node image> python - <fusion> <shard_id>...
# 出力は集計値の JSON 1 行だけ（メールの本文は出さない）．検索の質問は各シャードの 200 通の本文の先頭の 12 語と，そのメールの埋め込み．
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

from atrium.store import load_shard

N_QUERIES = 200
QUERY_WORDS = 12


def rss_mb() -> float:
    """/proc/self/status の VmRSS（MB）．"""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    raise RuntimeError("VmRSS not found")


def main() -> None:
    fusion, shard_ids = sys.argv[1], sys.argv[2:]
    base = rss_mb()
    start = time.perf_counter()
    stores = {sid: load_shard(Path("/shards") / sid, fusion, 50) for sid in shard_ids}  # type: ignore[arg-type]
    load_s = time.perf_counter() - start
    after = rss_mb()
    rng = np.random.default_rng(0)
    latencies: list[float] = []
    n_docs = 0
    for sid, store in stores.items():
        emb = np.load(Path("/shards") / sid / "emb" / f"{store.spec.files[0].name}.f16.npy")
        n_docs += store.spec.n_docs
        lines = (Path("/shards") / sid / "chunk" / f"{store.spec.files[0].name}.jsonl").read_text().splitlines()
        for i in rng.choice(len(lines), size=min(N_QUERIES, len(lines)), replace=False):
            text = " ".join(json.loads(lines[i])["content"].split()[:QUERY_WORDS])
            t0 = time.perf_counter()
            store.search(k=5, embedding=emb[i].astype(np.float32).tolist(), query_id=None, query_text=text)
            latencies.append(time.perf_counter() - t0)
    print(
        json.dumps(
            {
                "fusion": fusion,
                "n_shards": len(stores),
                "n_docs": n_docs,
                "rss_before_load_mb": round(base, 1),
                "rss_after_load_mb": round(after, 1),
                "rss_delta_mb": round(after - base, 1),
                "load_s": round(load_s, 3),
                "search_ms_mean": round(1000 * float(np.mean(latencies)), 3),
                "search_ms_p95": round(1000 * float(np.percentile(latencies, 95)), 3),
                "n_searches": len(latencies),
            }
        )
    )


main()
