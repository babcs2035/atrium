"""参加の通知の遅れ L ごとの評価（atrium.churn_join_eval）のメンバー表・区分の遷移・ゲート・判定の仕様．"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from atrium.churn_eval import (
    JUDGED_AVAILABILITY,
    N_SCHEDULE_SEEDS,
    REFRESH_STEPS,
    BoolArray,
    route_under_churn,
    simulate_availability,
    stale_view,
)
from atrium.churn_join_eval import (
    DELAYS,
    HJ1_DELAY,
    HJ1_METHOD,
    JOIN_DELAYS,
    LOSS_CLASSES,
    gate_g2,
    gate_g3,
    join_notified_view,
    judge_hj1,
    recovery_by_method,
    transition_table,
)

N_NODES = 6
N_STEPS = 600


def _schedule(seed: int) -> BoolArray:
    """乱数の時刻表（稼働率 0.6，稼働中の平均 8 刻み．参加が頻繁に起きる）．"""
    return simulate_availability(N_NODES, N_STEPS, 0.6, 8, np.random.default_rng(seed))


@pytest.mark.parametrize("refresh", [1, 7, 50])
def test_without_notice_the_view_is_the_stale_view(refresh: int) -> None:
    online = _schedule(0)
    assert (join_notified_view(online, refresh, None) == stale_view(online, refresh)).all()


@pytest.mark.parametrize(("refresh", "delay"), [(1, 1), (1, 5), (7, 7), (7, 30), (50, 50)])
def test_a_notice_slower_than_the_refresh_changes_nothing(refresh: int, delay: int) -> None:
    online = _schedule(1)
    assert (join_notified_view(online, refresh, delay) == stale_view(online, refresh)).all()


@pytest.mark.parametrize("refresh", [10, 100])
def test_a_shorter_delay_never_hides_a_node_that_a_longer_delay_shows(refresh: int) -> None:
    online = _schedule(2)
    views = [join_notified_view(online, refresh, d) for d in (0, 1, 3, 9, 30)]
    stale = stale_view(online, refresh)
    for shorter, longer in zip(views, views[1:], strict=False):
        assert (longer <= shorter).all()
    assert (stale <= views[-1]).all()
    # 参加が頻繁なので，L = 0 では通知の無い表より実際に広がる
    assert views[0].sum() > stale.sum()


@pytest.mark.parametrize("refresh", [1, 10, 100])
def test_with_an_immediate_notice_every_online_node_is_visible(refresh: int) -> None:
    online = _schedule(3)
    assert (online <= join_notified_view(online, refresh, 0)).all()


def test_a_hand_made_schedule_matches_the_expected_view() -> None:
    # ノード 0 は刻み 1・5・7 で参加し，刻み 3・6 で離脱する．ノード 1 は常に稼働中
    online = np.array(
        [
            [False, True],
            [True, True],
            [True, True],
            [False, True],
            [False, True],
            [True, True],
            [False, True],
            [True, True],
        ]
    )
    # 更新は刻み 0・4．どちらの時点でもノード 0 は停止中なので，通知が無ければ常に見えない
    assert join_notified_view(online, 4, None)[:, 0].tolist() == [False] * 8
    # L = 1：刻み 1 の参加は刻み 2 から，刻み 5 の参加は刻み 6 から見える．刻み 3・7 は離脱しても次の更新まで残る
    expected_l1 = [False, False, True, True, False, False, True, True]
    assert join_notified_view(online, 4, 1)[:, 0].tolist() == expected_l1
    # L = 0：参加した刻みから見える．刻み 4 の更新で離脱済みのノード 0 は表から消える
    expected_l0 = [False, True, True, True, False, True, True, True]
    assert join_notified_view(online, 4, 0)[:, 0].tolist() == expected_l0
    assert join_notified_view(online, 4, 0)[:, 1].all()


def test_a_negative_delay_is_rejected() -> None:
    with pytest.raises(ValueError):
        join_notified_view(np.ones((3, 1), np.bool_), 2, -1)


def test_flood_with_an_immediate_notice_reproduces_the_fresh_view() -> None:
    # G3 の性質：L = 0 では返事のある候補が稼働中の全ノードになるので，flood_score の hit は R = 1 と同じ
    rng = np.random.default_rng(4)
    online = _schedule(4)
    node_of = np.repeat(np.arange(N_NODES), 2).astype(np.int64)
    scores = rng.random((N_STEPS, len(node_of))).astype(np.float32)
    gold = rng.integers(0, len(node_of), N_STEPS).tolist()
    fresh = route_under_churn(scores, gold, node_of, stale_view(online, 1), online, 2, True)
    for refresh in (10, 100):
        view = join_notified_view(online, refresh, 0)
        out = route_under_churn(scores, gold, node_of, view, online, 2, True)
        assert out.hit.tolist() == fresh.hit.tolist()


def test_the_transition_table_counts_every_question_once() -> None:
    before = np.array([0, 0, 1, 2, 2, 3, 4], np.int64)
    after = np.array([0, 2, 1, 2, 0, 3, 4], np.int64)
    table = transition_table(before, after)
    assert len(table) == len(LOSS_CLASSES) and all(len(row) == len(LOSS_CLASSES) for row in table)
    assert sum(map(sum, table)) == len(before)
    assert table[0] == [1, 0, 1, 0, 0]
    assert table[2][0] == 1


def _record(
    method: str, refresh: int, delay: int | None, seed: int, n_hit: int, availability: float = 0.7
) -> dict[str, Any]:
    """ゲート・判定の入力の最小限の実行の記録（件数は n_hit だけを変える）．"""
    return {
        "method": method,
        "availability": availability,
        "refresh": refresh,
        "seed": seed,
        "delay": delay,
        "hit": n_hit / 100,
        "hit_given_gold_online": 0.0,
        "gold_online_rate": 0.0,
        "contacted": 1.0,
        "wasted": 0.0,
        "n": 100,
        "n_hit": n_hit,
        "n_gold_offline": 0,
        "n_gold_invisible": 0,
        "n_not_selected": 100 - n_hit,
        "n_not_candidate": None,
        "n_not_top_m": None,
        "n_slot_taken": 0,
    }


def test_g2_fails_when_a_slow_notice_changes_a_run() -> None:
    runs = [_record("x", r, d, 0, 50) for r in REFRESH_STEPS for d in DELAYS]
    assert gate_g2(runs)["passed"] is True
    changed = next(r for r in runs if r["refresh"] == 10 and r["delay"] == 100)
    changed["n_hit"] = 51
    result = gate_g2(runs)
    assert result["passed"] is False
    assert result["mismatches"][0]["fields"] == ["n_hit"]


def test_g3_fails_when_flood_with_an_immediate_notice_differs_from_the_fresh_view() -> None:
    runs = [_record("flood_score", r, d, 0, 60) for r in REFRESH_STEPS for d in DELAYS]
    assert gate_g3(runs)["passed"] is True
    next(r for r in runs if r["refresh"] == 1000 and r["delay"] == 0)["n_hit"] = 59
    assert gate_g3(runs)["passed"] is False


def _recovery_runs(notified: list[int], fresh: int = 60, stale: int = 40) -> list[dict[str, Any]]:
    """HJ1_METHOD の a = 0.7 の合成の結果．R = 1 は fresh，R = 最大は L = ∞ で stale，L = HJ1_DELAY で notified[種] 問の hit．"""
    runs = []
    for s in range(N_SCHEDULE_SEEDS):
        for refresh in REFRESH_STEPS:
            for delay in DELAYS:
                if refresh == min(REFRESH_STEPS):
                    n_hit = fresh
                elif refresh != max(REFRESH_STEPS) or delay is None:
                    n_hit = stale
                else:
                    n_hit = notified[s] if delay == HJ1_DELAY else stale
                runs.append(_record(HJ1_METHOD, refresh, delay, s, n_hit, JUDGED_AVAILABILITY))
    return runs


def test_hj1_needs_half_of_the_drop_back_in_every_seed() -> None:
    supported = judge_hj1(recovery_by_method(_recovery_runs([50] * 5), [HJ1_METHOD]), True)
    assert supported["rho"] == pytest.approx([0.5] * 5)
    assert supported["verdict"] == "支持"
    one_low = judge_hj1(
        recovery_by_method(_recovery_runs([50, 50, 50, 50, 49]), [HJ1_METHOD]), True
    )
    assert one_low["supported"] is False
    assert (
        judge_hj1(recovery_by_method(_recovery_runs([60] * 5), [HJ1_METHOD]), False)["verdict"]
        == "退行ゲートを通らないため判定しない"
    )


def test_hj1_treats_a_seed_without_a_drop_as_not_supported() -> None:
    recovery = recovery_by_method(_recovery_runs([40] * 5, fresh=40, stale=40), [HJ1_METHOD])
    out = judge_hj1(recovery, True)
    assert out["rho"] == [None] * 5
    assert out["supported"] is False


def test_the_registered_delays_are_the_lever_values() -> None:
    assert JOIN_DELAYS == (0, 1, 10, 100)
    assert DELAYS[0] is None
