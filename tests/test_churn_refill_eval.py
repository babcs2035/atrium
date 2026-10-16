"""返事の無い probe の枠を補う変種の評価（atrium.churn_refill_eval）の補う規則・ゲート・判定の仕様．"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from atrium.churn_cascade_eval import (
    COUNT_FIELDS,
    JUDGED_CASCADES,
    SINGLE_STAGE_METHODS,
    Cascade,
    route_cascade_under_churn,
    run_record,
    visible_top_devices,
    visible_top_k,
)
from atrium.churn_eval import (
    AVAILABILITIES,
    JUDGED_AVAILABILITY,
    MEAN_UP_STEPS,
    N_SCHEDULE_SEEDS,
    REFRESH_STEPS,
    STAT_FIELDS,
    BoolArray,
    simulate_availability,
)
from atrium.churn_join_eval import LOSS_CLASSES, join_notified_view
from atrium.churn_refill_eval import (
    CONTROL,
    HP1_JOIN_DELAY,
    HP1_METHOD,
    HP1_MIN_RECOVERY,
    HP1_REFILL,
    JOIN_DELAY_GRID,
    NO_REFILL,
    REFILLS,
    ROUND_FIELDS,
    aggregate_runs,
    count_g6_violations,
    gate_g2,
    gate_g3,
    gate_g5,
    gate_g6,
    judge_hp1,
    recovery_by_method,
    render_report,
    round_ends,
    route_cascade_with_refill,
    simulate_runs,
)

N_STEPS = 400
M = 2
# デバイスの番号とノードの番号をわざとずらす（node_of_device の表を通すことを確かめる）
DEVICE_NODE = np.array([3, 1, 4, 0, 5, 2], np.int64)
INBOXES_PER_DEVICE = 2
DEVICE_OF_INBOX = np.repeat(np.arange(len(DEVICE_NODE)), INBOXES_PER_DEVICE).astype(np.int64)
NODE_OF = DEVICE_NODE[DEVICE_OF_INBOX]
N_NODES = len(DEVICE_NODE)
CASCADES = (
    Cascade("cp_k3", "centroid_probe", 3),
    Cascade("cp_k6", "centroid_probe", 6),
    Cascade("dp_d2", "device_probe", 2),
    Cascade("dp_d3", "device_probe", 3),
)


def _schedule(seed: int, availability: float = 0.6) -> BoolArray:
    """乱数の時刻表（稼働中の平均 8 刻み．参加と離脱が頻繁に起きる）．"""
    return simulate_availability(
        N_NODES, N_STEPS, availability, 8, np.random.default_rng([seed, 99])
    )


def _scores(seed: int) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], list[int]]:
    """乱数の 1 段目・2 段目の得点と正解（同点の起きない連続値）．"""
    rng = np.random.default_rng(seed)
    first = rng.random((N_STEPS, len(NODE_OF))).astype(np.float32)
    second = rng.random((N_STEPS, len(NODE_OF))).astype(np.float32)
    gold = rng.integers(0, len(NODE_OF), N_STEPS).tolist()
    return first, second, gold


def _route(view: BoolArray, online: BoolArray, cascade: Cascade, seed: int = 0) -> Any:
    """合成の配置で全ての補い方を選ぶ．"""
    first, second, gold = _scores(seed)
    return route_cascade_with_refill(
        first,
        second,
        gold,
        NODE_OF,
        DEVICE_OF_INBOX,
        DEVICE_NODE,
        view,
        online,
        M,
        cascade,
        REFILLS,
    )


# ── 補う規則 ──────────────────────────────────────────────────────────────


def test_round_ends_follow_the_dead_count_of_the_previous_round() -> None:
    # 見えている 6 個の単位の返事の有無（順位の順）：無・有・無・無・有・有．S = 2
    dead = np.array([[True, False, True, True, False, False]])
    dead_cum = np.concatenate([[[0]], np.cumsum(dead, axis=1)], axis=1).astype(np.int64)
    n_visible = np.array([6], np.int64)
    assert [e.tolist() for e in round_ends(dead_cum, 2, n_visible, "none")] == [[0], [2]]
    # 1 往復目の 1 個の空きを 3 番目で補う
    assert [e.tolist() for e in round_ends(dead_cum, 2, n_visible, "once")] == [[0], [2], [3]]
    # 3・4 番目も返事が無いので，5 番目で返事のある単位が 2 個に揃う
    assert [e.tolist() for e in round_ends(dead_cum, 2, n_visible, "until_full")] == [
        [0],
        [2],
        [3],
        [4],
        [5],
    ]


def test_round_ends_stop_when_the_visible_units_run_out() -> None:
    dead = np.array([[True, True, True]])
    dead_cum = np.concatenate([[[0]], np.cumsum(dead, axis=1)], axis=1).astype(np.int64)
    ends = round_ends(dead_cum, 2, np.array([3], np.int64), "until_full")
    assert ends[-1].tolist() == [3]


@pytest.mark.parametrize("cascade", CASCADES, ids=lambda c: c.name)
@pytest.mark.parametrize("refresh", [10, 100])
def test_no_refill_is_the_iteration_15_cascade(cascade: Cascade, refresh: int) -> None:
    # G1 の構造上の根拠：none は route_cascade_under_churn と同じ結果になる
    online = _schedule(1)
    view = join_notified_view(online, refresh, None)
    first, second, gold = _scores(1)
    out, loss = route_cascade_under_churn(
        first, second, gold, NODE_OF, DEVICE_OF_INBOX, DEVICE_NODE, view, online, M, cascade
    )
    got = _route(view, online, cascade, 1)[NO_REFILL]
    assert run_record("x", 0.6, refresh, 0, got.outcome, got.loss) == run_record(
        "x", 0.6, refresh, 0, out, loss
    )
    assert (got.rounds <= 1).all()


@pytest.mark.parametrize("cascade", CASCADES, ids=lambda c: c.name)
def test_until_full_answers_with_the_top_of_the_visible_and_online_units(cascade: Cascade) -> None:
    online = _schedule(2)
    view = join_notified_view(online, 100, None)
    first, _, _ = _scores(2)
    live_inbox = view[:, NODE_OF] & online[:, NODE_OF]
    if cascade.kind == "centroid_probe":
        expected = visible_top_k(first, live_inbox, cascade.size)
    else:
        live_device = view[:, DEVICE_NODE] & online[:, DEVICE_NODE]
        expected = visible_top_devices(
            first, live_inbox, DEVICE_OF_INBOX, live_device, cascade.size
        )
    assert (_route(view, online, cascade, 2)["until_full"].answered == expected).all()


@pytest.mark.parametrize("cascade", CASCADES, ids=lambda c: c.name)
@pytest.mark.parametrize("refresh", [10, 100])
def test_until_full_on_an_immediate_join_view_matches_the_fresh_view(
    cascade: Cascade, refresh: int
) -> None:
    # G5 の構造上の根拠：L_join = 0 の表は稼働中の全ノードを含む
    online = _schedule(3)
    full = _route(join_notified_view(online, refresh, 0), online, cascade, 3)["until_full"]
    fresh = _route(join_notified_view(online, 1, None), online, cascade, 3)[NO_REFILL]
    got = run_record("x", 0.6, refresh, 0, full.outcome, full.loss)
    want = run_record("x", 0.6, 1, 0, fresh.outcome, fresh.loss)
    assert {f: got[f] for f in COUNT_FIELDS} == {f: want[f] for f in COUNT_FIELDS}


@pytest.mark.parametrize("cascade", CASCADES, ids=lambda c: c.name)
@pytest.mark.parametrize("join_delay", [None, 0, 5])
def test_answered_candidates_grow_with_the_refill(cascade: Cascade, join_delay: int | None) -> None:
    # G6 の構造上の根拠：none ⊆ once ⊆ until_full
    online = _schedule(4)
    results = _route(join_notified_view(online, 50, join_delay), online, cascade, 4)
    assert count_g6_violations(results) == 0
    # 頻繁な離脱で実際に補う問がある
    assert (results["once"].rounds == 2).any()
    assert results["once"].rounds.max() <= 2
    # hit は単調とは限らない（候補が増えると 2 段目の上位 m 個から正解が押し出されうる）ので確かめない


def test_once_adds_at_most_the_unanswered_devices() -> None:
    # device_probe では返事の無い f 台の分だけ，新たな問い合わせの先が増えうる
    online = _schedule(5)
    results = _route(join_notified_view(online, 100, None), online, CASCADES[3], 5)
    none, once = results[NO_REFILL].outcome, results["once"].outcome
    added = once.contacted - none.contacted
    assert (added >= 0).all()
    assert (added <= none.wasted).all()
    assert (added > 0).any()


@pytest.mark.parametrize("cascade", CASCADES, ids=lambda c: c.name)
def test_without_departures_every_refill_equals_no_refill(cascade: Cascade) -> None:
    # G3 の構造上の根拠：離脱が無ければ補う枠が無い
    online = np.ones((N_STEPS, N_NODES), np.bool_)
    results = _route(join_notified_view(online, 100, None), online, cascade, 6)
    base = results[NO_REFILL]
    for refill in REFILLS[1:]:
        r = results[refill]
        assert (r.answered == base.answered).all()
        assert (r.rounds == base.rounds).all()
        assert (r.requests == base.requests).all()


# ── ゲートと判定 ─────────────────────────────────────────────────────────────


def _record(
    method: str,
    availability: float,
    refresh: int,
    join_delay: int | None,
    refill: str,
    seed: int,
    n_hit: int,
) -> dict[str, Any]:
    """ゲート・判定の入力の最小限の実行の記録（件数は n_hit だけを変える）．"""
    return {
        "method": method,
        "availability": availability,
        "refresh": refresh,
        "seed": seed,
        "join_delay": join_delay,
        "refill": refill,
        **{f: 0.0 for f in STAT_FIELDS},
        "hit": n_hit / 100,
        "n": 100,
        "n_hit": n_hit,
        "n_gold_offline": 0,
        "n_gold_invisible": 0,
        "n_not_selected": 100 - n_hit,
        "n_not_candidate": 100 - n_hit,
        "n_not_top_m": 0,
        "n_slot_taken": 0,
        "n_contacted": 100,
        **{f: 0 for f in ROUND_FIELDS},
    }


def _grid(method: str, n_hit: int) -> list[dict[str, Any]]:
    """1 つの方式・種 0 の全ての（a，R，L_join，補い方）の同じ値の実行．"""
    return [
        _record(method, a, refresh, join_delay, refill, 0, n_hit)
        for a in AVAILABILITIES
        for refresh in REFRESH_STEPS
        for join_delay in JOIN_DELAY_GRID
        for refill in REFILLS
    ]


def test_g2_fails_when_a_method_without_targets_changes() -> None:
    runs = _grid("x", 50)
    result = gate_g2(runs, ["x"])
    assert result["passed"] is True
    assert result["compared"] == len(AVAILABILITIES) * len(REFRESH_STEPS) * 3 * 2
    next(r for r in runs if r["refill"] == "once")["wasted"] = 0.5
    result = gate_g2(runs, ["x"])
    assert result["passed"] is False
    assert result["mismatches"][0]["fields"] == ["wasted"]


def test_g3_compares_only_runs_without_visible_departures() -> None:
    runs = _grid("x", 50)
    # a = 1.0 で R 4 個，a < 1 で R = 1 の 3 個，それぞれ L_join 3 個 × 補い方 2 個
    assert gate_g3(runs)["compared"] == (4 + 3) * 3 * 2
    # R = 1000・a = 0.7 の違いは G3 の対象外
    next(
        r
        for r in runs
        if r["availability"] == JUDGED_AVAILABILITY
        and r["refresh"] == 1000
        and r["refill"] == "once"
    )["n_hit"] = 70
    assert gate_g3(runs)["passed"] is True
    next(r for r in runs if r["refresh"] == 1 and r["refill"] == "until_full")["rounds_max"] = 2
    assert gate_g3(runs)["passed"] is False


def test_g3_ignores_the_control() -> None:
    # 対照は none だけなので，比べる相手が無い
    runs = [r for r in _grid(CONTROL.name, 50) if r["refill"] == NO_REFILL]
    assert gate_g3(runs + _grid("x", 50))["passed"] is True


def test_g5_fails_when_until_full_on_an_immediate_join_view_differs_from_r1() -> None:
    runs = _grid(HP1_METHOD, 60)
    result = gate_g5(runs)
    assert result["passed"] is True
    assert result["compared"] == len(AVAILABILITIES) * len(REFRESH_STEPS)
    next(
        r
        for r in runs
        if r["refresh"] == 1000 and r["join_delay"] == 0 and r["refill"] == "until_full"
    )["n_not_top_m"] = 1
    assert gate_g5(runs)["passed"] is False


def test_g6_needs_every_expected_run_without_violations() -> None:
    checks = [{"violations": 0}, {"violations": 0}]
    assert gate_g6(checks, 2)["passed"] is True
    assert gate_g6(checks, 3)["passed"] is False
    assert gate_g6([{"violations": 0}, {"violations": 4}], 2)["n_violating_questions"] == 4
    assert gate_g6([{"violations": 0}, {"violations": 4}], 2)["passed"] is False


def _recovery_runs(refilled: list[int], fresh: int = 60, stale: int = 40) -> list[dict[str, Any]]:
    """HP1_METHOD の a = 0.7 の合成の結果．R = 1 は fresh，R = 最大・L_join = 100・once は refilled[種]，他は stale．"""
    runs = []
    for s in range(N_SCHEDULE_SEEDS):
        for refresh in REFRESH_STEPS:
            for join_delay in JOIN_DELAY_GRID:
                for refill in REFILLS:
                    if refresh == min(REFRESH_STEPS):
                        n_hit = fresh
                    elif (
                        refresh == max(REFRESH_STEPS)
                        and join_delay == HP1_JOIN_DELAY
                        and refill == HP1_REFILL
                    ):
                        n_hit = refilled[s]
                    else:
                        n_hit = stale
                    runs.append(
                        _record(
                            HP1_METHOD, JUDGED_AVAILABILITY, refresh, join_delay, refill, s, n_hit
                        )
                    )
    return runs


def test_hp1_needs_a_fifth_of_the_drop_back_in_every_seed() -> None:
    recovery = recovery_by_method(_recovery_runs([44] * 5), [HP1_METHOD], JUDGED_AVAILABILITY)
    supported = judge_hp1(recovery, True)
    assert supported["rho"] == pytest.approx([0.2] * 5)
    assert supported["denominator"] == [20] * 5
    assert supported["verdict"] == "支持"
    one_low = recovery_by_method(
        _recovery_runs([44, 44, 44, 44, 43]), [HP1_METHOD], JUDGED_AVAILABILITY
    )
    assert judge_hp1(one_low, True)["verdict"] == "支持されない"
    assert judge_hp1(recovery, False)["verdict"] == "退行ゲートを通らないため判定しない"


def test_hp1_treats_a_seed_without_a_drop_as_not_supported() -> None:
    recovery = recovery_by_method(
        _recovery_runs([40] * 5, fresh=40, stale=40), [HP1_METHOD], JUDGED_AVAILABILITY
    )
    out = judge_hp1(recovery, True)
    assert out["rho"] == [None] * 5
    assert out["supported"] is False


# ── 集計と報告の流れ ──────────────────────────────────────────────────────────


def test_the_whole_pipeline_on_synthetic_data_has_the_registered_counts() -> None:
    # 16 ノード・16 デバイス・32 受信箱の合成の配置で，事前登録と同じ 8 構成 + 対照の件数が出ることを確かめる
    n_dev, per_dev, n_steps = 16, 2, 300
    device_of_inbox = np.repeat(np.arange(n_dev), per_dev).astype(np.int64)
    device_node = np.arange(n_dev, dtype=np.int64)[::-1].copy()
    node_of = device_node[device_of_inbox]
    rng = np.random.default_rng(7)
    first = rng.random((n_steps, len(node_of))).astype(np.float32)
    second = rng.random((n_steps, len(node_of))).astype(np.float32)
    gold = rng.integers(0, len(node_of), n_steps).tolist()
    full = (
        Cascade(f"centroid_probe_k{len(node_of)}", "centroid_probe", len(node_of)),
        Cascade(f"device_probe_d{n_dev}", "device_probe", n_dev),
    )
    cascades = (*JUDGED_CASCADES, *full)
    runs, transitions, g6_checks = simulate_runs(
        first, second, gold, node_of, device_of_inbox, device_node, n_dev, M, cascades, CONTROL, 12
    )
    # G1 の基準は none の実行を Iteration 16 の形（delay を持つ）に直したもの
    reference = {
        "m": M,
        "n_test": n_steps,
        "runs": [
            {
                **{k: v for k, v in r.items() if k not in ("join_delay", "refill")},
                "delay": r["join_delay"],
            }
            for r in runs
            if r["refill"] == NO_REFILL and r["method"] != CONTROL.name
        ],
    }
    no_target = [*SINGLE_STAGE_METHODS, *(c.name for c in full)]
    g6_expected = len(cascades) * len(AVAILABILITIES) * len(REFRESH_STEPS) * 3 * N_SCHEDULE_SEEDS
    agg = aggregate_runs(
        runs, transitions, g6_checks, reference, no_target, g6_expected, M, n_steps
    )
    gates = agg["gates"]
    assert len(runs) == 6000
    assert gates["g1"]["matched"] == 1920
    assert gates["g2"]["compared"] == 1920
    assert gates["g3"]["compared"] == 1680
    assert gates["g5"]["compared"] == 320
    assert gates["g6"]["compared"] == g6_expected
    assert len(transitions) == 60
    assert gates["passed"] is True, {k: g["passed"] for k, g in gates.items() if k != "passed"}
    metrics = {
        "n_inboxes": len(node_of),
        "n_nodes": n_dev,
        "n_devices": n_dev,
        "n_test": n_steps,
        "m": M,
        "n_centroids": 32,
        "mean_up_steps": MEAN_UP_STEPS,
        "n_schedule_seeds": N_SCHEDULE_SEEDS,
        "join_delays": list(JOIN_DELAY_GRID),
        "refills": list(REFILLS),
        "methods": [*SINGLE_STAGE_METHODS, *(c.name for c in cascades), CONTROL.name],
        "control_cascade": {"name": CONTROL.name, "kind": CONTROL.kind, "size": CONTROL.size},
        "loss_classes": list(LOSS_CLASSES),
        **agg,
    }
    report = render_report(metrics)
    assert "G6" in report
    assert agg["hp1"]["verdict"] in ("支持", "支持されない")


def test_the_registered_constants_are_the_lever_values() -> None:
    assert REFILLS == ("none", "once", "until_full")
    assert JOIN_DELAY_GRID == (None, 0, 100)
    assert (CONTROL.name, CONTROL.kind, CONTROL.size) == ("device_probe_d15", "device_probe", 15)
    assert (HP1_METHOD, HP1_REFILL, HP1_JOIN_DELAY, HP1_MIN_RECOVERY) == (
        "device_probe_d12",
        "once",
        100,
        0.20,
    )
