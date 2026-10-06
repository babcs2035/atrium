"""MedRAG の関連ラベルの計算の仕様（断片ファイルを区切って計算しても結果が変わらない）．"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from atrium import labels
from atrium.config import AtriumConfig
from atrium.manifest import Manifest, write_json_model
from atrium.paths import dataset_paths
from tests.conftest import write_faiss_shard


def _prepare(cfg: AtriumConfig, root: Path) -> None:
    rng = np.random.default_rng(3)
    paths = dataset_paths(root, "medrag")
    shards = [
        write_faiss_shard(
            paths.shards,
            "pubmed-00",
            "pubmed",
            {"a": rng.normal(size=(37, 768)).astype(np.float32)},
        ),
        write_faiss_shard(
            paths.shards,
            "textbooks-00",
            "textbooks",
            {"b": rng.normal(size=(23, 768)).astype(np.float32)},
        ),
    ]
    write_json_model(
        paths.manifest, Manifest(dataset="medrag", sources=["pubmed", "textbooks"], shards=shards)
    )
    paths.queries.mkdir(parents=True)
    np.save(
        paths.query_embeddings(cfg.data.medrag.query_encoder),
        rng.normal(size=(9, 768)).astype(np.float32),
    )
    paths.query_ids.write_text(json.dumps([f"medqa/{i}" for i in range(9)]))


def test_labels_do_not_depend_on_block_size(
    cfg: AtriumConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = cfg.model_copy(
        update={"retrieval": cfg.retrieval.model_copy(update={"k_ret": 5, "k_rerank": 4})}
    )
    results = []
    for block in (1000, 7):
        root = tmp_path / str(block)
        _prepare(cfg, root)
        monkeypatch.setattr(labels, "LABEL_BLOCK_ROWS", block)
        labels.compute_medrag_labels(cfg, dataset_paths(root, "medrag"))
        results.append(json.loads(dataset_paths(root, "medrag").labels.read_text()))
    assert results[0] == results[1]
    assert set(results[0]) == {f"medqa/{i}" for i in range(9)}


def test_check_labels_meta_rejects_changed_k_rerank(cfg: AtriumConfig, tmp_path: Path) -> None:
    paths = dataset_paths(tmp_path, "medrag")
    paths.labels.parent.mkdir(parents=True)
    labels.write_labels_meta(cfg, "medrag", paths)
    labels.check_labels_meta(cfg, "medrag", paths)
    changed = cfg.model_copy(
        update={"retrieval": cfg.retrieval.model_copy(update={"k_rerank": 10})}
    )
    with pytest.raises(ValueError, match="k_rerank"):
        labels.check_labels_meta(changed, "medrag", paths)
