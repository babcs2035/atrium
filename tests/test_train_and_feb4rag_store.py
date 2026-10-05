"""ルーターの学習（小さな合成データ）と，FeB4RAG 型シャードの仕様．"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from atrium.config import AtriumConfig
from atrium.manifest import SHARD_SPEC_FILENAME, Manifest, ShardSpec, write_json_model
from atrium.paths import dataset_paths
from atrium.routing import RoutingQuery, SourceProfile
from atrium.routing.ragroute import RagrouteRouter
from atrium.store import load_shard
from atrium.train_router import train_router


def test_trained_router_learns_which_source_matches_the_query(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    # 先頭 32 次元の符号で関連するデータ源が決まる，容易に分離できる問題
    sources = cfg.data.medrag.sources
    paths = dataset_paths(tmp_path, "medrag")
    rng = np.random.default_rng(0)
    qids = [f"medqa/{i:04d}" for i in range(300)]
    sign = rng.choice([-1.0, 1.0], size=len(qids))
    emb = rng.normal(size=(len(qids), 768)).astype(np.float32)
    emb[:, :32] += 3 * sign[:, None]
    labels = {q: (["pubmed"] if sign[i] > 0 else ["textbooks"]) for i, q in enumerate(qids)}
    shards = [
        ShardSpec(
            shard_id=f"{s}-00",
            source=s,
            kind="faiss",
            files=[],
            n_docs=10,
            dim=768,
            encoder=cfg.data.medrag.query_encoder,
            centroid=[float(i)] * 768,
            description=s,
        )
        for i, s in enumerate(sources)
    ]
    write_json_model(paths.manifest, Manifest(dataset="medrag", sources=sources, shards=shards))
    paths.queries.mkdir(parents=True)
    np.save(paths.query_embeddings(cfg.data.medrag.query_encoder), emb)
    paths.query_ids.write_text(json.dumps(qids))
    paths.labels.parent.mkdir(parents=True)
    paths.labels.write_text(json.dumps(labels))
    paths.split.write_text(
        json.dumps({"train": qids[:200], "val": qids[200:240], "test": qids[240:]})
    )

    report = train_router(cfg, "medrag", paths)

    auc = report["best_val_auc"]
    assert isinstance(auc, float) and auc > 0.9
    router = RagrouteRouter(paths.router, sources, threshold=0.5)
    profiles = [
        SourceProfile(
            s.source, s.description, s.encoder, np.array(s.centroid, np.float32), 10, (s.shard_id,)
        )
        for s in shards
    ]
    query = RoutingQuery(
        "q",
        "",
        {cfg.data.medrag.query_encoder: np.r_[np.full(32, 3.0), np.zeros(736)].astype(np.float32)},
    )
    assert "pubmed" in router.select(query, profiles)


def test_search_results_shard_returns_distributed_hits_with_text(tmp_path: Path) -> None:
    shard_dir = tmp_path / "nfcorpus"
    shard_dir.mkdir()
    (shard_dir / "results.jsonl").write_text(
        json.dumps({"qid": "1", "hits": [["MED-1", 0.9], ["MED-2", 0.8]]}) + "\n"
    )
    (shard_dir / "docs.jsonl").write_text(
        json.dumps({"_id": "MED-1", "title": "T", "text": "body"}) + "\n"
    )
    spec = ShardSpec(
        shard_id="nfcorpus",
        source="nfcorpus",
        kind="search_results",
        files=[],
        n_docs=2,
        dim=2,
        encoder="e",
        centroid=[0.0, 0.0],
        description="",
    )
    write_json_model(shard_dir / SHARD_SPEC_FILENAME, spec)
    store = load_shard(shard_dir)
    docs = store.search(k=5, embedding=None, query_id="1")
    assert [(d.doc_id, d.content) for d in docs] == [("MED-1", "body"), ("MED-2", "")]
