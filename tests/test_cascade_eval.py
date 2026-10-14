"""二段ルーティング centroid_probe（atrium.cascade_eval・CentroidProbeRouter）の規則の仕様．"""

from __future__ import annotations

import numpy as np
import pytest

from atrium.advert_eval import hit_at, ranks_of_gold
from atrium.cascade_eval import (
    cascade_scores,
    choose_k,
    judge_success,
    regression_check,
)
from atrium.manifest import Advert
from atrium.routing import RoutingQuery, SourceProfile
from atrium.routing.advert import CentroidProbeRouter, MultiCentroidRouter

FIRST = np.array([[0.9, 0.8, 0.1, 0.2], [0.1, 0.2, 0.9, 0.8]], np.float32)
SECOND = np.array([[0.1, 0.7, 0.9, 0.6], [0.5, 0.4, 0.3, 0.6]], np.float32)


def test_only_the_top_k_of_the_first_stage_can_be_chosen() -> None:
    scores = cascade_scores(FIRST, SECOND, 2)
    assert np.isneginf(scores[0, 2:]).all()
    assert scores[0, :2].tolist() == pytest.approx([0.1, 0.7])


def test_k_equal_to_the_number_of_inboxes_keeps_the_second_stage_scores() -> None:
    assert np.array_equal(cascade_scores(FIRST, SECOND, 4), SECOND)


def test_k_equal_to_m_gives_the_same_top_m_set_as_the_first_stage() -> None:
    gold = [1, 2]
    cascade = ranks_of_gold(cascade_scores(FIRST, SECOND, 2), gold)
    first = ranks_of_gold(FIRST, gold)
    assert hit_at(cascade, 2) == hit_at(first, 2)


def test_gold_outside_the_candidates_is_never_a_hit() -> None:
    ranks = ranks_of_gold(cascade_scores(FIRST, SECOND, 2), [2, 0])
    assert (ranks > 2).all()


def test_choose_k_takes_the_smallest_k_reaching_the_ratio_of_flood_score() -> None:
    assert choose_k({4: 0.70, 8: 0.76, 15: 0.78}, 0.80) == (8, True)


def test_choose_k_falls_back_to_the_largest_candidate_when_none_reaches_the_target() -> None:
    assert choose_k({4: 0.1, 75: 0.5}, 0.80) == (75, False)


def _stat(hit: float, lo: float, hi: float) -> dict[str, object]:
    return {"hit@2": hit, "hit@2_ci95": (lo, hi)}


def test_s1_needs_both_the_gain_and_disjoint_intervals() -> None:
    multi, flood = _stat(0.44, 0.42, 0.46), _stat(0.80, 0.79, 0.82)
    assert judge_success(_stat(0.60, 0.58, 0.62), multi, flood, 30, 2)["s1"]
    assert not judge_success(_stat(0.60, 0.45, 0.62), multi, flood, 30, 2)["s1"]
    assert not judge_success(_stat(0.52, 0.51, 0.53), multi, flood, 30, 2)["s1"]


def test_verdict_is_adopted_partial_or_rejected() -> None:
    multi, flood = _stat(0.44, 0.42, 0.46), _stat(0.80, 0.79, 0.82)
    assert judge_success(_stat(0.77, 0.75, 0.79), multi, flood, 30, 2)["verdict"] == "採用"
    assert judge_success(_stat(0.77, 0.75, 0.79), multi, flood, 50, 2)["verdict"] == "部分的に支持"
    assert judge_success(_stat(0.46, 0.44, 0.48), multi, flood, 30, 2)["verdict"] == "棄却"


def test_regression_check_compares_k_150_with_flood_and_k_m_with_multi_centroid() -> None:
    reference = {
        "m": 2,
        "test": {"flood_score": {"hit@2": 0.8041}, "multi_centroid": {"hit@2": 0.4401}},
    }
    assert regression_check({150: 0.80412, 2: 0.44008}, 150, 2, reference)["passed"]
    assert not regression_check({150: 0.8100, 2: 0.4401}, 150, 2, reference)["passed"]


def _source(name: str, centers: list[list[float]]) -> SourceProfile:
    return SourceProfile(
        source=name,
        kind="faiss",
        description="",
        encoder="enc",
        centroid=np.zeros(2, np.float32),
        n_docs=1,
        shard_ids=(name,),
        advert=Advert(terms=[], term_weights=[], centroids=centers, card_embedding=[]),
    )


SOURCES = [
    _source("a", [[1.0, 0.0]]),
    _source("b", [[0.8, 0.2]]),
    _source("c", [[0.0, 1.0]]),
]


def _query(probe: dict[str, float] | None) -> RoutingQuery:
    return RoutingQuery("q", "", {"enc": np.asarray([1.0, 0.0], np.float32)}, probe_scores=probe)


def test_router_picks_the_best_probe_score_among_the_candidates_only() -> None:
    router = CentroidProbeRouter(m=1, probe_k=2)
    assert router.candidates(_query(None), SOURCES) == ["a", "b"]
    # c の probe が最高でも，1 段目の候補（a・b）に入らないので選ばれない
    assert router.select(_query({"a": 0.1, "b": 0.5, "c": 0.9}), SOURCES) == ["b"]


def test_router_with_k_equal_to_m_matches_multi_centroid() -> None:
    probe = {"a": 0.1, "b": 0.5, "c": 0.9}
    assert CentroidProbeRouter(m=2, probe_k=2).select(_query(probe), SOURCES) == (
        MultiCentroidRouter(2).select(_query(None), SOURCES)
    )


def test_router_requires_probe_scores_and_k_at_least_m() -> None:
    with pytest.raises(ValueError, match="probe scores"):
        CentroidProbeRouter(m=1, probe_k=2).select(_query(None), SOURCES)
    with pytest.raises(ValueError, match="at least m"):
        CentroidProbeRouter(m=3, probe_k=2)
