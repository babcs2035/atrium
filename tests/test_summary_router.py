"""term_probe・hybrid_probe（p0006 P4）の仕様：質問者の選択が，オフラインの評価（frontier_methods）と同じになる．"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import pytest

from atrium.arrays import F32Array
from atrium.frontier_core import FrontierData
from atrium.frontier_methods import (
    cori_scores,
    cori_weights,
    device_first_stage,
    source_centers,
    term_index,
    top_devices,
    zscore_rows,
)
from atrium.routing import RoutingQuery, SourceProfile
from atrium.routing.summary import SummaryArtifact, SummaryProbeRouter, load_summary, write_summary
from tests.test_frontier import DIM, _data

ENCODER = "enc"


def _artifact(tmp_path: Path) -> tuple[FrontierData, Any, Any, SummaryArtifact]:
    data = _data()
    weights, cf = cori_weights(data, 100)
    centers = source_centers(data, "body")
    groups = [
        [n for n, d in zip(data.names, data.device_of_source.tolist(), strict=True) if d == dev]
        for dev in range(data.n_devices)
    ]
    write_summary(
        tmp_path, data.names, groups, term_index(data)["vocab"], weights, cf, centers, 100
    )
    return data, weights, cf, load_summary(tmp_path)


def _profiles(names: list[str]) -> list[SourceProfile]:
    centroid: F32Array = np.zeros(DIM, dtype=np.float32)
    return [SourceProfile(n, "faiss", "", ENCODER, centroid, 1, (n,)) for n in names]


def _device_urls(data: FrontierData) -> dict[str, str]:
    return {
        n: f"http://node{d}"
        for n, d in zip(data.names, data.device_of_source.tolist(), strict=True)
    }


@pytest.mark.parametrize("mode", ["term", "hybrid"])
def test_router_candidates_match_the_offline_top_devices(
    tmp_path: Path, mode: Literal["term", "hybrid"]
) -> None:
    data, weights, cf, artifact = _artifact(tmp_path)
    router = SummaryProbeRouter(mode, 2, 2, artifact, ENCODER, 0.5)
    for q in range(4):
        query = RoutingQuery(
            f"q{q}", "", {ENCODER: data.q_emb["test"][q]}, tokens=tuple(data.q_tokens["test"][q])
        )
        got = router.candidates(query, _profiles(data.names), _device_urls(data))
        terms = cori_scores(term_index(data)["vocab"], weights, cf, [data.q_tokens["test"][q]])
        if mode == "term":
            scores = terms
        else:
            centroid = device_first_stage(data, "test", "body")[q : q + 1]
            scores = zscore_rows(terms) + 0.5 * zscore_rows(centroid)
        mask = top_devices(scores, 2, data.device_of_source)[0]
        assert got == [n for n, keep in zip(data.names, mask.tolist(), strict=True) if keep]


def test_select_among_takes_the_best_probe_scores_among_the_candidates(tmp_path: Path) -> None:
    _, _, _, artifact = _artifact(tmp_path)
    router = SummaryProbeRouter("term", 2, 2, artifact, ENCODER, 0.5)
    assert router.select_among(["a", "b", "c"], {"a": 0.1, "b": 0.9, "c": 0.5}) == ["b", "c"]


def test_a_summary_built_for_another_placement_is_rejected(tmp_path: Path) -> None:
    data, _, _, artifact = _artifact(tmp_path)
    router = SummaryProbeRouter("hybrid", 2, 2, artifact, ENCODER, 0.5)
    one_device = {n: "http://same" for n in data.names}
    query = RoutingQuery("q", "", {ENCODER: np.zeros(DIM, np.float32)}, tokens=("word0",))
    with pytest.raises(ValueError, match="different placement"):
        router.candidates(query, _profiles(data.names), one_device)
