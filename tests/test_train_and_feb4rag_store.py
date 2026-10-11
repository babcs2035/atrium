"""ルーターの学習（小さな合成データ）と，FeB4RAG 型シャードの仕様．"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from atrium.config import AtriumConfig
from atrium.manifest import SHARD_SPEC_FILENAME, ShardSpec, write_json_model
from atrium.paths import dataset_paths
from atrium.routing import RoutingQuery, SourceProfile
from atrium.routing.ragroute import ROUTER_META, ROUTER_WEIGHTS, RagrouteRouter, RouterMeta
from atrium.store import load_shard
from atrium.train_router import train_router
from tests.conftest import write_separable_medrag_data


def test_trained_router_learns_which_source_matches_the_query(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    sources = cfg.data.medrag.sources
    shards = write_separable_medrag_data(cfg, tmp_path)
    paths = dataset_paths(tmp_path, "medrag")

    report = train_router(cfg, "medrag", paths)

    auc = report["best_val_auc"]
    assert isinstance(auc, float) and auc > 0.9
    router = RagrouteRouter(paths.router, sources, threshold=0.5)
    profiles = [
        SourceProfile(
            s.source,
            s.kind,
            s.description,
            s.encoder,
            np.array(s.centroid, np.float32),
            10,
            (s.shard_id,),
        )
        for s in shards
    ]
    query = RoutingQuery(
        "q",
        "",
        {cfg.data.medrag.query_encoder: np.r_[np.full(32, 3.0), np.zeros(736)].astype(np.float32)},
    )
    assert "pubmed" in router.select(query, profiles)


def test_router_trained_without_a_source_drops_it_and_writes_to_out_dir(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    write_separable_medrag_data(cfg, tmp_path)
    paths = dataset_paths(tmp_path, "medrag")
    out = tmp_path / "loso" / "textbooks"

    report = train_router(cfg, "medrag", paths, exclude=["textbooks"], out_dir=out)

    kept = [s for s in cfg.data.medrag.sources if s != "textbooks"]
    meta = RouterMeta.model_validate_json((out / ROUTER_META).read_text(encoding="utf-8"))
    assert meta.sources == kept
    assert meta.input_dim == 2 * 768 + len(kept)
    assert (out / ROUTER_WEIGHTS).exists()
    assert not paths.router.exists()
    # 除いたデータ源の組は学習にも評価にも入らない（300 問 × 残りの 3 源）
    assert report["n_pairs"] == {"train": 600, "val": 120, "test": 180}


def test_router_training_rejects_an_unknown_source_to_exclude(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    write_separable_medrag_data(cfg, tmp_path)

    with pytest.raises(ValueError, match="unknown sources"):
        train_router(cfg, "medrag", dataset_paths(tmp_path, "medrag"), exclude=["nope"])


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
