"""ルーティング方式の仕様．"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from atrium.protocol import ShardProfile
from atrium.routing import (
    AllRouter,
    NoneRouter,
    RandomRouter,
    RoutingQuery,
    SourceProfile,
    build_source_profiles,
)
from atrium.routing.features import ragroute_features
from atrium.routing.ragroute import (
    ROUTER_META,
    ROUTER_WEIGHTS,
    CorpusRoutingNN,
    RagrouteRouter,
    RouterMeta,
)


def _sources(names: list[str], dim: int = 2) -> list[SourceProfile]:
    return [
        SourceProfile(n, "", "enc", np.zeros(dim, dtype=np.float32), 1, (f"{n}-00",)) for n in names
    ]


def _query(qid: str = "q1") -> RoutingQuery:
    return RoutingQuery(qid, "question", {"enc": np.ones(2, dtype=np.float32)})


def test_all_router_selects_every_source() -> None:
    assert AllRouter().select(_query(), _sources(["a", "b"])) == ["a", "b"]


def test_none_router_selects_nothing() -> None:
    assert NoneRouter().select(_query(), _sources(["a", "b"])) == []


def test_random_router_selects_k_sources_deterministically_per_question() -> None:
    sources = _sources(["a", "b", "c", "d"])
    router = RandomRouter(k=2, seed=12)
    first = router.select(_query("q1"), sources)
    assert len(first) == 2
    assert router.select(_query("q1"), sources) == first


def test_build_source_profiles_combines_shards_in_configured_order() -> None:
    shards = [
        ShardProfile(
            shard_id="t-00",
            source="t",
            n_docs=1,
            dim=1,
            encoder="e",
            centroid=[4.0],
            description="T",
        ),
        ShardProfile(
            shard_id="p-01",
            source="p",
            n_docs=3,
            dim=1,
            encoder="e",
            centroid=[2.0],
            description="P",
        ),
        ShardProfile(
            shard_id="p-00",
            source="p",
            n_docs=1,
            dim=1,
            encoder="e",
            centroid=[6.0],
            description="P",
        ),
    ]
    profiles = build_source_profiles(shards, ["p", "t", "missing"])
    assert [p.source for p in profiles] == ["p", "t"]
    assert profiles[0].shard_ids == ("p-00", "p-01")
    assert profiles[0].centroid.tolist() == [3.0]
    assert profiles[0].n_docs == 4


def test_ragroute_features_pad_embedding_and_centroid_then_append_one_hot() -> None:
    x = ragroute_features(
        np.array([1.0], np.float32), np.array([2.0, 3.0], np.float32), 1, 3, pad_dim=2
    )
    assert x.tolist() == [1.0, 0.0, 2.0, 3.0, 0.0, 1.0, 0.0]


def test_ragroute_router_selects_sources_whose_probability_exceeds_threshold(
    tmp_path: Path,
) -> None:
    sources = ["a", "b"]
    model = CorpusRoutingNN(input_dim=2 + 2 + 2)
    with torch.no_grad():
        for p in model.parameters():
            p.zero_()
        # 出力のバイアスだけで判定を決める：sigmoid(+5) > 0.5 なので全データ源が選ばれる
        model.fc_out.bias.fill_(5.0)
    torch.save(model.state_dict(), tmp_path / ROUTER_WEIGHTS)
    meta = RouterMeta(
        dataset="medrag",
        sources=sources,
        input_dim=6,
        pad_dim=2,
        use_scaler=False,
        val_optimal_threshold=0.5,
    )
    (tmp_path / ROUTER_META).write_text(meta.model_dump_json(), encoding="utf-8")
    router = RagrouteRouter(tmp_path, sources, threshold=0.5)
    assert router.select(_query(), _sources(sources)) == ["a", "b"]
    strict = RagrouteRouter(tmp_path, sources, threshold=0.999)
    assert strict.select(_query(), _sources(sources)) == []
