"""シャードの分割・重心の合成・配置の仕様．"""

from __future__ import annotations

import numpy as np
import pytest

from atrium.manifest import (
    ChunkFile,
    Manifest,
    ShardSpec,
    combine_centroids,
    group_files_into_shards,
    plan_placement,
)


def test_group_files_starts_new_shard_when_budget_would_be_exceeded() -> None:
    # 1 文書 = 4 次元 × 2 バイト = 8 バイト．予算 24 バイトなら 3 文書まで
    files = [ChunkFile(name=n, n_docs=k) for n, k in (("a", 2), ("b", 1), ("c", 2))]
    groups = group_files_into_shards(files, dim=4, budget_bytes=24)
    assert [[f.name for f in g] for g in groups] == [["a", "b"], ["c"]]


def test_group_files_puts_oversized_file_alone() -> None:
    files = [ChunkFile(name="big", n_docs=10), ChunkFile(name="small", n_docs=1)]
    groups = group_files_into_shards(files, dim=4, budget_bytes=24)
    assert [[f.name for f in g] for g in groups] == [["big"], ["small"]]


def test_combine_centroids_equals_mean_of_all_documents() -> None:
    rng = np.random.default_rng(0)
    a, b = rng.normal(size=(3, 5)), rng.normal(size=(7, 5))
    combined = combine_centroids([3, 7], [a.mean(axis=0).tolist(), b.mean(axis=0).tolist()])
    np.testing.assert_allclose(combined, np.concatenate([a, b]).mean(axis=0), rtol=1e-5)


def test_combine_centroids_rejects_empty_shards() -> None:
    with pytest.raises(ValueError):
        combine_centroids([0], [[1.0]])


def _manifest(n_shards: int) -> Manifest:
    shards = [
        ShardSpec(
            shard_id=f"s-{i}",
            source="s",
            kind="faiss",
            files=[],
            n_docs=1,
            dim=1,
            encoder="e",
            centroid=[0.0],
            description="",
        )
        for i in range(n_shards)
    ]
    return Manifest(dataset="medrag", sources=["s"], shards=shards)


def test_plan_placement_assigns_one_shard_per_host_in_order() -> None:
    placement = plan_placement(_manifest(2), ["h1", "h2", "h3"], port=8100)
    assert [(n.host, n.shard_ids, n.url) for n in placement.nodes] == [
        ("h1", ["s-0"], "http://h1:8100"),
        ("h2", ["s-1"], "http://h2:8100"),
    ]


def test_plan_placement_wraps_around_when_hosts_are_fewer_than_shards() -> None:
    placement = plan_placement(_manifest(3), ["h1", "h2"], port=8100)
    assert {n.host: n.shard_ids for n in placement.nodes} == {"h1": ["s-0", "s-2"], "h2": ["s-1"]}
