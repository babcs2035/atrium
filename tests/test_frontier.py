"""frontier-eval（p0006 P2）の仕様：要求数の勘定・同点の扱い・各方式の選択の規則．合成データだけを使う．"""

from __future__ import annotations

import numpy as np

from atrium.arrays import F32Array
from atrium.frontier_core import (
    FrontierData,
    Selection,
    evaluate_selection,
    pessimistic_ranks,
    top_k_mask,
)
from atrium.frontier_methods import (
    adaptive_device_mask,
    build_device_probe,
    build_flood,
    build_hybrid_summary,
    build_multi_centroid,
    cori_scores,
    cori_weights,
    make_groups,
    neighbor_table,
    sample_device_scores,
    zscore_rows,
    walk_visited,
)

DIM = 4


def _data(n_q: int = 4) -> FrontierData:
    """6 受信箱・3 デバイス（受信箱 2 個ずつ）．質問 i の正解は受信箱 i．"""
    rng = np.random.default_rng(0)
    names = [f"inbox{i}" for i in range(6)]
    device = np.array([0, 0, 1, 1, 2, 2])
    second = np.full((n_q, 6), 0.1, dtype=np.float32)
    rel = np.zeros((n_q, 6), dtype=bool)
    for q in range(n_q):
        second[q, q] = 0.9
        rel[q, q] = True
    emb = [rng.normal(size=(8, DIM)).astype(np.float32) for _ in names]
    tokens = [[[f"word{i}", "common"] for _ in range(8)] for i in range(6)]
    return FrontierData(
        dataset="test",
        placement="n3",
        names=names,
        device_of_source=device,
        m=2,
        target_hit=0.5,
        second={"dev": second, "test": second},
        relevant={"dev": rel, "test": rel},
        q_emb={"dev": rng.normal(size=(n_q, DIM)).astype(np.float32)}
        | {"test": rng.normal(size=(n_q, DIM)).astype(np.float32)},
        q_tokens={s: [[f"word{q}"] for q in range(n_q)] for s in ("dev", "test")},
        subsets={"dev": {}, "test": {}},
        seed=12,
        summary_emb=emb,
        summary_tokens=tokens,
        full_centers=[e[:2] for e in emb],
    )


def test_flood_score_asks_every_device_plus_the_retrievals_and_finds_the_gold_inbox() -> None:
    data = _data()
    (config,) = build_flood(data)
    result = evaluate_selection(data, config.select("test"), "test")
    assert result.requests.tolist() == [3 + 2] * 4
    assert result.hit1.tolist() == [1.0] * 4
    assert result.round_trips.tolist() == [2.0] * 4


def test_multi_centroid_selects_directly_without_probing() -> None:
    data = _data()
    (config,) = build_multi_centroid(data)
    result = evaluate_selection(data, config.select("test"), "test")
    assert result.requests.tolist() == [2.0] * 4
    assert result.round_trips.tolist() == [1.0] * 4


def test_device_probe_counts_only_the_probed_devices() -> None:
    data = _data()
    by_d = {c.params["D"]: c for c in build_device_probe(data)}
    result = evaluate_selection(data, by_d[1].select("test"), "test")
    assert result.requests.tolist() == [1 + 2] * 4


def test_ties_count_against_the_gold_source() -> None:
    scores = np.array([[0.5, 0.5, 0.1]], dtype=np.float32)
    assert pessimistic_ranks(scores).tolist() == [[2, 2, 3]]


def test_unprobed_gold_is_a_miss_even_when_fewer_than_m_sources_are_probed() -> None:
    data = _data()
    mask = np.zeros((4, 6), dtype=bool)
    mask[:, 5] = True  # 正解でない受信箱 1 個だけを probe する
    result = evaluate_selection(data, Selection(candidates=mask), "test")
    assert result.hit_m.tolist() == [0.0, 0.0, 0.0, 0.0]


def test_score_gap_keeps_devices_within_the_gap_and_at_least_one() -> None:
    scores = np.array([[0.90, 0.89, 0.50, 0.10], [0.20, 0.19, 0.18, 0.17]], dtype=np.float32)
    mask = adaptive_device_mask(scores, "score_gap", 0.02, dmax=3)
    assert mask.tolist() == [[True, True, False, False], [True, True, True, False]]


def test_softmax_mass_stops_when_the_mass_is_reached_and_respects_dmax() -> None:
    sharp = np.array([[10.0, 0.0, 0.0, 0.0]], dtype=np.float32)
    flat = np.zeros((1, 4), dtype=np.float32)
    assert adaptive_device_mask(sharp, "softmax_mass", 0.9, dmax=4, tau=1.0).sum() == 1
    assert adaptive_device_mask(flat, "softmax_mass", 0.9, dmax=2, tau=1.0).sum() == 2


def test_greedy_walk_never_revisits_a_device_and_stays_within_the_budget() -> None:
    rng = np.random.default_rng(1)
    scores = rng.random((50, 10)).astype(np.float32)
    table = neighbor_table(10, 3, seed=0)
    visited, longest = walk_visited(scores, table, ttl=4, walkers=2, mode="greedy", seed=0)
    assert visited.sum(axis=1).max() <= 1 + 4 * 2
    assert longest.max() <= 4
    assert visited.any(axis=1).all()


def test_neighbor_table_excludes_the_device_itself() -> None:
    table = neighbor_table(8, 3, seed=0)
    assert all(dev not in table[dev] for dev in range(8))


def test_cori_ranks_the_device_that_publishes_the_query_term_first() -> None:
    data = _data()
    weights, cf = cori_weights(data, 10)
    scores = cori_scores(data, weights, cf, [["word2"]])
    assert int(np.argmax(scores[0])) == 1  # 受信箱 2 のデバイスは 1


def test_vote_scorer_prefers_the_device_owning_the_top_ranked_samples() -> None:
    samples = np.eye(4, dtype=np.float32)
    owner = np.array([0, 0, 1, 1])
    query = np.array([[0.0, 0.0, 1.0, 0.1]], dtype=np.float32)
    scores = sample_device_scores(query, samples, owner, 2, "vote")
    assert int(np.argmax(scores[0])) == 1


def test_random_groups_are_balanced_and_cover_every_device() -> None:
    data = _data()
    groups = make_groups(data, 2, "random")
    assert sorted(np.bincount(groups).tolist()) == [1, 2]


def test_top_k_mask_breaks_ties_by_the_smaller_column() -> None:
    scores: F32Array = np.array([[0.5, 0.5, 0.5]], dtype=np.float32)
    assert top_k_mask(scores, 2).tolist() == [[True, True, False]]


def test_zscore_rows_standardizes_each_query_across_devices_and_survives_constant_rows() -> None:
    scores = np.array([[1.0, 2.0, 3.0], [5.0, 5.0, 5.0]], dtype=np.float32)
    z = zscore_rows(scores)
    assert abs(float(z[0].mean())) < 1e-6 and abs(float(z[0].std()) - 1.0) < 1e-3
    assert z[1].tolist() == [0.0, 0.0, 0.0]


def test_hybrid_summary_with_a_large_weight_follows_the_centroid_ranking() -> None:
    data = _data()
    configs = {(c.params["V"], c.params["w"], c.params["D"]): c for c in build_hybrid_summary(data)}
    result = evaluate_selection(data, configs[(16000, 2.0, 1)].select("test"), "test")
    assert result.requests.tolist() == [1 + 2] * 4
