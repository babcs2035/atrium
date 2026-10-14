"""デバイスを単位に上位を選ぶ二段の方式 device_probe（DeviceProbeRouter・atrium.device_probe_eval）の規則の仕様．"""

from __future__ import annotations

import numpy as np
import pytest

from atrium.device_probe_eval import (
    choose_d,
    containment_violations,
    device_candidate_mask,
    device_index_of_inbox,
    device_scores,
    is_promising,
    masked_scores,
)
from atrium.manifest import Advert, NodeAssignment, Placement
from atrium.probe_reach import selected_sources
from atrium.routing import RoutingQuery, SourceProfile
from atrium.routing.advert import DeviceProbeRouter, top_devices

# 4 受信箱を 2 台に置く：ノード x が a・b，ノード y が c・d
NAMES = ["a", "b", "c", "d"]
DEVICE_OF = {"a": "x", "b": "x", "c": "y", "d": "y"}
DEVICE_INDEX = np.array([0, 0, 1, 1], np.int64)


def _source(name: str, center: float) -> SourceProfile:
    return SourceProfile(
        source=name,
        kind="faiss",
        description="",
        encoder="enc",
        centroid=np.zeros(2, np.float32),
        n_docs=1,
        shard_ids=(name,),
        advert=Advert(terms=[], term_weights=[], centroids=[[center, 0.0]], card_embedding=[]),
    )


def _query() -> RoutingQuery:
    return RoutingQuery("q", "", {"enc": np.asarray([1.0, 0.0], np.float32)})


def test_top_devices_returns_every_source_on_the_device_with_the_best_single_score() -> None:
    # x の最大は 0.6，y の最大は 0.9（y の d は低いが，同じデバイスなので候補に入る）
    assert top_devices(NAMES, [0.6, 0.5, 0.9, 0.0], DEVICE_OF, 1) == ["c", "d"]


def test_top_devices_aggregates_by_max_not_by_sum() -> None:
    # 和なら x（0.5 + 0.5）が上だが，最大値では y（0.9）が上
    assert top_devices(NAMES, [0.5, 0.5, 0.9, 0.0], DEVICE_OF, 1) == ["c", "d"]


def test_top_devices_breaks_ties_by_the_first_appearance_in_names() -> None:
    assert top_devices(NAMES, [0.5, 0.0, 0.5, 0.0], DEVICE_OF, 1) == ["a", "b"]


def test_d_at_least_the_number_of_devices_keeps_all_sources() -> None:
    assert top_devices(NAMES, [0.1, 0.2, 0.3, 0.4], DEVICE_OF, 5) == NAMES


def test_router_candidates_are_the_sources_on_the_top_d_devices() -> None:
    sources = [_source(n, c) for n, c in zip(NAMES, [0.6, 0.5, 0.9, 0.0], strict=True)]
    router = DeviceProbeRouter(m=1, probe_d=1)
    assert router.candidates(_query(), sources, DEVICE_OF) == ["c", "d"]


def test_router_selects_the_best_probe_score_among_the_candidates_only() -> None:
    router = DeviceProbeRouter(m=1, probe_d=1)
    # a の probe が最高でも，候補（c・d）に入らないので選ばれない
    assert router.select_among(["c", "d"], {"a": 0.99, "c": 0.2, "d": 0.7}) == ["d"]


def test_router_needs_the_device_map_and_a_positive_d() -> None:
    sources = [_source(n, 0.1) for n in NAMES]
    with pytest.raises(ValueError, match="device"):
        DeviceProbeRouter(m=1, probe_d=1).select(_query(), sources)
    with pytest.raises(ValueError, match="at least 1"):
        DeviceProbeRouter(m=1, probe_d=0)


def test_device_index_follows_the_first_appearance_in_the_inbox_order() -> None:
    placement = Placement(
        dataset="enronqa",
        nodes=[
            NodeAssignment(host="y", url="http://y", shard_ids=["c", "d"]),
            NodeAssignment(host="x", url="http://x", shard_ids=["a", "b"]),
        ],
    )
    assert device_index_of_inbox(NAMES, placement).tolist() == [0, 0, 1, 1]


def test_device_index_rejects_inboxes_missing_from_the_placement() -> None:
    placement = Placement(
        dataset="enronqa", nodes=[NodeAssignment(host="x", url="http://x", shard_ids=["a"])]
    )
    with pytest.raises(ValueError, match="without a node"):
        device_index_of_inbox(NAMES, placement)


FIRST = np.array([[0.6, 0.5, 0.9, 0.0], [0.5, 0.5, 0.9, 0.0]], np.float32)
SECOND = np.array([[0.1, 0.7, 0.9, 0.6], [0.5, 0.4, 0.3, 0.6]], np.float32)


def test_device_scores_take_the_max_or_the_sum_per_device() -> None:
    np.testing.assert_allclose(
        device_scores(FIRST, DEVICE_INDEX, "max"), [[0.6, 0.9], [0.5, 0.9]], rtol=1e-6
    )
    np.testing.assert_allclose(
        device_scores(FIRST, DEVICE_INDEX, "sum"), [[1.1, 0.9], [1.0, 0.9]], rtol=1e-6
    )


def test_candidate_mask_marks_all_inboxes_of_the_top_devices() -> None:
    assert device_candidate_mask(FIRST, DEVICE_INDEX, 1, "max").tolist() == [
        [False, False, True, True],
        [False, False, True, True],
    ]
    assert device_candidate_mask(FIRST, DEVICE_INDEX, 1, "sum").tolist() == [
        [True, True, False, False],
        [True, True, False, False],
    ]


def test_offline_selection_matches_the_router() -> None:
    offline = selected_sources(
        masked_scores(SECOND, device_candidate_mask(FIRST, DEVICE_INDEX, 1, "max")), NAMES, 1
    )
    sources = [_source(n, float(c)) for n, c in zip(NAMES, FIRST[0], strict=True)]
    router = DeviceProbeRouter(m=1, probe_d=1)
    candidates = router.candidates(_query(), sources, DEVICE_OF)
    probe = dict(zip(NAMES, SECOND[0].tolist(), strict=True))
    assert router.select_among(candidates, probe) == offline[0]


def test_max_aggregation_contains_the_top_k_whenever_they_fit_on_d_devices() -> None:
    rng = np.random.default_rng(0)
    first = rng.normal(size=(50, 12)).astype(np.float32)
    device_of_inbox = np.repeat(np.arange(4, dtype=np.int64), 3)
    for d in (1, 2, 3, 4):
        for k in (1, 2, 4, 6):
            assert containment_violations(first, device_of_inbox, d, k) == 0


def test_choose_d_takes_the_smallest_d_reaching_the_target() -> None:
    assert choose_d({2: 0.5, 4: 0.7, 6: 0.8}, 0.7) == 4
    assert choose_d({2: 0.5, 4: 0.6}, 0.7) is None


def test_promising_needs_requests_at_most_the_ratio_of_centroid_probe() -> None:
    # centroid_probe K = 75 の届くデバイス 29.1 台：(29.1 + 2) × 0.9 = 27.99 → D ≤ 25
    assert is_promising(24, 29.1, 2)
    assert is_promising(25, 29.1, 2)
    assert not is_promising(26, 29.1, 2)
