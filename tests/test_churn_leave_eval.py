"""離脱の通知の遅れ L_leave ごとの評価（atrium.churn_leave_eval）のメンバー表・ゲート・判定の仕様．"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from atrium.churn_eval import (
    AVAILABILITIES,
    JUDGED_AVAILABILITY,
    N_SCHEDULE_SEEDS,
    REFRESH_STEPS,
    BoolArray,
    route_under_churn,
    simulate_availability,
)
from atrium.churn_join_eval import join_notified_view
from atrium.churn_leave_eval import (
    HL1_LEAVE_DELAY,
    HL1_METHOD,
    HL1_MIN_RECOVERY,
    JOIN_DELAY_GRID,
    LEAVE_DELAY_GRID,
    LEAVE_DELAYS,
    gate_g3,
    gate_g5,
    gate_join_reference,
    judge_hl1,
    leave_notified_view,
    recovery_by_method,
)

N_NODES = 6
N_STEPS = 600


def _schedule(seed: int) -> BoolArray:
    """乱数の時刻表（稼働率 0.6，稼働中の平均 8 刻み．参加と離脱が頻繁に起きる）．"""
    return simulate_availability(N_NODES, N_STEPS, 0.6, 8, np.random.default_rng(seed))


# ── メンバー表 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("refresh", [1, 7, 50])
@pytest.mark.parametrize("join_delay", [None, 0, 3, 100])
def test_without_a_leave_notice_the_view_is_the_join_notified_view(
    refresh: int, join_delay: int | None
) -> None:
    # G1・G2 の構造上の根拠：L_leave = ∞ は Iteration 16 の表と同じ
    online = _schedule(0)
    expected = join_notified_view(online, refresh, join_delay)
    assert (leave_notified_view(online, refresh, join_delay, None) == expected).all()


@pytest.mark.parametrize(
    ("refresh", "leave_delay"), [(1, 0), (1, 10), (7, 7), (7, 30), (50, 50), (50, 100)]
)
@pytest.mark.parametrize("join_delay", [0, 100])
def test_a_leave_notice_slower_than_the_refresh_changes_nothing(
    refresh: int, leave_delay: int, join_delay: int
) -> None:
    # G3 の構造上の根拠：L_leave ≥ R では次の更新の方が先に来る
    online = _schedule(1)
    expected = leave_notified_view(online, refresh, join_delay, None)
    assert (leave_notified_view(online, refresh, join_delay, leave_delay) == expected).all()


@pytest.mark.parametrize("refresh", [1, 10, 100])
def test_immediate_join_and_leave_notices_give_the_online_set(refresh: int) -> None:
    # G5 の構造上の根拠：(L_join, L_leave) = (0, 0) では表が稼働中の集合そのものになる
    online = _schedule(2)
    assert (leave_notified_view(online, refresh, 0, 0) == online).all()


@pytest.mark.parametrize("refresh", [10, 100])
@pytest.mark.parametrize("join_delay", [0, 100])
def test_a_shorter_leave_delay_never_shows_a_node_that_a_longer_one_hides(
    refresh: int, join_delay: int
) -> None:
    online = _schedule(3)
    views = [leave_notified_view(online, refresh, join_delay, d) for d in (0, 1, 3, 9, 30, None)]
    for shorter, longer in zip(views, views[1:], strict=False):
        assert (shorter <= longer).all()
    # 離脱が頻繁なので，L_leave = 0 では通知の無い表より実際に狭まる
    assert views[0].sum() < views[-1].sum()


def test_a_hand_made_schedule_follows_the_latest_delivered_event() -> None:
    # ノード 0 は刻み 2・6 で離脱し，刻み 4・7 で再び参加する．ノード 1 は常に稼働中．更新は刻み 0 だけ（R = 8）
    up = [True, True, False, False, True, True, False, True]
    online = np.array([[u, True] for u in up])
    # 通知なしでは刻み 0 の状態（稼働中）のまま
    assert leave_notified_view(online, 8, 0, None)[:, 0].all()
    # (0, 0)：稼働状態そのもの
    assert leave_notified_view(online, 8, 0, 0)[:, 0].tolist() == up
    # (0, 1)：刻み 2 の離脱は刻み 3 に届いて消える．刻み 4 の再参加は参加の方が新しいので刻み 4 から見える（B47 (d)）．
    # 刻み 6 の離脱は刻み 7 に届くが，同じ刻み 7 に届いた再参加の方が新しい
    expected_01 = [True, True, True, False, True, True, True, True]
    assert leave_notified_view(online, 8, 0, 1)[:, 0].tolist() == expected_01
    # (1, 0)：再参加は参加の通知が届く刻み 5 から見える．刻み 7 の再参加はまだ届かないので，刻み 6 の離脱で消えたまま
    expected_10 = [True, True, False, False, False, True, False, False]
    assert leave_notified_view(online, 8, 1, 0)[:, 0].tolist() == expected_10
    assert leave_notified_view(online, 8, 1, 0)[:, 1].all()


@pytest.mark.parametrize(("join_delay", "leave_delay"), [(-1, None), (0, -1)])
def test_a_negative_delay_is_rejected(join_delay: int, leave_delay: int | None) -> None:
    with pytest.raises(ValueError):
        leave_notified_view(np.ones((3, 1), np.bool_), 2, join_delay, leave_delay)


def test_immediate_notices_leave_no_wasted_contact() -> None:
    # 離脱の通知が直ちに届けば，離脱したノードへ問い合わせない
    rng = np.random.default_rng(4)
    online = _schedule(4)
    node_of = np.repeat(np.arange(N_NODES), 2).astype(np.int64)
    scores = rng.random((N_STEPS, len(node_of))).astype(np.float32)
    gold = rng.integers(0, len(node_of), N_STEPS).tolist()
    stale = route_under_churn(
        scores, gold, node_of, leave_notified_view(online, 100, 0, None), online, 2, False
    )
    fresh = route_under_churn(
        scores, gold, node_of, leave_notified_view(online, 100, 0, 0), online, 2, False
    )
    assert stale.wasted.sum() > 0
    assert fresh.wasted.sum() == 0


# ── ゲートと判定 ─────────────────────────────────────────────────────────────


def _record(
    method: str,
    refresh: int,
    join_delay: int,
    leave_delay: int | None,
    seed: int,
    n_hit: int,
    availability: float = JUDGED_AVAILABILITY,
) -> dict[str, Any]:
    """ゲート・判定の入力の最小限の実行の記録（件数は n_hit だけを変える）．"""
    return {
        "method": method,
        "availability": availability,
        "refresh": refresh,
        "seed": seed,
        "join_delay": join_delay,
        "leave_delay": leave_delay,
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


def _grid(
    method: str, n_hit: int, availability: float = JUDGED_AVAILABILITY
) -> list[dict[str, Any]]:
    """1 つの方式・種 0 の全ての（R，L_join，L_leave）の同じ値の実行．"""
    runs = []
    for refresh in REFRESH_STEPS:
        for join_delay in JOIN_DELAY_GRID:
            for leave_delay in LEAVE_DELAY_GRID:
                runs.append(
                    _record(method, refresh, join_delay, leave_delay, 0, n_hit, availability)
                )
    return runs


def _without_delay_keys(r: dict[str, Any], delay: int) -> dict[str, Any]:
    """Iteration 16 の runs の形（join_delay・leave_delay の代わりに delay を持つ）．"""
    out = {k: v for k, v in r.items() if k not in ("join_delay", "leave_delay")}
    return {**out, "delay": delay}


def test_the_join_reference_gate_compares_only_the_same_join_delay() -> None:
    runs = []
    for refresh in REFRESH_STEPS:
        for join_delay in JOIN_DELAY_GRID:
            runs.append(_record("x", refresh, join_delay, None, 0, 50 if join_delay == 0 else 40))
    reference = {
        "m": 2,
        "n_test": 100,
        "runs": [_without_delay_keys(r, r["join_delay"]) for r in runs]
        # Iteration 16 の L = ∞ の実行は別の値なので，L で絞らずに照合すると不通過になる
        + [_without_delay_keys(_record("x", r, 0, None, 0, 10), None) for r in REFRESH_STEPS],
    }
    for join_delay in JOIN_DELAY_GRID:
        result = gate_join_reference(runs, reference, join_delay, 2, 100)
        assert result["passed"] is True
        assert result["matched"] == len(REFRESH_STEPS)
    runs[0]["n_hit"] = 52
    assert gate_join_reference(runs, reference, 0, 2, 100)["passed"] is False


def test_g3_fails_when_a_slow_leave_notice_changes_a_run() -> None:
    runs = _grid("x", 50)
    result = gate_g3(runs)
    assert result["passed"] is True
    # 種 1 個・方式 1 個で，L_leave ≥ R は（R = 1 で 2 個，10 で 2 個，100 で 1 個）× L_join 2 個
    assert result["compared"] == 10
    next(r for r in runs if r["refresh"] == 10 and r["leave_delay"] == 100)["wasted"] = 0.5
    result = gate_g3(runs)
    assert result["passed"] is False
    assert result["mismatches"][0]["fields"] == ["wasted"]


def test_g5_fails_when_immediate_notices_differ_from_the_fresh_view() -> None:
    runs = _grid("x", 60)
    result = gate_g5(runs)
    assert result["passed"] is True
    assert result["compared"] == len(REFRESH_STEPS)
    next(
        r for r in runs if r["refresh"] == 1000 and r["join_delay"] == 0 and r["leave_delay"] == 0
    )["n_hit"] = 59
    assert gate_g5(runs)["passed"] is False


def _recovery_runs(notified: list[int], fresh: int = 60, stale: int = 40) -> list[dict[str, Any]]:
    """HL1_METHOD の a = 0.7 の合成の結果．R = 1 は fresh，R = 最大・L_join = 0 の L_leave = HL1_LEAVE_DELAY は notified[種]，他は stale．"""
    runs = []
    for s in range(N_SCHEDULE_SEEDS):
        for refresh in REFRESH_STEPS:
            for join_delay in JOIN_DELAY_GRID:
                for leave_delay in LEAVE_DELAY_GRID:
                    if refresh == min(REFRESH_STEPS):
                        n_hit = fresh
                    elif (
                        refresh == max(REFRESH_STEPS)
                        and join_delay == 0
                        and leave_delay == HL1_LEAVE_DELAY
                    ):
                        n_hit = notified[s]
                    else:
                        n_hit = stale
                    runs.append(_record(HL1_METHOD, refresh, join_delay, leave_delay, s, n_hit))
    return runs


def test_hl1_needs_three_quarters_of_the_drop_back_in_every_seed() -> None:
    supported = judge_hl1(recovery_by_method(_recovery_runs([55] * 5), [HL1_METHOD], 0.7), True)
    assert supported["rho"] == pytest.approx([0.75] * 5)
    assert supported["verdict"] == "支持"
    one_low = judge_hl1(
        recovery_by_method(_recovery_runs([55, 55, 55, 55, 54]), [HL1_METHOD], 0.7), True
    )
    assert one_low["supported"] is False
    assert one_low["verdict"] == "支持されない"
    assert (
        judge_hl1(recovery_by_method(_recovery_runs([60] * 5), [HL1_METHOD], 0.7), False)["verdict"]
        == "退行ゲートを通らないため判定しない"
    )


def test_hl1_treats_a_seed_without_a_drop_as_not_supported() -> None:
    recovery = recovery_by_method(_recovery_runs([40] * 5, fresh=40, stale=40), [HL1_METHOD], 0.7)
    out = judge_hl1(recovery, True)
    assert out["rho"] == [None] * 5
    assert out["supported"] is False


def test_the_registered_grid_is_the_lever_values() -> None:
    assert LEAVE_DELAYS == (0, 10, 100)
    assert LEAVE_DELAY_GRID[0] is None
    assert JOIN_DELAY_GRID == (0, 100)
    assert (HL1_METHOD, HL1_LEAVE_DELAY, HL1_MIN_RECOVERY) == ("device_probe_d12", 10, 0.75)
    assert JUDGED_AVAILABILITY in AVAILABILITIES
