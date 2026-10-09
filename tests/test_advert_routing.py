"""公開情報を使うルーティング方式（p0004 の RQ-A）の仕様．"""

from __future__ import annotations

import numpy as np

from atrium.manifest import Advert
from atrium.routing import RoutingQuery, SourceProfile
from atrium.routing.advert import (
    CardSimRouter,
    CentroidSimRouter,
    MultiCentroidRouter,
    OracleRouter,
    TermSketchRouter,
    tokenize,
    top_m,
)


def _source(name: str, centroid: list[float], advert: Advert) -> SourceProfile:
    return SourceProfile(
        source=name,
        kind="faiss",
        description=f"card of {name}",
        encoder="enc",
        centroid=np.asarray(centroid, np.float32),
        n_docs=1,
        shard_ids=(name,),
        advert=advert,
    )


def _advert(terms: list[str], centroids: list[list[float]], card: list[float]) -> Advert:
    return Advert(
        terms=terms, term_weights=[1.0] * len(terms), centroids=centroids, card_embedding=card
    )


SOURCES = [
    _source("a", [1.0, 0.0], _advert(["gas"], [[0.5, 0.5]], [0.0, 1.0])),
    _source("b", [0.0, 1.0], _advert(["power", "price"], [[0.0, 1.0], [0.9, 0.1]], [1.0, 0.0])),
]


def _query(vec: list[float], text: str = "") -> RoutingQuery:
    return RoutingQuery(
        "q1", text, {"enc": np.asarray(vec, np.float32)}, tokens=tuple(tokenize(text))
    )


def test_top_m_keeps_the_original_order_and_breaks_ties_by_position() -> None:
    assert top_m(["x", "y", "z"], [0.5, 0.9, 0.5], 2) == ["x", "y"]


def test_centroid_sim_picks_the_closest_mean() -> None:
    assert CentroidSimRouter(1).select(_query([1.0, 0.0]), SOURCES) == ["a"]


def test_multi_centroid_uses_the_best_of_several_centers() -> None:
    # b の重心は遠いが，2 個目の中心がクエリに近い
    assert MultiCentroidRouter(1).select(_query([0.95, 0.05]), SOURCES) == ["b"]


def test_card_sim_scores_the_card_embedding_not_the_centroid() -> None:
    assert CardSimRouter(1).select(_query([1.0, 0.0]), SOURCES) == ["b"]


def test_term_sketch_counts_overlapping_query_words() -> None:
    assert TermSketchRouter(1).select(_query([0.0, 0.0], "What is the power price?"), SOURCES) == [
        "b"
    ]


def test_oracle_returns_only_the_labelled_source() -> None:
    assert OracleRouter({"q1": ["b"]}).select(_query([1.0, 0.0]), SOURCES) == ["b"]
    assert OracleRouter({}).select(_query([1.0, 0.0]), SOURCES) == []


def test_advert_bytes_is_zero_for_methods_that_publish_nothing() -> None:
    assert OracleRouter({}).advert_bytes(SOURCES) == 0
    assert TermSketchRouter(1).advert_bytes(SOURCES) > 0
