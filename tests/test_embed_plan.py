"""MedRAG の埋め込みを複数の GPU で分担する表の仕様．"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from atrium.config import AtriumConfig
from atrium.data_medrag import balance_by_size, plan_embedding
from atrium.paths import dataset_paths


def test_balance_by_size_assigns_largest_first_to_least_loaded_worker() -> None:
    # a(10)→w1，b(9)→w2，c(5)→w2(14)，d(4)→w1(14)，同じ負荷なら先の担当者へ：e(2)→w1(16)
    sizes = {"a": 10, "b": 9, "c": 5, "d": 4, "e": 2}
    assert balance_by_size(sizes, ["w1", "w2"]) == {"w1": ["a", "d", "e"], "w2": ["b", "c"]}


def test_balance_by_size_gives_empty_lists_to_idle_workers() -> None:
    assert balance_by_size({"a": 1}, ["w1", "w2"]) == {"w1": ["a"], "w2": []}


def test_plan_embedding_skips_files_already_embedded(cfg: AtriumConfig, tmp_path: Path) -> None:
    paths = dataset_paths(tmp_path, "medrag")
    chunk = paths.corpus("textbooks") / "chunk"
    chunk.mkdir(parents=True)
    for name in ("Anatomy", "Biochem", "Empty"):
        doc = json.dumps({"id": "x", "title": "t", "content": "c"}) + "\n"
        (chunk / f"{name}.jsonl").write_text("" if name == "Empty" else doc)
    (paths.corpus("textbooks") / "emb").mkdir()
    np.save(paths.corpus("textbooks") / "emb" / "Anatomy.f16.npy", np.zeros((1, 768), np.float16))
    plan = plan_embedding(cfg, paths, ["local"])
    assert plan == {"local": ["textbooks/Biochem"]}
