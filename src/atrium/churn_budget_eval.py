"""device_probe の 1 段目の台数 D を変え，返事の無い枠を補う変種と補わない構成を同じ問い合わせの数で比べるオフラインの評価（Iteration 19）．

`atrium churn-budget-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（規則は journal の Iteration 19 の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. 採点・時刻表・質問の順番・メンバー表（join_notified_view，L_join ∈ {∞, 0, 100}．離脱は通知しない）は Iteration 18 と同じにする．
2. device_probe の D ∈ BUDGETS の全てで，補い方 none・once・until_full を churn_refill_eval.route_cascade_with_refill で選ぶ
   （補う規則・2 段目・hit と (i)〜(iv)・contacted・wasted・往復の情報は作り直さない）．
3. 同じ（a, R, L_join, 種）の none の点 (contacted, n_hit) を contacted の昇順に並べた折れ線 Î を作り（contacted の等しい点は
   n_hit の最大値），補う変種の点 (c, n) に Δ = n − Î(c)（範囲外は None）と s = Î⁻¹(n) − c（解がただ 1 つのときだけ）を出す．
4. 退行ゲート G1〜G6，主の点の判定 H-B1 → H-B3 → H-B2，記録（none の曲線・Δ と s・問ごとの contacted の分布・往復・d15 との差）を出す．
   ゲートが 1 つでも通らなければ判定しない．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.advert_eval import load_inboxes, load_query_set, score_flood_dense, score_multi
from atrium.arrays import F32Array, I64Array
from atrium.churn_cascade_eval import COUNT_FIELDS, Cascade, gate_g4, node_of_device
from atrium.churn_eval import (
    AVAILABILITIES,
    JUDGED_AVAILABILITY,
    MEAN_UP_STEPS,
    N_SCHEDULE_SEEDS,
    REFRESH_STEPS,
    BoolArray,
    simulate_availability,
)
from atrium.churn_join_eval import delay_label, join_notified_view
from atrium.churn_refill_eval import (
    EXACT_FIELDS,
    JOIN_DELAY_GRID,
    NO_REFILL,
    REFILLED,
    REFILLS,
    Refill,
    count_g6_violations,
    refill_run_record,
    route_cascade_with_refill,
    summarize_by_refill,
    summarize_losses_by_refill,
)
from atrium.churn_refill_eval import gate_g6 as refill_inclusion_gate
from atrium.config import AtriumConfig
from atrium.device_probe_eval import device_index_of_inbox
from atrium.manifest import Placement
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

# device_probe の 1 段目の台数 D の格子（G4 の入れ子の照合は昇順で隣り合う D の間で行う）
BUDGETS: tuple[int, ...] = tuple(range(9, 19))
MIN_REFRESH = min(REFRESH_STEPS)
MAX_REFRESH = max(REFRESH_STEPS)
# 離脱の無い稼働率（G2．このときと R = 1 では見えているのに離脱済みのデバイスが無く，補う枠が生じない）
NO_CHURN_AVAILABILITY = 1.0
# 主の点：D = 12・once・a = 0.7・R = 最大・L_join = 100
MAIN_BUDGET = 12
MAIN_REFILL: Refill = "once"
MAIN_JOIN_DELAY = 100
# 判定の閾値（問）．H-B1 → H-B3 → H-B2 の順に調べ，最初に成り立ったものを結論にする
HB1_MIN_MEAN = 15.0
HB3_MAX_MEAN = -15.0
HB2_MAX_ABS = 25.0
UNDETERMINED = "undetermined"
CONCLUSION_TEXT = {
    "H-B1": "H-B1（補う変種が有利）",
    "H-B3": "H-B3（補う変種が不利）",
    "H-B2": "H-B2（差が無い）",
    UNDETERMINED: "判定できない",
}
# G1：Iteration 18 で全ての補い方を流した D と，none だけを流した対照の D
G1_FULL_BUDGET = 12
G1_NONE_BUDGET = 15
G1_KEY_FIELDS = ("method", "availability", "refresh", "join_delay", "refill", "seed")
# G6：L_join = 0・until_full は R = 1 と件数で一致する（Iteration 18 の G5）
G6_JOIN_DELAY = 0
G6_REFILL: Refill = "until_full"
# 記録：問ごとの contacted の分布のパーセンタイル（numpy.percentile の既定の線形補間）
CONTACTED_PERCENTILE = 95
# 記録：Iteration 18 の対照の形（device_probe_d15 の none との単純な差）
D15_BUDGET = 15
# 記録：none の曲線と Δ・s の表を出す条件 (a, R, L_join)．先頭が主の条件
RECORDED_CONDITIONS: tuple[tuple[float, int, int | None], ...] = (
    (JUDGED_AVAILABILITY, MAX_REFRESH, MAIN_JOIN_DELAY),
    (JUDGED_AVAILABILITY, MAX_REFRESH, None),
    (JUDGED_AVAILABILITY, MAX_REFRESH, 0),
    (0.9, MAX_REFRESH, MAIN_JOIN_DELAY),
    (0.5, MAX_REFRESH, MAIN_JOIN_DELAY),
)
# （稼働率，更新間隔，L_join，種）．none の曲線はこの単位で作る
CurveKey = tuple[float, int, int | None, int]


def budget_name(d: int) -> str:
    """D 台の device_probe の構成の名前（Iteration 15〜18 の device_probe_d12・d15 と同じ付け方．G1 の照合のキーになる）．"""
    return f"device_probe_d{d}"


def _curve_key(r: dict[str, Any]) -> CurveKey:
    """実行の（稼働率，更新間隔，L_join，種）．"""
    return (r["availability"], r["refresh"], r["join_delay"], r["seed"])


def _run_id(r: dict[str, Any]) -> dict[str, Any]:
    """ゲートの不一致の記録に載せる実行の識別子．"""
    return {k: r[k] for k in G1_KEY_FIELDS}


# ── 評価の本体 ────────────────────────────────────────────────────────────


def simulate_runs(
    first: F32Array,
    second: F32Array,
    gold: Sequence[int],
    node_of: I64Array,
    device_of_inbox: I64Array,
    device_node: I64Array,
    n_nodes: int,
    m: int,
    budgets: Sequence[int],
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """時刻表ごとに R・L_join・D・補い方を変えて選び，実行の記録・G5 の照合（補い方の包含）・G4 の照合（D の入れ子）を返す．

    乱数の引き方・問の数・ノードの数・メンバー表は Iteration 18 と同じにする（G1 で照合する）．
    """
    ordered = sorted(budgets)
    n_steps = len(gold)
    runs: list[dict[str, Any]] = []
    inclusion_checks: list[dict[str, Any]] = []
    nesting_checks: list[dict[str, Any]] = []
    for availability in AVAILABILITIES:
        for s in range(N_SCHEDULE_SEEDS):
            rng = np.random.default_rng([seed, s, round(availability * 100)])
            online = simulate_availability(n_nodes, n_steps, availability, MEAN_UP_STEPS, rng)
            for refresh in REFRESH_STEPS:
                for join_delay in JOIN_DELAY_GRID:
                    view = join_notified_view(online, refresh, join_delay)
                    condition = {
                        "availability": availability,
                        "refresh": refresh,
                        "join_delay": join_delay,
                        "seed": s,
                    }
                    prev_answered: BoolArray | None = None
                    for d in ordered:
                        cascade = Cascade(budget_name(d), "device_probe", d)
                        results = route_cascade_with_refill(
                            first,
                            second,
                            gold,
                            node_of,
                            device_of_inbox,
                            device_node,
                            view,
                            online,
                            m,
                            cascade,
                            REFILLS,
                        )
                        for refill, res in results.items():
                            contacted = res.outcome.contacted
                            record = refill_run_record(
                                cascade.name,
                                availability,
                                refresh,
                                join_delay,
                                refill,
                                s,
                                res.outcome,
                                res.loss,
                                res.rounds,
                                res.requests,
                            )
                            runs.append(
                                record
                                | {
                                    "budget": d,
                                    "contacted_max": int(contacted.max()),
                                    "contacted_p95": float(
                                        np.percentile(contacted, CONTACTED_PERCENTILE)
                                    ),
                                }
                            )
                        inclusion_checks.append(
                            {**condition, "budget": d, "violations": count_g6_violations(results)}
                        )
                        # none の返事のある候補が，1 つ小さい D の候補を含むか（G4）
                        answered = results[NO_REFILL].answered
                        if prev_answered is not None:
                            not_nested = (prev_answered & ~answered).any(axis=1)
                            nesting_checks.append(
                                {**condition, "budget": d, "violations": int(not_nested.sum())}
                            )
                        prev_answered = answered
        logger.info("availability %.2f done", availability)
    return runs, inclusion_checks, nesting_checks


# ── 退行ゲート ──────────────────────────────────────────────────────────────


def _g1_key(r: dict[str, Any]) -> tuple[Any, ...]:
    """G1 の照合のキー（method，availability，refresh，join_delay，refill，seed）．"""
    return tuple(r[f] for f in G1_KEY_FIELDS)


def gate_g1(
    runs: Sequence[dict[str, Any]],
    reference: dict[str, Any],
    m: int,
    n_test: int,
    full_budget: int = G1_FULL_BUDGET,
    none_budget: int = G1_NONE_BUDGET,
) -> dict[str, Any]:
    """G1：D = full_budget の全ての補い方と D = none_budget の none が，Iteration 18 の runs の同じキーの実行と EXACT_FIELDS で完全に一致するか．

    両側の欠けが 0 件で，m・n_test も一致することを求める．
    """
    full_name, none_name = budget_name(full_budget), budget_name(none_budget)

    def selected(r: dict[str, Any]) -> bool:
        """G1 で照合する実行か（D = full_budget の全ての補い方と D = none_budget の none）．"""
        return bool(
            r["method"] == full_name or (r["method"] == none_name and r["refill"] == NO_REFILL)
        )

    ref = {_g1_key(r): r for r in reference["runs"] if selected(r)}
    ours = [r for r in runs if selected(r)]
    our_keys = {_g1_key(r) for r in ours}
    matched = 0
    mismatches = []
    missing_in_reference = []
    for r in ours:
        expected = ref.get(_g1_key(r))
        if expected is None:
            missing_in_reference.append(_run_id(r))
            continue
        matched += 1
        differing = [f for f in EXACT_FIELDS if r[f] != expected[f]]
        if differing:
            mismatches.append({**_run_id(r), "fields": differing})
    missing_here = [dict(zip(G1_KEY_FIELDS, k, strict=True)) for k in ref if k not in our_keys]
    expected_count = (
        (len(REFILLS) + 1)
        * len(AVAILABILITIES)
        * len(REFRESH_STEPS)
        * len(JOIN_DELAY_GRID)
        * N_SCHEDULE_SEEDS
    )
    same_setup = reference["m"] == m and reference["n_test"] == n_test
    return {
        "full_budget": full_budget,
        "none_budget": none_budget,
        "fields": list(EXACT_FIELDS),
        "matched": matched,
        "expected_count": expected_count,
        "same_setup": same_setup,
        "mismatches": mismatches,
        "missing_here": missing_here,
        "missing_in_reference": missing_in_reference,
        "passed": same_setup
        and matched == expected_count
        and not mismatches
        and not missing_here
        and not missing_in_reference,
    }


def _no_churn(r: dict[str, Any]) -> bool:
    """a = 1.0 または R = 1 の実行か（見えているのに離脱済みのデバイスが無い）．"""
    return bool(r["availability"] == NO_CHURN_AVAILABILITY or r["refresh"] == MIN_REFRESH)


def gate_g2(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """G2：a = 1.0 または R = 1 の once・until_full が，同じ D・条件の none と EXACT_FIELDS で完全に一致するか．

    churn_refill_eval.gate_g3 は device_probe_d15 を対照として照合から外すので使わない（今回は d15 も格子の 1 点である）．
    """
    base = {
        (r["method"], *_curve_key(r)): r for r in runs if r["refill"] == NO_REFILL and _no_churn(r)
    }
    compared = 0
    mismatches = []
    for r in runs:
        if r["refill"] == NO_REFILL or not _no_churn(r):
            continue
        compared += 1
        expected = base[(r["method"], *_curve_key(r))]
        differing = [f for f in EXACT_FIELDS if r[f] != expected[f]]
        if differing:
            mismatches.append({**_run_id(r), "fields": differing})
    expected_count = len(base) * len(REFILLED)
    return {
        "compared": compared,
        "expected_count": expected_count,
        "mismatches": mismatches,
        "passed": compared > 0 and compared == expected_count and not mismatches,
    }


def gate_g4_nesting(
    runs: Sequence[dict[str, Any]], nesting_checks: Sequence[dict[str, Any]], n_budgets: int
) -> dict[str, Any]:
    """G4：同じ（a, R, L_join, 種）の none で n_contacted が D について減らず，返事のある候補が D について入れ子か（違反 0）．"""
    by: dict[CurveKey, list[tuple[int, int]]] = {}
    for r in runs:
        if r["refill"] == NO_REFILL:
            by.setdefault(_curve_key(r), []).append((r["budget"], r["n_contacted"]))
    decreasing = []
    for key, points in by.items():
        points.sort()
        for (d0, c0), (d1, c1) in zip(points, points[1:], strict=False):
            if c1 < c0:
                decreasing.append({"condition": list(key), "budgets": [d0, d1]})
    violating = [c for c in nesting_checks if c["violations"] > 0]
    expected_count = len(by) * (n_budgets - 1)
    return {
        "conditions": len(by),
        "compared": len(nesting_checks),
        "expected_count": expected_count,
        "decreasing": decreasing,
        "n_violating_questions": sum(c["violations"] for c in nesting_checks),
        "violating_runs": violating,
        "passed": len(by) > 0
        and len(nesting_checks) == expected_count
        and not decreasing
        and not violating,
    }


def gate_g6(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """G6：L_join = 0・until_full の実行が，同じ（D, a, 種）の R = 1（none・L_join = ∞）の実行と COUNT_FIELDS で完全に一致するか．

    L_join = 0 の表は稼働中の全ノードを含むので，until_full で返事のある候補は R = 1 の候補と同じになる．contacted・wasted は照合しない．
    """
    fresh = {
        (r["budget"], r["availability"], r["seed"]): r
        for r in runs
        if r["refresh"] == MIN_REFRESH and r["join_delay"] is None and r["refill"] == NO_REFILL
    }
    compared = 0
    mismatches = []
    for r in runs:
        if r["join_delay"] != G6_JOIN_DELAY or r["refill"] != G6_REFILL:
            continue
        compared += 1
        expected = fresh[(r["budget"], r["availability"], r["seed"])]
        differing = [f for f in COUNT_FIELDS if r[f] != expected[f]]
        if differing:
            mismatches.append({**_run_id(r), "fields": differing})
    expected_count = len(fresh) * len(REFRESH_STEPS)
    return {
        "compared": compared,
        "expected_count": expected_count,
        "mismatches": mismatches,
        "passed": compared > 0 and compared == expected_count and not mismatches,
    }


# ── none の曲線と比較 ──────────────────────────────────────────────────────


def none_curve(points: Sequence[tuple[float, int]]) -> tuple[list[float], list[float]]:
    """none の点 (contacted, n_hit) を contacted の昇順に並べた折れ線の頂点（contacted の等しい点は n_hit の最大値）．

    最大値を使うのは none に有利な側に寄せ，H-B1 を厳しくするためである．
    """
    best: dict[float, int] = {}
    for x, y in points:
        best[x] = max(y, best.get(x, y))
    xs = sorted(best)
    return xs, [float(best[x]) for x in xs]


def curve_value(xs: Sequence[float], ys: Sequence[float], x: float) -> float | None:
    """折れ線の x での値 Î(x)（x が頂点の範囲の外なら None．外挿しない）．"""
    if not xs or x < xs[0] or x > xs[-1]:
        return None
    return float(np.interp(x, xs, ys))


def curve_inverse(xs: Sequence[float], ys: Sequence[float], y: float) -> float | None:
    """Î(x) = y の解 x（範囲の中で解がただ 1 つのときだけ．解が無い・2 つ以上・平らな区間で無数にあるときは None）．

    頂点で y に等しい点と，両端が y を挟む区間の内側の交点を数える（頂点を 2 つの区間で重ねて数えない）．
    """
    solutions: list[float] = []
    for i, (x0, y0) in enumerate(zip(xs, ys, strict=True)):
        if y0 == y:
            solutions.append(x0)
        if i + 1 == len(xs):
            break
        x1, y1 = xs[i + 1], ys[i + 1]
        if y0 == y1 == y:
            return None
        if (y0 - y) * (y1 - y) < 0:
            solutions.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
    return solutions[0] if len(solutions) == 1 else None


def classify_deltas(deltas: Sequence[float | None]) -> str:
    """種ごとの Δ から H-B1 → H-B3 → H-B2 の順に最初に成り立つものを返す（None の種があるか，どれも成り立たなければ UNDETERMINED）．"""
    if not deltas or any(d is None for d in deltas):
        return UNDETERMINED
    values = [float(d) for d in deltas if d is not None]
    mean = float(np.mean(values))
    if all(v > 0 for v in values) and mean >= HB1_MIN_MEAN:
        return "H-B1"
    if all(v < 0 for v in values) and mean <= HB3_MAX_MEAN:
        return "H-B3"
    if all(abs(v) <= HB2_MAX_ABS for v in values):
        return "H-B2"
    return UNDETERMINED


def compare_with_none(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """once・until_full の全ての実行の，同じ（a, R, L_join, 種）の none の曲線に対する Δ・s と，d15 の none との単純な差．

    Δ = n_hit − Î(contacted)，s = Î⁻¹(n_hit) − contacted（補う変種が節約した問あたりの問い合わせの数）．
    """
    points: dict[CurveKey, list[tuple[float, int]]] = {}
    d15: dict[CurveKey, int] = {}
    for r in runs:
        if r["refill"] != NO_REFILL:
            continue
        points.setdefault(_curve_key(r), []).append((r["contacted"], r["n_hit"]))
        if r["method"] == budget_name(D15_BUDGET):
            d15[_curve_key(r)] = r["n_hit"]
    curves = {k: none_curve(p) for k, p in points.items()}
    out = []
    for r in runs:
        if r["refill"] == NO_REFILL:
            continue
        key = _curve_key(r)
        xs, ys = curves[key]
        interpolated = curve_value(xs, ys, r["contacted"])
        inverse = curve_inverse(xs, ys, float(r["n_hit"]))
        base_d15 = d15.get(key)
        out.append(
            {
                "availability": r["availability"],
                "refresh": r["refresh"],
                "join_delay": r["join_delay"],
                "seed": r["seed"],
                "budget": r["budget"],
                "refill": r["refill"],
                "contacted": r["contacted"],
                "n_hit": r["n_hit"],
                "interpolated_n_hit": interpolated,
                "delta": None if interpolated is None else r["n_hit"] - interpolated,
                "saved_contacts": None if inverse is None else inverse - r["contacted"],
                "diff_vs_d15_none": None if base_d15 is None else r["n_hit"] - base_d15,
            }
        )
    return out


def judge_hb(comparisons: Sequence[dict[str, Any]], gates_passed: bool) -> dict[str, Any]:
    """主の点（D = MAIN_BUDGET・MAIN_REFILL・a = 0.7・R = 最大・L_join = MAIN_JOIN_DELAY）の種ごとの Δ_s で機械的に判定する．"""
    main = {
        c["seed"]: c
        for c in comparisons
        if c["availability"] == JUDGED_AVAILABILITY
        and c["refresh"] == MAX_REFRESH
        and c["join_delay"] == MAIN_JOIN_DELAY
        and c["budget"] == MAIN_BUDGET
        and c["refill"] == MAIN_REFILL
    }
    per_seed = [main[s] for s in range(N_SCHEDULE_SEEDS)]
    deltas: list[float | None] = [c["delta"] for c in per_seed]
    conclusion = classify_deltas(deltas)
    delta_mean = (
        None
        if any(d is None for d in deltas)
        else float(np.mean([d for d in deltas if d is not None]))
    )
    verdict = CONCLUSION_TEXT[conclusion] if gates_passed else "退行ゲートを通らないため判定しない"
    return {
        "budget": MAIN_BUDGET,
        "refill": MAIN_REFILL,
        "availability": JUDGED_AVAILABILITY,
        "refresh": MAX_REFRESH,
        "join_delay": MAIN_JOIN_DELAY,
        "thresholds": {
            "hb1_min_mean": HB1_MIN_MEAN,
            "hb3_max_mean": HB3_MAX_MEAN,
            "hb2_max_abs": HB2_MAX_ABS,
        },
        "per_seed": per_seed,
        "delta": deltas,
        "delta_mean": delta_mean,
        "saved_contacts": [c["saved_contacts"] for c in per_seed],
        "conclusion": conclusion if gates_passed else None,
        "verdict": verdict,
    }


# ── 記録 ──────────────────────────────────────────────────────────────────


def _condition_runs(
    runs: Sequence[dict[str, Any]], condition: tuple[float, int, int | None], refill: str
) -> dict[int, list[dict[str, Any]]]:
    """条件 (a, R, L_join) と補い方の実行を D ごとに種の順に並べる．"""
    a, refresh, join_delay = condition
    by: dict[int, list[dict[str, Any]]] = {}
    for r in runs:
        if (
            r["availability"] == a
            and r["refresh"] == refresh
            and r["join_delay"] == join_delay
            and r["refill"] == refill
        ):
            by.setdefault(r["budget"], []).append(r)
    return {d: sorted(rs, key=lambda x: x["seed"]) for d, rs in sorted(by.items())}


def record_none_curves(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """RECORDED_CONDITIONS ごとの none の曲線（D ごとの n_hit・contacted（種ごと），contacted・wasted の種の平均）．"""
    out = []
    for condition in RECORDED_CONDITIONS:
        rows = [
            {
                "budget": d,
                "n_hit": [r["n_hit"] for r in rs],
                "contacted": [r["contacted"] for r in rs],
                "contacted_mean": float(np.mean([r["contacted"] for r in rs])),
                "wasted_mean": float(np.mean([r["wasted"] for r in rs])),
            }
            for d, rs in _condition_runs(runs, condition, NO_REFILL).items()
        ]
        a, refresh, join_delay = condition
        out.append({"availability": a, "refresh": refresh, "join_delay": join_delay, "rows": rows})
    return out


def record_variants(
    runs: Sequence[dict[str, Any]], comparisons: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """RECORDED_CONDITIONS ごとの once・until_full の D ごとの Δ・s・d15 との差（種ごと），問ごとの contacted の分布と往復の情報．"""
    comp = {
        (c["availability"], c["refresh"], c["join_delay"], c["seed"], c["budget"], c["refill"]): c
        for c in comparisons
    }
    out = []
    for condition in RECORDED_CONDITIONS:
        rows = []
        for refill in REFILLED:
            for d, rs in _condition_runs(runs, condition, refill).items():
                cs = [comp[(*condition, r["seed"], d, refill)] for r in rs]
                deltas = [c["delta"] for c in cs]
                rows.append(
                    {
                        "budget": d,
                        "refill": refill,
                        "n_hit": [r["n_hit"] for r in rs],
                        "contacted": [r["contacted"] for r in rs],
                        "contacted_mean": float(np.mean([r["contacted"] for r in rs])),
                        "delta": deltas,
                        "delta_mean": None
                        if any(x is None for x in deltas)
                        else float(np.mean(deltas)),
                        "saved_contacts": [c["saved_contacts"] for c in cs],
                        "diff_vs_d15_none": [c["diff_vs_d15_none"] for c in cs],
                        "second_round_rate_mean": float(
                            np.mean([r["second_round_rate"] for r in rs])
                        ),
                        "rounds_mean": float(np.mean([r["rounds_mean"] for r in rs])),
                        "rounds_max": max(r["rounds_max"] for r in rs),
                        "contacted_max": max(r["contacted_max"] for r in rs),
                        "contacted_p95_mean": float(np.mean([r["contacted_p95"] for r in rs])),
                    }
                )
        a, refresh, join_delay = condition
        out.append({"availability": a, "refresh": refresh, "join_delay": join_delay, "rows": rows})
    return out


def aggregate_runs(
    runs: Sequence[dict[str, Any]],
    inclusion_checks: Sequence[dict[str, Any]],
    nesting_checks: Sequence[dict[str, Any]],
    refill_reference: dict[str, Any],
    budgets: Sequence[int],
    m: int,
    n_test: int,
) -> dict[str, Any]:
    """退行ゲート・判定・記録をまとめる．refill_reference は Iteration 18 の churn-refill-eval の metrics.json（G1 の基準）．"""
    n_conditions = (
        len(AVAILABILITIES) * len(REFRESH_STEPS) * len(JOIN_DELAY_GRID) * N_SCHEDULE_SEEDS
    )
    gates = {
        "g1": gate_g1(runs, refill_reference, m, n_test),
        "g2": gate_g2(runs),
        "g3": gate_g4(runs),
        "g4": gate_g4_nesting(runs, nesting_checks, len(budgets)),
        "g5": refill_inclusion_gate(inclusion_checks, len(budgets) * n_conditions),
        "g6": gate_g6(runs),
    }
    gates_passed = all(g["passed"] for g in gates.values())
    comparisons = compare_with_none(runs)
    return {
        "gates": {**gates, "passed": gates_passed},
        "hb": judge_hb(comparisons, gates_passed),
        "none_curves": record_none_curves(runs),
        "variants": record_variants(runs, comparisons),
        "comparisons": comparisons,
        "summary": summarize_by_refill(runs),
        "losses": summarize_losses_by_refill(runs),
        "runs": list(runs),
    }


def evaluate(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    placement: Placement,
    refill_reference: dict[str, Any],
) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．refill_reference は Iteration 18 の churn-refill-eval の metrics.json（G1 の基準）．"""
    from atrium.data_enronqa import kmeans_centroids

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    names = [ib.name for ib in inboxes]
    test = load_query_set(cfg, paths, "test")
    n_test = len(test.qids)
    # 採点・番号の付け方は churn_refill_eval.evaluate と同じ（G1 で Iteration 18 との完全な一致を確かめる）
    node_index = {sid: i for i, node in enumerate(placement.nodes) for sid in node.shard_ids}
    node_of = np.array([node_index[n] for n in names], np.int64)
    device_of_inbox = device_index_of_inbox(names, placement)
    device_node = node_of_device(device_of_inbox, node_of)
    n_devices = len(device_node)
    if max(BUDGETS) > n_devices:
        raise ValueError(f"budget {max(BUDGETS)} exceeds {n_devices} devices")
    centers = [kmeans_centroids(ib.emb, e.n_centroids, seed) for ib in inboxes]
    order = np.random.default_rng(seed).permutation(n_test)
    first = score_multi(test.emb, centers)[order]
    second = score_flood_dense(test.emb, inboxes)[order]
    gold = [test.gold[i] for i in order]
    logger.info("scored %d questions", n_test)
    runs, inclusion_checks, nesting_checks = simulate_runs(
        first,
        second,
        gold,
        node_of,
        device_of_inbox,
        device_node,
        len(placement.nodes),
        m,
        BUDGETS,
        seed,
    )
    return {
        "n_inboxes": len(names),
        "n_nodes": len(placement.nodes),
        "n_devices": n_devices,
        "n_test": n_test,
        "m": m,
        "n_centroids": e.n_centroids,
        "mean_up_steps": MEAN_UP_STEPS,
        "n_schedule_seeds": N_SCHEDULE_SEEDS,
        "budgets": list(BUDGETS),
        "join_delays": list(JOIN_DELAY_GRID),
        "refills": list(REFILLS),
        "contacted_percentile": CONTACTED_PERCENTILE,
        **aggregate_runs(
            runs, inclusion_checks, nesting_checks, refill_reference, BUDGETS, m, n_test
        ),
        "duration_s": time.perf_counter() - start,
    }


# ── 報告 ──────────────────────────────────────────────────────────────────


def _num(value: float | None, digits: int) -> str:
    """None を「-」にした数値の表記．"""
    return "-" if value is None else f"{value:.{digits}f}"


def _nums(values: Sequence[float | None], digits: int) -> str:
    """種ごとの数値の表記．"""
    return ", ".join(_num(v, digits) for v in values)


def _diffs(values: Sequence[int | None]) -> str:
    """種ごとの符号付きの件数の表記．"""
    return ", ".join("-" if v is None else f"{v:+d}" for v in values)


def _condition_title(entry: dict[str, Any]) -> str:
    """記録の表の条件の表記．"""
    return (
        f"a = {entry['availability']}，R = {entry['refresh']}，"
        f"L_join = {delay_label(entry['join_delay'])}"
    )


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    gates = metrics["gates"]
    g1, g2, g3, g4, g5, g6 = (gates[k] for k in ("g1", "g2", "g3", "g4", "g5", "g6"))
    hb = metrics["hb"]

    def ok(passed: bool) -> str:
        return "通過" if passed else "**不通過**"

    lines = [
        "# 返事の無い枠を補う方式と最初から多めに送る方式の，同じ問い合わせの数での hit の比較（Iteration 19）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個（ノード {metrics['n_nodes']} 台，デバイス {metrics['n_devices']} 台），"
        f"test {metrics['n_test']} 問，m = {metrics['m']}，中心の数 C = {metrics['n_centroids']}，"
        f"稼働中の平均の長さ {metrics['mean_up_steps']} 問，時刻表の種 {metrics['n_schedule_seeds']} 個",
        f"- device_probe の D：{', '.join(str(d) for d in metrics['budgets'])}．補い方：{', '.join(metrics['refills'])}",
        f"- 参加の通知の遅れ L_join（問）：{', '.join(delay_label(d) for d in metrics['join_delays'])}"
        "（inf は通知なし．離脱は通知しない）",
        f"- 実行 {len(metrics['runs'])} 件",
        f"- 退行ゲート：{'全て通過' if gates['passed'] else '**不通過あり（判定しない）**'}",
        "",
        "## 退行ゲート",
        "",
        f"- G1（D = {g1['full_budget']} の全ての補い方と D = {g1['none_budget']} の none が Iteration 18 と完全に一致）："
        f"{ok(g1['passed'])}（照合 {g1['matched']} 件 / 期待 {g1['expected_count']} 件，不一致 {len(g1['mismatches'])} 件，"
        f"こちらの欠け {len(g1['missing_here'])} 件，基準の欠け {len(g1['missing_in_reference'])} 件，"
        f"m・n_test の一致 {g1['same_setup']}）",
        f"- G2（a = 1.0 または R = 1 の once・until_full が none と完全に一致）：{ok(g2['passed'])}"
        f"（照合 {g2['compared']} 件 / 期待 {g2['expected_count']} 件，不一致 {len(g2['mismatches'])} 件）",
        f"- G3（hit と (i)〜(iv) の和が問の数に等しい）：{ok(g3['passed'])}（照合 {len(metrics['runs'])} 件，"
        f"違反 {len(g3['violations'])} 件）",
        f"- G4（none の contacted が D について減らず，返事のある候補が D について入れ子）：{ok(g4['passed'])}"
        f"（条件 {g4['conditions']} 個，入れ子の照合 {g4['compared']} 組 / 期待 {g4['expected_count']} 組，"
        f"contacted の減少 {len(g4['decreasing'])} 件，入れ子の違反 {g4['n_violating_questions']} 問）",
        f"- G5（全ての D で none ⊆ once ⊆ until_full）：{ok(g5['passed'])}"
        f"（照合 {g5['compared']} 組 / 期待 {g5['expected_count']} 組，違反 {g5['n_violating_questions']} 問）",
        f"- G6（L_join = {G6_JOIN_DELAY}・{G6_REFILL} が同じ (D, a, 種) の R = 1 と件数で完全に一致）：{ok(g6['passed'])}"
        f"（照合 {g6['compared']} 件 / 期待 {g6['expected_count']} 件，不一致 {len(g6['mismatches'])} 件）",
        "",
        f"## 主の点（device_probe_d{hb['budget']}，{hb['refill']}，a = {hb['availability']}，R = {hb['refresh']}，"
        f"L_join = {hb['join_delay']}）",
        "",
        "Î(c) は同じ種の none の D の曲線を contacted で補間した n_hit．Δ = n_hit − Î(c)，s = Î⁻¹(n_hit) − c．",
        "",
        "| 種 | n_hit | contacted | Î(c) | Δ（問） | s（問あたりの問い合わせ） | d15 の none との差（問） |",
        "|---|---|---|---|---|---|---|",
        *(
            f"| {c['seed']} | {c['n_hit']} | {c['contacted']:.4f} | {_num(c['interpolated_n_hit'], 2)} "
            f"| {_num(c['delta'], 2)} | {_num(c['saved_contacts'], 4)} | {_diffs([c['diff_vs_d15_none']])} |"
            for c in hb["per_seed"]
        ),
        "",
        f"- Δ の平均：{_num(hb['delta_mean'], 2)} 問",
        f"- 機械的な判定（H-B1：全ての種で Δ > 0 かつ平均 ≥ {HB1_MIN_MEAN:g}，H-B3：全ての種で Δ < 0 かつ平均 ≤ {HB3_MAX_MEAN:g}，"
        f"H-B2：全ての種で |Δ| ≤ {HB2_MAX_ABS:g} の順）：{hb['verdict']}",
    ]
    for curve in metrics["none_curves"]:
        lines += [
            "",
            f"## none の曲線（記録．{_condition_title(curve)}）",
            "",
            "| D | n_hit（種ごと） | contacted（種ごと） | contacted の平均 | wasted の平均 |",
            "|---|---|---|---|---|",
            *(
                f"| {r['budget']} | {', '.join(str(v) for v in r['n_hit'])} | {_nums(r['contacted'], 4)} "
                f"| {r['contacted_mean']:.4f} | {r['wasted_mean']:.4f} |"
                for r in curve["rows"]
            ),
        ]
    for variants in metrics["variants"]:
        lines += [
            "",
            f"## 補う変種の Δ・s・往復（記録．{_condition_title(variants)}）",
            "",
            "contacted の最大は種の最大，95 パーセンタイルは種の平均．",
            "",
            "| D | 補い方 | contacted の平均 | Δ（種ごと） | Δ の平均 | s（種ごと） | d15 の none との差（種ごと） "
            "| 2 往復目の要る問の割合 | 往復の数の平均 | 往復の数の最大 | contacted の最大 | contacted の 95 パーセンタイル |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
            *(
                f"| {r['budget']} | {r['refill']} | {r['contacted_mean']:.4f} | {_nums(r['delta'], 2)} "
                f"| {_num(r['delta_mean'], 2)} | {_nums(r['saved_contacts'], 4)} | {_diffs(r['diff_vs_d15_none'])} "
                f"| {r['second_round_rate_mean']:.4f} | {r['rounds_mean']:.3f} | {r['rounds_max']} "
                f"| {r['contacted_max']} | {r['contacted_p95_mean']:.2f} |"
                for r in variants["rows"]
            ),
        ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
