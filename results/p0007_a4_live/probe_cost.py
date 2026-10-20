# p0007 A4 の費用の測定：ノードのイメージの中で，fusion=rrf のシャードに本文だけの probe 用の索引（routing=bm25_probe）を足したときの
# 読み込みの時間・RSS・1 回の probe_bm25 の時間を，足さない場合（A2 と同じ）と比べる．
# 使い方（各ノードで）: docker run --rm -i -v <REMOTE_DIR>/shards:/shards <node image> python - <rrf|rrf+probe> <shard_id>...
# 出力は集計値の JSON 1 行だけ（メールの本文は出さない）．質問の語は各シャードの 200 通の本文の先頭の 12 語（IDF は 1.0．長さの平均は 335.9）．
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

from atrium.routing.advert import tokenize
from atrium.store import FaissShardStore, load_shard

N_QUERIES = 200
QUERY_WORDS = 12
AVG_DOC_LEN = 335.9


def rss_mb() -> float:
    """/proc/self/status の VmRSS（MB）．"""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    raise RuntimeError("VmRSS not found")


def main() -> None:
    mode, shard_ids = sys.argv[1], sys.argv[2:]
    base = rss_mb()
    start = time.perf_counter()
    stores = {
        sid: load_shard(Path("/shards") / sid, "rrf", 50, mode == "rrf+probe") for sid in shard_ids
    }
    load_s = time.perf_counter() - start
    after = rss_mb()
    rng = np.random.default_rng(0)
    latencies: list[float] = []
    n_docs = 0
    for sid, store in stores.items():
        assert isinstance(store, FaissShardStore)
        n_docs += store.spec.n_docs
        lines = (Path("/shards") / sid / "chunk" / f"{store.spec.files[0].name}.jsonl").read_text().splitlines()
        if mode != "rrf+probe":
            continue
        for i in rng.choice(len(lines), size=min(N_QUERIES, len(lines)), replace=False):
            words = tokenize(" ".join(json.loads(lines[i])["content"].split()[:QUERY_WORDS]))
            t0 = time.perf_counter()
            store.probe_bm25({w: 1.0 for w in set(words)}, AVG_DOC_LEN)
            latencies.append(time.perf_counter() - t0)
    print(
        json.dumps(
            {
                "mode": mode,
                "n_shards": len(stores),
                "n_docs": n_docs,
                "rss_before_load_mb": round(base, 1),
                "rss_after_load_mb": round(after, 1),
                "rss_delta_mb": round(after - base, 1),
                "load_s": round(load_s, 3),
                "probe_bm25_ms_mean": round(1000 * float(np.mean(latencies)), 3) if latencies else None,
                "probe_bm25_ms_p95": round(1000 * float(np.percentile(latencies, 95)), 3) if latencies else None,
                "n_probes": len(latencies),
            }
        )
    )


main()
