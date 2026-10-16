"""device_probe の D ごとに補う変種と補わない構成を比べる評価（atrium.churn_budget_eval）の補間・判定・ゲートの仕様．"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from atrium.churn_budget_eval import (
    BUDGETS,
    G1_FULL_BUDGET,
    G1_NONE_BUDGET,
    HB1_MIN_MEAN,
    HB2_MAX_ABS,
    HB3_MAX_MEAN,
    MAIN_BUDGET,
    MAIN_JOIN_DELAY,
    MAIN_REFILL,
    UNDETERMINED,
    aggregate_runs,
    budget_name,
    classify_deltas,
    curve_inverse,
    curve_value,
    gate_g1,
    gate_g2,
    gate_g4_nesting,
    gate_g6,
    none_curve,
    render_report,
    simulate_runs,
)
from atrium.churn_refill_eval import NO_REFILL

# 合成の配置：18 デバイス（BUDGETS の最大）・各 2 受信箱．デバイスの番号とノードの番号をわざとずらす
N_DEVICES = 18
DEVICE_NODE = np.roll(np.arange(N_DEVICES), 5).astype(np.int64)
INBOXES_PER_DEVICE = 2
DEVICE_OF_INBOX = np.repeat(np.arange(N_DEVICES), INBOXES_PER_DEVICE).astype(np.int64)
NODE_OF = DEVICE_NODE[DEVICE_OF_INBOX]
N_STEPS = 300
M = 2
SEED = 12
Simulated = tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]


@pytest.fixture(scope="module")
def simulated() -> Simulated:
    """合成の得点で全ての D・補い方・条件を流した結果（実行の記録，G5 の照合，G4 の照合）．"""
    rng = np.random.default_rng(0)
    first = rng.random((N_STEPS, len(NODE_OF))).astype(np.float32)
    second = rng.random((N_STEPS, len(NODE_OF))).astype(np.float32)
    gold = rng.integers(0, len(NODE_OF), N_STEPS).tolist()
    return simulate_runs(
        first, second, gold, NODE_OF, DEVICE_OF_INBOX, DEVICE_NODE, N_DEVICES, M, BUDGETS, SEED
    )


def _reference(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """G1 の基準を，同じ実行の D = 12 の全ての補い方と D = 15 の none から作る．"""
    selected = [
        dict(r)
        for r in runs
        if r["method"] == budget_name(G1_FULL_BUDGET)
        or (r["method"] == budget_name(G1_NONE_BUDGET) and r["refill"] == NO_REFILL)
    ]
    return {"m": M, "n_test": N_STEPS, "runs": selected}


# ── 折れ線と判定 ──────────────────────────────────────────────────────────


def test_none_curve_keeps_the_largest_hit_among_equal_contacts() -> None:
    xs, ys = none_curve([(12.0, 100), (12.0, 104), (10.0, 90)])
    assert xs == [10.0, 12.0]
    assert ys == [90.0, 104.0]


def test_curve_value_interpolates_inside_and_is_none_outside() -> None:
    xs, ys = [10.0, 12.0, 14.0], [100.0, 110.0, 120.0]
    assert curve_value(xs, ys, 11.0) == pytest.approx(105.0)
    assert curve_value(xs, ys, 9.9) is None
    assert curve_value(xs, ys, 14.1) is None


def test_curve_inverse_returns_the_unique_solution() -> None:
    assert curve_inverse([10.0, 12.0, 14.0], [100.0, 110.0, 120.0], 105.0) == pytest.approx(11.0)


def test_curve_inverse_is_none_when_two_segments_cross_the_value() -> None:
    assert curve_inverse([10.0, 12.0, 14.0], [100.0, 120.0, 110.0], 115.0) is None


def test_curve_inverse_counts_a_peak_vertex_once() -> None:
    assert curve_inverse([10.0, 12.0, 14.0], [100.0, 120.0, 110.0], 120.0) == pytest.approx(12.0)


def test_curve_inverse_is_none_on_a_flat_segment() -> None:
    assert curve_inverse([10.0, 12.0, 14.0], [100.0, 110.0, 110.0], 110.0) is None


def test_curve_inverse_is_none_outside_the_range() -> None:
    assert curve_inverse([10.0, 12.0], [100.0, 110.0], 120.0) is None


@pytest.mark.parametrize(
    ("deltas", "expected"),
    [
        ([HB1_MIN_MEAN] * 5, "H-B1"),
        ([20.0, 20.0, 20.0, 20.0, 5.0], "H-B1"),
        ([HB1_MIN_MEAN - 1] * 5, "H-B2"),
        ([HB3_MAX_MEAN] * 5, "H-B3"),
        ([1.0, -2.0, 3.0, -4.0, 5.0], "H-B2"),
        ([HB2_MAX_ABS + 5, -HB2_MAX_ABS - 5, 0.0, 0.0, 0.0], UNDETERMINED),
        ([None, 1.0, 1.0, 1.0, 1.0], UNDETERMINED),
    ],
)
def test_classify_deltas_checks_hb1_then_hb3_then_hb2(
    deltas: list[float | None], expected: str
) -> None:
    assert classify_deltas(deltas) == expected


# ── 退行ゲートと集計の流れ ──────────────────────────────────────────────────


def test_the_whole_pipeline_on_synthetic_data_has_the_registered_counts(
    simulated: Simulated,
) -> None:
    runs, inclusion, nesting = simulated
    agg = aggregate_runs(runs, inclusion, nesting, _reference(runs), BUDGETS, M, N_STEPS)
    gates = agg["gates"]
    assert len(runs) == 7200
    assert gates["g1"]["matched"] == 960
    assert gates["g2"]["compared"] == 2100
    assert len(gates["g3"]["violations"]) == 0
    assert gates["g4"]["compared"] == 2160
    assert gates["g5"]["compared"] == 2400
    assert gates["g6"]["compared"] == 800
    assert gates["passed"]
    assert len(agg["hb"]["per_seed"]) == 5
    metrics = {
        "n_inboxes": len(NODE_OF),
        "n_nodes": N_DEVICES,
        "n_devices": N_DEVICES,
        "n_test": N_STEPS,
        "m": M,
        "n_centroids": 32,
        "mean_up_steps": 0,
        "n_schedule_seeds": 5,
        "budgets": list(BUDGETS),
        "join_delays": [None, 0, 100],
        "refills": ["none", "once", "until_full"],
        **agg,
    }
    assert "機械的な判定" in render_report(metrics)


def test_g1_fails_when_one_field_differs(simulated: Simulated) -> None:
    runs = simulated[0]
    reference = _reference(runs)
    reference["runs"][0]["n_hit"] += 1
    assert not gate_g1(runs, reference, M, N_STEPS)["passed"]


def test_g1_fails_when_a_reference_run_is_missing(simulated: Simulated) -> None:
    runs = simulated[0]
    reference = _reference(runs)
    reference["runs"] = reference["runs"][1:]
    result = gate_g1(runs, reference, M, N_STEPS)
    assert not result["passed"]
    assert len(result["missing_in_reference"]) == 1


def test_g1_fails_when_the_question_count_differs(simulated: Simulated) -> None:
    runs = simulated[0]
    reference = _reference(runs) | {"n_test": N_STEPS + 1}
    assert not gate_g1(runs, reference, M, N_STEPS)["passed"]


def test_g2_fails_when_a_refill_without_departures_differs(simulated: Simulated) -> None:
    runs = [dict(r) for r in simulated[0]]
    target = next(r for r in runs if r["refill"] == "once" and r["refresh"] == 1)
    target["n_hit"] += 1
    assert not gate_g2(runs)["passed"]


def test_g4_fails_when_contacts_decrease_with_the_budget(simulated: Simulated) -> None:
    runs = [dict(r) for r in simulated[0]]
    nesting = simulated[2]
    target = next(r for r in runs if r["refill"] == NO_REFILL and r["budget"] == max(BUDGETS))
    target["n_contacted"] = -1
    assert not gate_g4_nesting(runs, nesting, len(BUDGETS))["passed"]


def test_g4_fails_when_a_nesting_check_has_violations(simulated: Simulated) -> None:
    runs, _, nesting = simulated
    broken = [dict(c) for c in nesting]
    broken[0]["violations"] = 1
    assert not gate_g4_nesting(runs, broken, len(BUDGETS))["passed"]


def test_g6_fails_when_until_full_on_an_immediate_join_view_differs(simulated: Simulated) -> None:
    runs = [dict(r) for r in simulated[0]]
    target = next(r for r in runs if r["join_delay"] == 0 and r["refill"] == "until_full")
    target["n_hit"] += 1
    assert not gate_g6(runs)["passed"]


def test_the_registered_constants_are_the_lever_values() -> None:
    assert tuple(range(9, 19)) == BUDGETS
    assert (MAIN_BUDGET, MAIN_REFILL, MAIN_JOIN_DELAY) == (12, "once", 100)
    assert (HB1_MIN_MEAN, HB3_MAX_MEAN, HB2_MAX_ABS) == (15.0, -15.0, 25.0)
