"""RQ-A のオフラインの評価（atrium.advert_eval）の採点と指標の仕様．"""

from __future__ import annotations

import numpy as np

from atrium.advert_eval import (
    Inbox,
    hit_at,
    owner_auc,
    ranks_of_gold,
    score_flood_bm25,
    spearman,
)


def test_rank_of_gold_counts_ties_against_the_gold_source() -> None:
    scores = np.array([[0.9, 0.5, 0.9], [0.1, 0.8, 0.2]], np.float32)
    assert ranks_of_gold(scores, [0, 1]).tolist() == [2, 1]
    assert hit_at(np.array([2, 1]), 1) == 0.5


def test_owner_auc_is_one_when_the_owner_always_scores_highest() -> None:
    scores = np.array([[0.9, 0.1, 0.2], [0.0, 0.5, 0.4]], np.float32)
    assert owner_auc(scores, [0, 1]) == 1.0
    assert owner_auc(np.zeros((1, 3), np.float32), [0]) == 0.5


def _inbox(name: str, texts: list[str]) -> Inbox:
    return Inbox(
        name,
        [f"{name}/{i}" for i in range(len(texts))],
        [""] * len(texts),
        texts,
        np.zeros((len(texts), 2), np.float32),
    )


def test_bm25_flood_finds_the_inbox_that_contains_the_query_terms() -> None:
    inboxes = [_inbox("a", ["gas pipeline contract", "lunch"]), _inbox("b", ["power trading desk"])]
    for shared in (False, True):
        scores = score_flood_bm25([["power", "desk"]], inboxes, shared)
        assert scores[0, 1] > scores[0, 0]


def test_spearman_is_one_for_monotone_and_zero_without_variation() -> None:
    assert spearman([1, 2, 3], [0.1, 0.5, 0.9]) == 1.0
    assert spearman([1, 2, 3], [0.5, 0.5, 0.5]) == 0.0
