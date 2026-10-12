"""RQ-C のオフラインの評価（atrium.churn_eval）の時刻表と経路の選び方の仕様．"""

from __future__ import annotations

import numpy as np
import pytest

from atrium.advert_eval import hit_at, ranks_of_gold
from atrium.churn_eval import (
    JUDGED_AVAILABILITY,
    JUDGED_METHOD,
    N_SCHEDULE_SEEDS,
    REFRESH_STEPS,
    judge_hc1,
    regression_check,
    render_report,
    route_under_churn,
    simulate_availability,
    stale_view,
    summarize_runs,
)


def test_availability_is_always_on_without_churn_and_near_the_target_with_churn() -> None:
    rng = np.random.default_rng(0)
    assert simulate_availability(3, 5, 1.0, 200, rng).all()
    online = simulate_availability(50, 20000, 0.7, 50, rng)
    assert abs(online.mean() - 0.7) < 0.03


def test_stale_view_repeats_the_state_at_the_last_refresh() -> None:
    online = np.array([[True], [False], [True], [False], [False]])
    assert (stale_view(online, 1) == online).all()
    assert stale_view(online, 2)[:, 0].tolist() == [True, True, True, True, False]


# ノード 0 に受信箱 0・1，ノード 1 に受信箱 2・3．正解は受信箱 1（スコア 2 位），受信箱 0（1 位）のノード 0 は稼働中
NODE_OF = np.array([0, 0, 1, 1], np.int64)
SCORES = np.array([[0.5, 0.8, 0.9, 0.1]], np.float32)


def test_without_churn_the_hit_equals_the_rank_based_hit() -> None:
    everyone = np.ones((1, 2), np.bool_)
    for flood in (False, True):
        out = route_under_churn(SCORES, [1], NODE_OF, everyone, everyone, 1, flood)
        assert out.hit.mean() == hit_at(ranks_of_gold(SCORES, [1]), 1)
        assert out.wasted.tolist() == [0]


def test_a_stale_entry_takes_a_slot_from_advert_routing_but_not_from_flood() -> None:
    # ノード 1（受信箱 2 がスコア 1 位）は離脱したが，メンバー表にはまだ残っている
    view = np.array([[True, True]])
    online = np.array([[True, False]])
    advert = route_under_churn(SCORES, [1], NODE_OF, view, online, 1, False)
    assert advert.hit.tolist() == [False]
    assert (advert.contacted.tolist(), advert.wasted.tolist()) == ([1], [1])
    flood = route_under_churn(SCORES, [1], NODE_OF, view, online, 1, True)
    assert flood.hit.tolist() == [True]
    assert (flood.contacted.tolist(), flood.wasted.tolist()) == ([2], [1])


def _runs(drop_by_method: dict[str, float]) -> list[dict[str, float | int | str]]:
    """R = 1 の hit を 0.5 とし，R = 最大の hit を 0.5 × (1 − 低下) とする合成の結果（全ての種で同じ値）．"""
    runs: list[dict[str, float | int | str]] = []
    for method, drop in drop_by_method.items():
        for seed in range(N_SCHEDULE_SEEDS):
            for refresh in REFRESH_STEPS:
                hit = (
                    0.5
                    if refresh == 1
                    else 0.5 * (1 - drop if refresh == max(REFRESH_STEPS) else 1)
                )
                runs.append(
                    {
                        "method": method,
                        "availability": JUDGED_AVAILABILITY,
                        "refresh": refresh,
                        "seed": seed,
                        "hit": hit,
                        "hit_given_gold_online": hit,
                        "gold_online_rate": 0.7,
                        "contacted": 2.0,
                        "wasted": 0.5,
                    }
                )
    return runs


def test_hc1_is_supported_only_when_the_judged_method_drops_more_than_flood_in_every_seed() -> None:
    supported = judge_hc1(
        _runs({JUDGED_METHOD: 0.4, "flood_score": 0.1}), [JUDGED_METHOD, "flood_score"]
    )
    assert supported["supported"] is True
    assert supported["flood_rel_drop"] == pytest.approx([0.1] * N_SCHEDULE_SEEDS)
    not_supported = judge_hc1(
        _runs({JUDGED_METHOD: 0.1, "flood_score": 0.1}), [JUDGED_METHOD, "flood_score"]
    )
    assert not_supported["supported"] is False


def test_summary_reports_the_mean_and_the_range_across_schedule_seeds() -> None:
    runs = _runs({"card_sim": 0.0})
    runs[0]["hit"] = 0.3
    by_key = {(s["method"], s["refresh"]): s for s in summarize_runs(runs)}
    hit = by_key[("card_sim", 1)]["hit"]
    assert (hit["min"], hit["max"]) == (0.3, 0.5)
    assert abs(hit["mean"] - (0.3 + 0.5 * (N_SCHEDULE_SEEDS - 1)) / N_SCHEDULE_SEEDS) < 1e-12


def test_regression_passes_within_one_question_and_fails_beyond_it() -> None:
    reference = {"m": 2, "test": {"card_sim": {"hit@2": 0.50}}}
    assert regression_check({"card_sim": 0.50 + 1 / 2940}, reference, 2, 2940)["passed"] is True
    assert regression_check({"card_sim": 0.50 + 3 / 2940}, reference, 2, 2940)["passed"] is False
    assert regression_check({"card_sim": 0.50}, {**reference, "m": 3}, 2, 2940)["passed"] is False


def test_report_names_the_regression_result_and_the_hc1_verdict() -> None:
    runs = _runs({JUDGED_METHOD: 0.4, "flood_score": 0.1})
    metrics = {
        "m": 2,
        "n_inboxes": 150,
        "n_nodes": 30,
        "n_test": 2940,
        "params": {"card_sim": 40},
        "mean_up_steps": 200,
        "n_schedule_seeds": N_SCHEDULE_SEEDS,
        "regression": {"passed": True, "diff": {JUDGED_METHOD: 0.0}},
        "summary": summarize_runs(runs),
        "hc1": judge_hc1(runs, [JUDGED_METHOD, "flood_score"]),
    }
    report = render_report(metrics)
    assert "通過" in report
    assert f"判定（{JUDGED_METHOD}）：支持" in report


def test_a_gold_inbox_on_an_offline_node_is_never_a_hit() -> None:
    view = np.array([[True, True]])
    online = np.array([[False, True]])
    for flood in (False, True):
        out = route_under_churn(SCORES, [1], NODE_OF, view, online, 2, flood)
        assert out.hit.tolist() == [False]
        assert out.gold_online.tolist() == [False]
