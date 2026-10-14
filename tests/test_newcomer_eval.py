"""150 人での新参者の評価（atrium.newcomer_eval）の仕様：新参者の選び方・評価する質問・判定の規則．"""

from __future__ import annotations

import numpy as np

from atrium.newcomer_eval import (
    choose_newcomers,
    intervals_disjoint_above,
    judge,
    pooled_stats,
    ranks_for_rows,
    regression_check,
    rows_of,
)


def test_newcomers_are_a_seeded_sample_whose_prefix_does_not_depend_on_n() -> None:
    names = [f"user-{i:03d}" for i in range(150)][::-1]  # 並びは名前の昇順に直してから抽出する
    thirty = choose_newcomers(names, 30)
    assert len(set(thirty)) == 30
    assert choose_newcomers(names, 15) == thirty[:15]
    assert choose_newcomers(sorted(names), 30) == thirty


def test_evaluation_rows_are_only_the_questions_of_the_newcomer() -> None:
    gold = [0, 2, 2, 1, 2]
    assert rows_of(gold, 2) == [1, 2, 4]
    assert rows_of(gold, 2, invert=True) == [0, 3]


def test_ranks_use_only_the_selected_rows() -> None:
    scores = np.array([[0.9, 0.1], [0.2, 0.8], [0.7, 0.3]], np.float32)
    assert ranks_for_rows(scores, [0, 0, 1], [0, 1]).tolist() == [1, 2]


def _stat(lo: float, hi: float, hit: float) -> dict[str, object]:
    return {"hit@2": hit, "hit@2_ci95": (lo, hi)}


def test_judgement_requires_non_overlapping_intervals() -> None:
    pooled = {
        "ragroute-upper": _stat(0.6, 0.7, 0.65),
        "ragroute-frozen": _stat(0.3, 0.4, 0.35),
        "flood_score": _stat(0.55, 0.65, 0.6),
        "card_sim": _stat(0.1, 0.2, 0.15),
        "term_sketch": _stat(0.3, 0.45, 0.4),
        "centroid_sim": _stat(0.2, 0.3, 0.25),
        "multi_centroid": _stat(0.38, 0.5, 0.45),
    }
    j = judge(pooled)
    assert j["h_n1"] is True
    assert j["h_n2_flood"] is True
    # 公開情報を使う最良の方式（multi_centroid）の区間は，ragroute-frozen の区間と重なる
    assert j["best_public"] == "multi_centroid"
    assert j["h_n2_best_public"] is False
    assert intervals_disjoint_above(_stat(0.51, 0.6, 0.55), _stat(0.3, 0.5, 0.4)) is True
    # 区間が接するだけなら重なるとみなす
    assert intervals_disjoint_above(_stat(0.5, 0.6, 0.55), _stat(0.3, 0.5, 0.4)) is False


def test_pooled_stats_reports_hit_and_interval() -> None:
    ranks = np.array([1, 2, 3, 1], np.int64)
    stats = pooled_stats(ranks, seed=12)
    assert stats["n"] == 4
    assert stats["hit@1"] == 0.5 and stats["hit@2"] == 0.75


def test_term_sketch_regression_tolerates_the_hash_order_noise_only() -> None:
    reference = {
        "m": 2,
        "test": {
            "term_sketch": {"hit@1": 0.2929, "hit@2": 0.3588},
            "card_sim": {"hit@1": 0.112, "hit@2": 0.153},
        },
    }
    ok = {
        "term_sketch": {"hit@1": 0.2922, "hit@2": 0.3588},
        "card_sim": {"hit@1": 0.112, "hit@2": 0.153},
    }
    assert regression_check(ok, reference)["passed"] is True
    bad = {**ok, "card_sim": {"hit@1": 0.1127, "hit@2": 0.153}}
    assert regression_check(bad, reference)["passed"] is False
