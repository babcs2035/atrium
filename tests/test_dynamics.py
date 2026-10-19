"""p0007 I5〜I7 の純粋な計算（選択の再現性・転送のスケジュール・時間の集計・時間積分・検定・判定）の仕様．"""

from __future__ import annotations

import math

import pytest

from atrium.dynamics import (
    JOIN_EQUIVALENCE_RATIO,
    binned_rates,
    central_flush_offsets,
    central_rate_integral,
    choose_held_back,
    choose_names,
    durations_or_censored,
    eligible_indices,
    fill_forward,
    integrate_rates,
    judge_failure,
    judge_freshness,
    judge_join,
    parse_interval,
    sign_test_less,
    summarize_times,
    times_or_censored,
)

# ── 選択 ──


def test_held_back_is_ten_percent_of_distinct_docs_and_reproducible_with_the_seed() -> None:
    docs = [f"d{i}" for i in range(100)] + ["d0", "d1"]  # 重複は数えない
    first = choose_held_back(docs, 0.10, 12)
    assert len(first) == 10
    assert len(set(first)) == 10
    assert first == choose_held_back(docs, 0.10, 12)
    assert first != choose_held_back(docs, 0.10, 13)


def test_held_back_does_not_depend_on_the_input_order() -> None:
    docs = [f"d{i}" for i in range(50)]
    assert choose_held_back(docs, 0.2, 12) == choose_held_back(list(reversed(docs)), 0.2, 12)


def test_held_back_rejects_a_fraction_outside_the_range() -> None:
    with pytest.raises(ValueError, match="fraction"):
        choose_held_back(["a"], 0.0)


def test_chosen_names_are_a_reproducible_sorted_subset_of_the_given_size() -> None:
    names = [f"host-{i:02d}" for i in range(30)]
    for k in (1, 3, 6, 10):
        chosen = choose_names(names, k, 12)
        assert len(chosen) == k
        assert chosen == sorted(chosen)
        assert set(chosen) <= set(names)
        assert chosen == choose_names(list(reversed(names)), k, 12)


def test_chosen_names_cannot_exceed_the_population() -> None:
    with pytest.raises(ValueError, match="cannot choose"):
        choose_names(["a", "b"], 3)


# ── スケジュール ──


def test_flush_offsets_for_zero_interval_are_the_arrival_times() -> None:
    assert central_flush_offsets([0.0, 0.5, 1.2], 0.0) == [0.0, 0.5, 1.2]


def test_flush_offsets_round_up_to_the_next_multiple_of_the_interval() -> None:
    assert central_flush_offsets([0.0, 0.5, 59.9, 60.0, 60.1, 130.0], 60.0) == [
        0.0,
        60.0,
        60.0,
        60.0,
        120.0,
        180.0,
    ]


def test_flush_offsets_for_infinite_interval_never_flush() -> None:
    assert central_flush_offsets([0.0, 5.0], math.inf) == [math.inf, math.inf]


def test_flush_offsets_reject_a_negative_interval() -> None:
    with pytest.raises(ValueError, match="interval_s"):
        central_flush_offsets([0.0], -1.0)


def test_interval_parsing_accepts_inf_and_numbers() -> None:
    assert [parse_interval(t) for t in ("0", "60", "600", "inf")] == [0.0, 60.0, 600.0, math.inf]


# ── 集計 ──


def test_time_to_answer_is_the_difference_and_censored_when_never_answered() -> None:
    assert times_or_censored([1.0, 2.0, 3.0], [1.5, None, 10.0]) == [0.5, math.inf, 7.0]
    assert durations_or_censored([0.2, None]) == [0.2, math.inf]


def test_summary_counts_censored_items_and_reports_null_for_infinite_statistics() -> None:
    summary = summarize_times([0.1, 0.2, 0.3, math.inf])
    assert summary["n"] == 4
    assert summary["n_censored"] == 1
    assert summary["p50"] == pytest.approx(0.3)
    assert summary["p95"] is None  # 打ち切りが p95 を占めるので有限の値は無い
    assert summarize_times([])["p50"] is None


def test_sign_test_counts_only_untied_pairs_and_uses_the_exact_binomial_tail() -> None:
    result = sign_test_less([1, 1, 1, 1, 5], [2, 2, 2, 2, 5])
    assert (result["n_less"], result["n_greater"], result["n_tied"]) == (4, 0, 1)
    assert result["p"] == pytest.approx(1 / 16)
    assert sign_test_less([2, 2], [1, 1])["p"] == 1.0
    assert sign_test_less([1.0], [math.inf])["n_less"] == 1


# ── 時間積分 ──


def test_binned_rates_average_each_interval_and_leave_empty_ones_missing() -> None:
    rates = binned_rates([0.5, 1.5, 1.7, 5.2], [True, True, False, True], 0.0, 6.0, 2.0)
    assert rates == [pytest.approx(2 / 3), None, 1.0]
    assert fill_forward(rates, 0.0) == [pytest.approx(2 / 3), pytest.approx(2 / 3), 1.0]
    assert fill_forward([None, 0.5], initial=0.25) == [0.25, 0.5]


def test_step_integral_is_rate_times_overlap_with_the_window() -> None:
    rates = [1.0, 0.5, 0.0]  # [0,2) 1.0 / [2,4) 0.5 / [4,6) 0
    assert integrate_rates(rates, 0.0, 2.0, 0.0, 6.0) == pytest.approx(3.0)
    assert integrate_rates(rates, 0.0, 2.0, 1.0, 3.0) == pytest.approx(1.5)
    assert integrate_rates(rates, 0.0, 2.0, 5.0, 100.0) == pytest.approx(0.0)  # 区間の外は 0


def test_central_integral_is_zero_until_the_restart_finishes() -> None:
    assert central_rate_integral(restart_s=30.0, rate_after=0.8, window_s=100.0) == pytest.approx(
        56.0
    )
    assert central_rate_integral(restart_s=120.0, rate_after=0.8, window_s=100.0) == 0.0


# ── 判定 ──


def test_freshness_holds_when_p50_is_under_one_second_and_central_is_significantly_slower() -> None:
    p2p = [0.4] * 20
    central = {0.0: [0.5] * 20, 60.0: [30.0] * 20, 600.0: [200.0] * 20, math.inf: [math.inf] * 20}
    verdict = judge_freshness(p2p, central)
    assert verdict["holds"] is True
    assert set(verdict["comparisons"]) == {"60", "600", "inf"}  # T < 60 秒は比べない


def test_freshness_fails_when_p2p_p50_reaches_one_second() -> None:
    verdict = judge_freshness([1.0] * 20, {60.0: [30.0] * 20})
    assert verdict["p50_below_limit"] is False
    assert verdict["holds"] is False


def test_freshness_fails_when_the_difference_is_not_significant() -> None:
    central = {60.0: [0.3, 0.3, 0.3, 0.9, 0.9, 0.9]}  # 3 組だけ P2P が短い
    verdict = judge_freshness([0.5] * 6, central)
    assert verdict["comparisons"]["60"]["significantly_shorter"] is False
    assert verdict["holds"] is False


def test_freshness_is_undecided_without_a_central_interval_of_a_minute_or_more() -> None:
    assert judge_freshness([0.1], {0.0: [1.0]})["holds"] is None


def test_failure_holds_only_if_p2p_integral_exceeds_central_for_every_k() -> None:
    rows = [
        {"k": 1, "p2p_integral": 50.0, "central_integral": 10.0},
        {"k": 3, "p2p_integral": 40.0, "central_integral": 10.0},
    ]
    assert judge_failure(rows)["holds"] is True
    rows.append({"k": 10, "p2p_integral": 5.0, "central_integral": 10.0})
    assert judge_failure(rows)["holds"] is False
    assert judge_failure([])["holds"] is None


def test_join_holds_when_p2p_is_faster() -> None:
    verdict = judge_join([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], p2p_bytes=10, central_bytes=1)
    assert verdict["branch"] == "faster"
    assert verdict["holds"] is True


def test_join_holds_when_equivalent_in_time_and_smaller_in_transfer() -> None:
    central = [10.0, 10.0, 10.0]
    p2p = [10.0 * JOIN_EQUIVALENCE_RATIO] * 3
    assert judge_join(p2p, central, p2p_bytes=1, central_bytes=100)["branch"] == (
        "equivalent_less_transfer"
    )
    assert judge_join(p2p, central, p2p_bytes=100, central_bytes=1)["holds"] is False


def test_join_fails_when_p2p_is_slower_than_the_equivalence_ratio() -> None:
    verdict = judge_join([13.0] * 3, [10.0] * 3, p2p_bytes=1, central_bytes=100)
    assert verdict["equivalent"] is False
    assert verdict["holds"] is False


def test_join_treats_a_never_answered_p2p_as_not_faster() -> None:
    verdict = judge_join([math.inf] * 3, [10.0] * 3, p2p_bytes=1, central_bytes=100)
    assert verdict["holds"] is False


def test_only_mails_answerable_in_p2p_and_in_some_finite_central_run_are_compared() -> None:
    p2p = [0.1, 0.2, None, 0.4]
    central = {0.0: [0.0, None, 0.1, None], 60.0: [30.0, None, 20.0, 40.0], math.inf: [None] * 4}
    # 1 番目は両方で答えられる／2 番目は中央が最後まで答えられない／3 番目は P2P が答えられない／4 番目は T = 60 で答えられる
    assert eligible_indices(p2p, central) == [0, 3]
    assert eligible_indices(p2p, {math.inf: [None] * 4}) == [
        0,
        1,
        3,
    ]  # 有限の T が無ければ P2P だけで決める
