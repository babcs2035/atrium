"""離脱の通知の遅れ L_leave ごとの無駄な問い合わせと hit の回復のオフラインの評価（Iteration 17）．

`atrium churn-leave-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（規則は journal の Iteration 17 の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. 採点・時刻表・質問の順番・方式の構成・経路の選択は Iteration 16（atrium.churn_join_eval）と同じにする．
2. メンバー表は，直前の更新 u(t) = ⌊t/R⌋·R の時点の状態に，区間 (u(t), t] の事象のうち通知の届いたものを重ねる．
   参加（online[j] かつ ¬online[j−1]）は j ≤ t − L_join，離脱（¬online[j] かつ online[j−1]）は j ≤ t − L_leave のときに届く．
   届いた事象のうち最も新しいものに従い（参加なら表に入れ，離脱なら表から消す），届いた事象が無ければ u(t) の時点の状態のままとする．
   離脱の通知の後に再び参加したノードは，参加の事象の方が新しいので，参加の通知が届いた時点から見える（backlog B47 (d)）．
   通知なし（L_leave = ∞．leave_delay = None）は Iteration 16 の join_notified_view と同じ表になる．L_leave ≥ R では離脱の通知は効かない．
3. L_join ∈ {0, 100} × L_leave ∈ {∞, 0, 10, 100} × 稼働率 a × 更新間隔 R × 時刻表の種ごとに，Iteration 16 の 8 構成を同じ規則で選ぶ．
   L_join = 0 が判定の条件で，L_join = 100 は記録だけの感度の格子である（B47 の A1・E1）．
4. 退行ゲート G1〜G5，判定 H-L1，記録（全方式の L_join・L_leave ごとの ρ_leave・hit・contacted・wasted・(ii) の件数，
   a = 0.9・0.5 の ρ_leave，E-2 相当，a = 0.7・L_join = 0 の R = 1 → 最大の問ごとの遷移表）を出す．
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
from atrium.churn_cascade_eval import (
    COUNT_FIELDS,
    JUDGED_CASCADES,
    SINGLE_STAGE_METHODS,
    Cascade,
    LossBreakdown,
    gate_g4,
    node_of_device,
    run_record,
)
from atrium.churn_eval import (
    AVAILABILITIES,
    JUDGED_AVAILABILITY,
    MEAN_UP_STEPS,
    N_SCHEDULE_SEEDS,
    REFRESH_STEPS,
    STAT_FIELDS,
    BoolArray,
    ChurnOutcome,
    simulate_availability,
    stale_view,
)
from atrium.churn_join_eval import (
    E2_COUNT_FIELDS,
    LOSS_CLASSES,
    NO_NOTICE_LABEL,
    delay_label,
    gate_g1,
    loss_class,
    route_methods,
    transition_table,
)
from atrium.config import AtriumConfig
from atrium.device_probe_eval import device_index_of_inbox
from atrium.manifest import Placement
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

# 事前登録の離脱の通知の遅れ（問の数）．None は通知なし（L_leave = ∞）で，先頭に置いて同じ時刻表の基準を先に求める
LEAVE_DELAYS = (0, 10, 100)
LEAVE_DELAY_GRID: tuple[int | None, ...] = (None, *LEAVE_DELAYS)
# 参加の通知の遅れ．判定は L_join = 0 で，100 は記録だけの感度の格子（B47 の A1・E1）．判定の値を先頭に置く
JUDGED_JOIN_DELAY = 0
SENSITIVITY_JOIN_DELAY = 100
JOIN_DELAY_GRID = (JUDGED_JOIN_DELAY, SENSITIVITY_JOIN_DELAY)
MIN_REFRESH = min(REFRESH_STEPS)
MAX_REFRESH = max(REFRESH_STEPS)
# H-L1：device_probe（D = 12）の a = 0.7・R = 最大・L_join = 0 で，L_leave = 10 の ρ_leave が全ての種でこの値以上
HL1_METHOD = "device_probe_d12"
HL1_LEAVE_DELAY = 10
HL1_MIN_RECOVERY = 0.75
# G5：(L_join, L_leave) = (0, 0) では表が稼働中の集合そのものになるので，R = 1 と同じになる（主の判定には使わない）
G5_JOIN_DELAY = 0
G5_LEAVE_DELAY = 0
# E-2 相当：device_probe（D = 12）で L_join = 0・L_leave = 10 にしても残る低下と，その件数の内訳の増分
E2_METHOD = HL1_METHOD
E2_LEAVE_DELAY = HL1_LEAVE_DELAY
# 判定の a 以外で ρ_leave を記録する稼働率（a = 1 は低下が無いので分母が 0 になる）
EXTRA_RECOVERY_AVAILABILITIES = (0.9, 0.5)
# 問ごとの遷移表は a = 0.7・L_join = 0 に限る（出力の大きさを抑えるため）
TRANSITION_AVAILABILITY = JUDGED_AVAILABILITY
TRANSITION_JOIN_DELAY = JUDGED_JOIN_DELAY
RUN_KEY_FIELDS = ("method", "availability", "refresh", "seed", "join_delay")
RunKey = tuple[str, float, int, int, int]
ConditionKey = tuple[str, float, int, int, int | None]


def _latest_notified_event(events: BoolArray, last: I64Array, delay: int | None) -> I64Array:
    """各刻み t で，区間 (last[t], t − delay] に起きた事象のうち最も新しい刻み（無ければ −1．delay が None なら全て −1）．"""
    n_steps, n_nodes = events.shape
    if delay is None:
        none: I64Array = np.full((n_steps, n_nodes), -1, np.int64)
        return none
    t = np.arange(n_steps, dtype=np.int64)
    latest: I64Array = np.maximum.accumulate(np.where(events, t[:, None], -1), axis=0)
    end = t - delay
    # 区間が空の刻みは end を 0 に丸めた値を引くが，end > last で落とす
    at = latest[np.clip(end, 0, None)]
    valid = (end > last)[:, None] & (at > last[:, None])
    out: I64Array = np.where(valid, at, -1)
    return out


def leave_notified_view(
    online: BoolArray, refresh: int, join_delay: int | None, leave_delay: int | None
) -> BoolArray:
    """refresh 問ごとの更新に，参加（遅れ join_delay 問）と離脱（遅れ leave_delay 問）の通知を重ねたメンバー表．

    届いた事象のうち最も新しいものに従う．同じ刻みに同じノードの参加と離脱は起きないので，新しさの比較は一意に決まる．
    leave_delay が None なら churn_join_eval.join_notified_view(online, refresh, join_delay) と同じ表になる．
    """
    for d in (join_delay, leave_delay):
        if d is not None and d < 0:
            raise ValueError(f"delay must be >= 0 or None, got {d}")
    stale = stale_view(online, refresh)
    t = np.arange(online.shape[0], dtype=np.int64)
    last = (t // refresh) * refresh
    # 刻み 0 は直前の状態が無いので事象として数えない（u(t) ≥ 0 なので区間 (u(t), t − L] にも入らない）
    joined = np.zeros_like(online)
    joined[1:] = online[1:] & ~online[:-1]
    left = np.zeros_like(online)
    left[1:] = ~online[1:] & online[:-1]
    join_at = _latest_notified_event(joined, last, join_delay)
    leave_at = _latest_notified_event(left, last, leave_delay)
    notified = (join_at >= 0) | (leave_at >= 0)
    out: BoolArray = np.where(notified, join_at > leave_at, stale)
    return out


def leave_run_record(
    method: str,
    availability: float,
    refresh: int,
    join_delay: int,
    leave_delay: int | None,
    seed: int,
    out: ChurnOutcome,
    loss: LossBreakdown,
) -> dict[str, Any]:
    """1 実行の記録（Iteration 15 の run_record に参加・離脱の通知の遅れを足す．離脱の通知なしは None）．"""
    return {
        **run_record(method, availability, refresh, seed, out, loss),
        "join_delay": join_delay,
        "leave_delay": leave_delay,
    }


def _run_key(r: dict[str, Any]) -> RunKey:
    """（方式，稼働率，更新間隔，種，参加の通知の遅れ）．"""
    return (r["method"], r["availability"], r["refresh"], r["seed"], r["join_delay"])


def _run_id(r: dict[str, Any]) -> dict[str, Any]:
    """ゲートの不一致の記録に載せる実行の識別子．"""
    return {k: r[k] for k in (*RUN_KEY_FIELDS, "leave_delay")}


def _group_by_condition(runs: Sequence[dict[str, Any]]) -> dict[ConditionKey, list[dict[str, Any]]]:
    """（方式，稼働率，更新間隔，L_join，L_leave）ごとに時刻表の種の実行をまとめる．"""
    groups: dict[ConditionKey, list[dict[str, Any]]] = {}
    for r in runs:
        key = (r["method"], r["availability"], r["refresh"], r["join_delay"], r["leave_delay"])
        groups.setdefault(key, []).append(r)
    return groups


def _condition_entry(key: ConditionKey) -> dict[str, Any]:
    """summary・losses の行の条件の項目．"""
    method, availability, refresh, join_delay, leave_delay = key
    return {
        "method": method,
        "availability": availability,
        "refresh": refresh,
        "join_delay": join_delay,
        "leave_delay": leave_delay,
    }


def summarize_by_delays(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """条件（_group_by_condition）ごとに，時刻表の種をまたいだ平均・最小・最大．"""
    summary = []
    for key, rs in _group_by_condition(runs).items():
        entry = _condition_entry(key)
        for f in STAT_FIELDS:
            values = [float(x[f]) for x in rs]
            entry[f] = {"mean": float(np.mean(values)), "min": min(values), "max": max(values)}
        summary.append(entry)
    return summary


def summarize_losses_by_delays(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """条件（_group_by_condition）ごとの件数の種の和（二段の方式以外の内訳は None）．"""
    out = []
    for key, rs in _group_by_condition(runs).items():
        entry = _condition_entry(key)
        for f in COUNT_FIELDS:
            values = [x[f] for x in rs]
            entry[f] = None if any(v is None for v in values) else int(sum(values))
        out.append(entry)
    return out


# ── 退行ゲート ──────────────────────────────────────────────────────────────


def gate_join_reference(
    runs: Sequence[dict[str, Any]],
    join_reference: dict[str, Any],
    join_delay: int,
    m: int,
    n_test: int,
) -> dict[str, Any]:
    """G1・G2：L_join = join_delay・L_leave = ∞ の全ての実行が，Iteration 16 の delay = join_delay の runs と一致するか．

    照合は churn_join_eval.gate_g1（1/n_test 以内，欠け無し，件数の None の有無も一致）で行う．
    Iteration 16 の runs は（方式，a，R，種）が L ごとに重なるので，先に delay で絞らないと基準が 1 つに潰れる．
    """
    ours = [
        {**r, "delay": None}
        for r in runs
        if r["join_delay"] == join_delay and r["leave_delay"] is None
    ]
    reference = {
        "m": join_reference["m"],
        "n_test": join_reference["n_test"],
        "runs": [r for r in join_reference["runs"] if r["delay"] == join_delay],
    }
    return {"join_delay": join_delay, **gate_g1(ours, reference, m, n_test)}


def gate_g3(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """G3：L_leave ≥ R の実行が，同じ（方式，a，R，種，L_join）の L_leave = ∞ と全ての項目で完全に一致するか．"""
    base = {_run_key(r): r for r in runs if r["leave_delay"] is None}
    fields = (*STAT_FIELDS, *COUNT_FIELDS)
    mismatches = []
    compared = 0
    for r in runs:
        if r["leave_delay"] is None or r["leave_delay"] < r["refresh"]:
            continue
        compared += 1
        expected = base[_run_key(r)]
        differing = [f for f in fields if r[f] != expected[f]]
        if differing:
            mismatches.append({**_run_id(r), "fields": differing})
    expected_count = sum(1 for r in base.values() for d in LEAVE_DELAYS if d >= r["refresh"])
    return {
        "compared": compared,
        "expected_count": expected_count,
        "mismatches": mismatches,
        "passed": compared > 0 and compared == expected_count and not mismatches,
    }


def gate_g5(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """G5：(L_join, L_leave) = (0, 0) の実行が，同じ（方式，a，種）の R = 1 の実行と n_hit と全ての件数で完全に一致するか．

    このとき表は稼働中の集合そのものになるので，構造上そうなる．R = 1 の基準は L_join = 0・L_leave = ∞ の実行とする
    （R = 1 では通知の窓が常に空なので，どの遅れでも同じ表になる）．
    """
    fresh = {
        (r["method"], r["availability"], r["seed"]): r
        for r in runs
        if r["refresh"] == MIN_REFRESH
        and r["join_delay"] == G5_JOIN_DELAY
        and r["leave_delay"] is None
    }
    mismatches = []
    compared = 0
    for r in runs:
        if r["join_delay"] != G5_JOIN_DELAY or r["leave_delay"] != G5_LEAVE_DELAY:
            continue
        compared += 1
        expected = fresh[(r["method"], r["availability"], r["seed"])]
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


# ── 判定と記録 ──────────────────────────────────────────────────────────────


def _verdict(flag: bool, gates_passed: bool, yes: str, no: str) -> str:
    """判定の文言（ゲートを通らなければ判定しない）．"""
    if not gates_passed:
        return "退行ゲートを通らないため判定しない"
    return yes if flag else no


def recovery_by_method(
    runs: Sequence[dict[str, Any]], methods: Sequence[str], availability: float
) -> dict[str, Any]:
    """稼働率 availability で，方式・L_join・L_leave ごとの ρ_leave（種ごと）と，R = 最大の hit・(ii) の件数（種ごと）・contacted・wasted（種の平均）．

    ρ_leave = (hit(R=最大, L_join, L_leave) − hit(R=最大, L_join, ∞)) / (hit(R=1) − hit(R=最大, L_join, ∞))．
    問の数は実行によらず等しいので件数で計算する．分母が 0 以下の種は None とする．
    """
    by = {
        (r["method"], r["seed"], r["refresh"], r["join_delay"], r["leave_delay"]): r
        for r in runs
        if r["availability"] == availability
    }
    seeds = range(N_SCHEDULE_SEEDS)
    out: dict[str, Any] = {}
    for method in methods:
        fresh = [by[(method, s, MIN_REFRESH, JUDGED_JOIN_DELAY, None)] for s in seeds]
        per_join: dict[str, Any] = {}
        for join_delay in JOIN_DELAY_GRID:
            at_max = {
                ld: [by[(method, s, MAX_REFRESH, join_delay, ld)] for s in seeds]
                for ld in LEAVE_DELAY_GRID
            }
            rho: dict[str, list[float | None]] = {}
            for leave_delay in LEAVE_DELAYS:
                per_seed: list[float | None] = []
                for f, base, notified in zip(fresh, at_max[None], at_max[leave_delay], strict=True):
                    denom = f["n_hit"] - base["n_hit"]
                    per_seed.append(
                        (notified["n_hit"] - base["n_hit"]) / denom if denom > 0 else None
                    )
                rho[delay_label(leave_delay)] = per_seed
            per_join[delay_label(join_delay)] = {
                "hit": {delay_label(d): [r["hit"] for r in rs] for d, rs in at_max.items()},
                "n_hit": {delay_label(d): [r["n_hit"] for r in rs] for d, rs in at_max.items()},
                "n_gold_invisible": {
                    delay_label(d): [r["n_gold_invisible"] for r in rs] for d, rs in at_max.items()
                },
                "rho": rho,
                "contacted": {
                    delay_label(d): float(np.mean([r["contacted"] for r in rs]))
                    for d, rs in at_max.items()
                },
                "wasted": {
                    delay_label(d): float(np.mean([r["wasted"] for r in rs]))
                    for d, rs in at_max.items()
                },
            }
        out[method] = {
            "hit_fresh": [r["hit"] for r in fresh],
            "n_hit_fresh": [r["n_hit"] for r in fresh],
            "by_join_delay": per_join,
        }
    return out


def judge_hl1(recovery: dict[str, Any], gates_passed: bool) -> dict[str, Any]:
    """H-L1：HL1_METHOD の L_join = 0・L_leave = HL1_LEAVE_DELAY の ρ_leave が全ての種で HL1_MIN_RECOVERY 以上か（分母が 0 以下の種は不成立）．"""
    entry = recovery[HL1_METHOD]
    joined = entry["by_join_delay"][delay_label(JUDGED_JOIN_DELAY)]
    rho: list[float | None] = joined["rho"][delay_label(HL1_LEAVE_DELAY)]
    supported = all(x is not None and x >= HL1_MIN_RECOVERY for x in rho)
    return {
        "method": HL1_METHOD,
        "availability": JUDGED_AVAILABILITY,
        "refresh": MAX_REFRESH,
        "join_delay": JUDGED_JOIN_DELAY,
        "leave_delay": HL1_LEAVE_DELAY,
        "min_recovery": HL1_MIN_RECOVERY,
        "hit_fresh": entry["hit_fresh"],
        "hit_no_leave_notice": joined["hit"][NO_NOTICE_LABEL],
        "hit_notified": joined["hit"][delay_label(HL1_LEAVE_DELAY)],
        "n_hit_fresh": entry["n_hit_fresh"],
        "n_hit_no_leave_notice": joined["n_hit"][NO_NOTICE_LABEL],
        "n_hit_notified": joined["n_hit"][delay_label(HL1_LEAVE_DELAY)],
        "rho": rho,
        "supported": supported,
        "verdict": _verdict(supported, gates_passed, "支持", "支持されない"),
    }


def record_e2(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """E-2 相当（記録）：E2_METHOD の a = 0.7・L_join = 0 で，R = 1 → 最大・L_leave = E2_LEAVE_DELAY にしても残る低下と，件数の内訳の増分（種ごと）．"""
    by = {
        (r["seed"], r["refresh"], r["join_delay"], r["leave_delay"]): r
        for r in runs
        if r["method"] == E2_METHOD and r["availability"] == JUDGED_AVAILABILITY
    }
    per_seed = []
    for s in range(N_SCHEDULE_SEEDS):
        fresh = by[(s, MIN_REFRESH, JUDGED_JOIN_DELAY, None)]
        notified = by[(s, MAX_REFRESH, JUDGED_JOIN_DELAY, E2_LEAVE_DELAY)]
        base = by[(s, MAX_REFRESH, JUDGED_JOIN_DELAY, None)]
        per_seed.append(
            {
                "seed": s,
                "hit_fresh": fresh["hit"],
                "hit_notified": notified["hit"],
                "hit_no_leave_notice": base["hit"],
                "remaining_drop": fresh["n_hit"] - notified["n_hit"],
                "recovered": notified["n_hit"] - base["n_hit"],
                "count_gain": {f: notified[f] - fresh[f] for f in E2_COUNT_FIELDS},
            }
        )
    return {
        "method": E2_METHOD,
        "availability": JUDGED_AVAILABILITY,
        "refresh": MAX_REFRESH,
        "join_delay": JUDGED_JOIN_DELAY,
        "leave_delay": E2_LEAVE_DELAY,
        "per_seed": per_seed,
    }


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
    cascades: Sequence[Cascade],
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """時刻表ごとに，更新間隔と通知の遅れを変えて全ての構成を選び，実行の記録と遷移表を返す．

    乱数の引き方・問の数・ノードの数は EQ7a・Iteration 15・16 と同じにする（G1・G2 で Iteration 16 と照合する）．
    """
    n_steps = len(gold)
    runs: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    for availability in AVAILABILITIES:
        for s in range(N_SCHEDULE_SEEDS):
            rng = np.random.default_rng([seed, s, round(availability * 100)])
            online = simulate_availability(n_nodes, n_steps, availability, MEAN_UP_STEPS, rng)
            # REFRESH_STEPS は昇順，JOIN_DELAY_GRID は判定の L_join が先頭，LEAVE_DELAY_GRID は None が先頭なので，
            # 遷移表の基準（R = 1・L_join = 0・L_leave = ∞）を R = 最大より先に求められる
            fresh_class: dict[str, I64Array] = {}
            for refresh in REFRESH_STEPS:
                for join_delay in JOIN_DELAY_GRID:
                    for leave_delay in LEAVE_DELAY_GRID:
                        view = leave_notified_view(online, refresh, join_delay, leave_delay)
                        routed = route_methods(
                            first,
                            second,
                            gold,
                            node_of,
                            device_of_inbox,
                            device_node,
                            view,
                            online,
                            m,
                            cascades,
                        )
                        for name, out, loss in routed:
                            runs.append(
                                leave_run_record(
                                    name,
                                    availability,
                                    refresh,
                                    join_delay,
                                    leave_delay,
                                    s,
                                    out,
                                    loss,
                                )
                            )
                            if (
                                availability != TRANSITION_AVAILABILITY
                                or join_delay != TRANSITION_JOIN_DELAY
                            ):
                                continue
                            if refresh == MIN_REFRESH and leave_delay is None:
                                fresh_class[name] = loss_class(out, loss)
                            elif refresh == MAX_REFRESH:
                                table = transition_table(fresh_class[name], loss_class(out, loss))
                                transitions.append(
                                    {
                                        "method": name,
                                        "join_delay": join_delay,
                                        "leave_delay": leave_delay,
                                        "seed": s,
                                        "table": table,
                                    }
                                )
        logger.info("availability %.2f done", availability)
    return runs, transitions


def aggregate_runs(
    runs: Sequence[dict[str, Any]],
    transitions: Sequence[dict[str, Any]],
    join_reference: dict[str, Any],
    methods: Sequence[str],
    m: int,
    n_test: int,
) -> dict[str, Any]:
    """退行ゲート・判定・記録をまとめる．join_reference は Iteration 16 の churn-join-eval の metrics.json（G1・G2 の基準）．"""
    gates = {
        "g1": gate_join_reference(runs, join_reference, JUDGED_JOIN_DELAY, m, n_test),
        "g2": gate_join_reference(runs, join_reference, SENSITIVITY_JOIN_DELAY, m, n_test),
        "g3": gate_g3(runs),
        "g4": gate_g4(runs),
        "g5": gate_g5(runs),
    }
    gates_passed = all(g["passed"] for g in gates.values())
    recovery = recovery_by_method(runs, methods, JUDGED_AVAILABILITY)
    return {
        "gates": {**gates, "passed": gates_passed},
        "hl1": judge_hl1(recovery, gates_passed),
        "recovery": recovery,
        "recovery_other_availabilities": {
            str(a): recovery_by_method(runs, [HL1_METHOD], a) for a in EXTRA_RECOVERY_AVAILABILITIES
        },
        "e2": record_e2(runs),
        "transitions": {
            "availability": TRANSITION_AVAILABILITY,
            "join_delay": TRANSITION_JOIN_DELAY,
            "from_refresh": MIN_REFRESH,
            "to_refresh": MAX_REFRESH,
            "tables": list(transitions),
        },
        "summary": summarize_by_delays(runs),
        "losses": summarize_losses_by_delays(runs),
        "runs": list(runs),
    }


def evaluate(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    placement: Placement,
    join_reference: dict[str, Any],
) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．join_reference は Iteration 16 の churn-join-eval の metrics.json（G1・G2 の基準）．"""
    from atrium.data_enronqa import kmeans_centroids

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    names = [ib.name for ib in inboxes]
    test = load_query_set(cfg, paths, "test")
    n_test = len(test.qids)
    # 採点・番号の付け方・構成は churn_join_eval.evaluate と同じ（G1・G2 で Iteration 16 との一致を確かめる）
    node_index = {sid: i for i, node in enumerate(placement.nodes) for sid in node.shard_ids}
    node_of = np.array([node_index[n] for n in names], np.int64)
    device_of_inbox = device_index_of_inbox(names, placement)
    device_node = node_of_device(device_of_inbox, node_of)
    n_devices = len(device_node)
    centers = [kmeans_centroids(ib.emb, e.n_centroids, seed) for ib in inboxes]
    order = np.random.default_rng(seed).permutation(n_test)
    first = score_multi(test.emb, centers)[order]
    second = score_flood_dense(test.emb, inboxes)[order]
    gold = [test.gold[i] for i in order]
    logger.info("scored %d questions", n_test)
    full = (
        Cascade(f"centroid_probe_k{len(names)}", "centroid_probe", len(names)),
        Cascade(f"device_probe_d{n_devices}", "device_probe", n_devices),
    )
    cascades = (*JUDGED_CASCADES, *full)
    methods = [*SINGLE_STAGE_METHODS, *(c.name for c in cascades)]
    runs, transitions = simulate_runs(
        first,
        second,
        gold,
        node_of,
        device_of_inbox,
        device_node,
        len(placement.nodes),
        m,
        cascades,
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
        "join_delays": list(JOIN_DELAY_GRID),
        "leave_delays": list(LEAVE_DELAY_GRID),
        "methods": methods,
        "cascades": [{"name": c.name, "kind": c.kind, "size": c.size} for c in cascades],
        "loss_classes": list(LOSS_CLASSES),
        **aggregate_runs(runs, transitions, join_reference, methods, m, n_test),
        "duration_s": time.perf_counter() - start,
    }


# ── 報告 ──────────────────────────────────────────────────────────────────


def _num(value: float | None, digits: int) -> str:
    """None を「-」にした数値の表記．"""
    return "-" if value is None else f"{value:.{digits}f}"


def _count(value: int | None) -> str:
    """None を「-」にした件数の表記．"""
    return "-" if value is None else str(value)


def _stat(s: dict[str, float]) -> str:
    """種をまたいだ平均 [最小, 最大] の表記．"""
    return f"{s['mean']:.4f} [{s['min']:.4f}, {s['max']:.4f}]"


def _rho_text(values: list[float | None]) -> str:
    """種ごとの ρ_leave の表記（全ての種で分母が 0 以下なら「定義できない」）．"""
    if all(v is None for v in values):
        return "定義できない（分母が 0 以下）"
    return ", ".join(_num(v, 4) for v in values)


def _delay_order(delay: int | None) -> int:
    """報告の行の並び（通知なしを先頭にする）．"""
    return -1 if delay is None else delay


def _pooled_transitions(metrics: dict[str, Any]) -> dict[tuple[str, int | None], list[list[int]]]:
    """（方式，L_leave）ごとの遷移表の種の和．"""
    n_classes = len(LOSS_CLASSES)
    pooled: dict[tuple[str, int | None], list[list[int]]] = {}
    for t in metrics["transitions"]["tables"]:
        key = (t["method"], t["leave_delay"])
        acc = pooled.setdefault(key, [[0] * n_classes for _ in LOSS_CLASSES])
        for i, row in enumerate(t["table"]):
            for j, v in enumerate(row):
                acc[i][j] += v
    return pooled


def _recovery_rows(name: str, rec: dict[str, Any]) -> list[str]:
    """1 つの方式の L_join・L_leave ごとの ρ_leave・hit・(ii)・contacted・wasted の表の行．"""
    rows = []
    for join_label, entry in rec["by_join_delay"].items():
        for leave_label, hits in entry["hit"].items():
            rho = "-" if leave_label == NO_NOTICE_LABEL else _rho_text(entry["rho"][leave_label])
            hit_text = ", ".join(f"{x:.4f}" for x in hits)
            invisible = sum(entry["n_gold_invisible"][leave_label])
            rows.append(
                f"| {name} | {join_label} | {leave_label} | {rho} | {hit_text} | {invisible} "
                f"| {entry['contacted'][leave_label]:.2f} | {entry['wasted'][leave_label]:.2f} |"
            )
    return rows


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    gates = metrics["gates"]
    g1, g2, g3, g4, g5 = (gates[k] for k in ("g1", "g2", "g3", "g4", "g5"))
    hl1, e2, recovery = metrics["hl1"], metrics["e2"], metrics["recovery"]
    methods: list[str] = metrics["methods"]
    a = hl1["availability"]
    join_text = ", ".join(delay_label(d) for d in metrics["join_delays"])
    leave_text = ", ".join(delay_label(d) for d in metrics["leave_delays"])

    def ok(passed: bool) -> str:
        return "通過" if passed else "**不通過**"

    def g1_line(label: str, g: dict[str, Any]) -> str:
        return (
            f"- {label}（L_join = {g['join_delay']}・L_leave = inf の全ての実行が Iteration 16 の L = {g['join_delay']} と "
            f"{g['tolerance']:.6f} 以内．欠け無し）：{ok(g['passed'])}（照合 {g['matched']} 件，こちらの欠け {len(g['missing_here'])} 件，"
            f"基準の欠け {len(g['missing_in_reference'])} 件，None の不一致 {len(g['none_mismatches'])} 件，"
            f"差の最大 {max(g['max_abs_diff'].values()):.6f}）"
        )

    def by_condition(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda x: (
                methods.index(x["method"]),
                -x["availability"],
                x["refresh"],
                x["join_delay"],
                _delay_order(x["leave_delay"]),
            ),
        )

    def effective(x: dict[str, Any]) -> bool:
        # L_leave ≥ R の行は G3 で L_leave = inf と同じことを確かめるので，表には出さない（metrics.json には全て残す）
        return x["leave_delay"] is None or x["leave_delay"] < x["refresh"]

    lines = [
        "# 離脱の通知の遅れ L_leave ごとの無駄な問い合わせと hit の回復のオフラインの評価（Iteration 17）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個（ノード {metrics['n_nodes']} 台，デバイス {metrics['n_devices']} 台），"
        f"test {metrics['n_test']} 問，m = {m}，中心の数 C = {metrics['n_centroids']}，"
        f"稼働中の平均の長さ {metrics['mean_up_steps']} 問，時刻表の種 {metrics['n_schedule_seeds']} 個",
        f"- 参加の通知の遅れ L_join（問）：{join_text}（{JUDGED_JOIN_DELAY} が判定の条件，{SENSITIVITY_JOIN_DELAY} は記録だけの感度の格子）",
        f"- 離脱の通知の遅れ L_leave（問）：{leave_text}（inf は通知なし．Iteration 16 の同じ L の表）",
        f"- 退行ゲート：{'全て通過' if gates['passed'] else '**不通過あり（判定しない）**'}",
        "",
        "## 退行ゲート",
        "",
        g1_line("G1", g1),
        g1_line("G2", g2),
        f"- G3（L_leave ≥ R の実行が同じ (構成, a, R, 種, L_join) の L_leave = inf と完全に一致）：{ok(g3['passed'])}"
        f"（照合 {g3['compared']} 件 / 期待 {g3['expected_count']} 件，不一致 {len(g3['mismatches'])} 件）",
        f"- G4（hit と (i)〜(iv) の和が問の数に等しい）：{ok(g4['passed'])}（不一致 {len(g4['violations'])} 件）",
        f"- G5（(L_join, L_leave) = ({G5_JOIN_DELAY}, {G5_LEAVE_DELAY}) の実行が同じ (構成, a, 種) の R = 1 と n_hit と全ての件数で完全に一致）："
        f"{ok(g5['passed'])}（照合 {g5['compared']} 件 / 期待 {g5['expected_count']} 件，不一致 {len(g5['mismatches'])} 件）",
        "",
        f"## H-L1（{hl1['method']}，a = {a}，R = {hl1['refresh']}，L_join = {hl1['join_delay']}，L_leave = {hl1['leave_delay']}）",
        "",
        f"| 種 | hit（R = 1） | hit（R = {hl1['refresh']}，L_leave = inf） | hit（R = {hl1['refresh']}，L_leave = {hl1['leave_delay']}） "
        "| 残る低下の分母（問） | 回復（問） | ρ_leave |",
        "|---|---|---|---|---|---|---|",
        *(
            f"| {s} | {f:.4f} | {n:.4f} | {y:.4f} | {nf - nn} | {ny - nn} | {_num(r, 4)} |"
            for s, (f, n, y, nf, nn, ny, r) in enumerate(
                zip(
                    hl1["hit_fresh"],
                    hl1["hit_no_leave_notice"],
                    hl1["hit_notified"],
                    hl1["n_hit_fresh"],
                    hl1["n_hit_no_leave_notice"],
                    hl1["n_hit_notified"],
                    hl1["rho"],
                    strict=True,
                )
            )
        ),
        "",
        f"判定（全ての種で ρ_leave ≥ {hl1['min_recovery']}．分母が 0 以下の種は不成立）：{hl1['verdict']}",
        "",
        f"## 全方式の L_join・L_leave ごとの ρ_leave（記録．a = {a}，R = {MAX_REFRESH}．L_join = {SENSITIVITY_JOIN_DELAY} の行は感度の格子）",
        "",
        "ρ_leave の基準は同じ L_join の L_leave = inf．(ii) は「稼働中だが見えない」の件数の種の和．",
        "",
        "| 方式 | L_join | L_leave | ρ_leave（種ごと） | hit（種ごと） | (ii)（種の和） | contacted（種の平均） | wasted（種の平均） |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in methods:
        lines += _recovery_rows(name, recovery[name])
    lines += [
        "",
        f"## {HL1_METHOD} の ρ_leave（記録．a = {', '.join(metrics['recovery_other_availabilities'])}，R = {MAX_REFRESH}）",
        "",
        "| a | L_join | L_leave | ρ_leave（種ごと） | hit（種ごと） | (ii)（種の和） | contacted（種の平均） | wasted（種の平均） |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a_label, rec in metrics["recovery_other_availabilities"].items():
        lines += [
            f"| {a_label} " + row[row.index("|", 1) :]
            for row in _recovery_rows("", rec[HL1_METHOD])
        ]
    lines += [
        "",
        f"## E-2 相当（記録．{e2['method']}，a = {e2['availability']}，R = 1 → {e2['refresh']}，"
        f"L_join = {e2['join_delay']}，L_leave = {e2['leave_delay']}）",
        "",
        f"| 種 | hit（R = 1） | hit（L_leave = {e2['leave_delay']}） | hit（L_leave = inf） | 残る低下（問） | 回復（問） | "
        + " | ".join(f"{f} の増分" for f in E2_COUNT_FIELDS)
        + " |",
        "|" + "---|" * (6 + len(E2_COUNT_FIELDS)),
        *(
            f"| {p['seed']} | {p['hit_fresh']:.4f} | {p['hit_notified']:.4f} | {p['hit_no_leave_notice']:.4f} "
            f"| {p['remaining_drop']} | {p['recovered']} | "
            + " | ".join(_count(p["count_gain"][f]) for f in E2_COUNT_FIELDS)
            + " |"
            for p in e2["per_seed"]
        ),
        "",
        f"## 問ごとの遷移（記録．a = {metrics['transitions']['availability']}，L_join = {metrics['transitions']['join_delay']}，"
        f"R = 1 の区分 → R = {MAX_REFRESH} の区分，種の和）",
        "",
        "区分：hit，(i) 正解のノードが離脱，(ii) 稼働中だが見えない，(iii) 見えて稼働中だが選ばれない，(iv) 枠を取られた．",
        "",
        "| 方式 | L_leave | R = 1 の区分 | → hit | → (i) | → (ii) | → (iii) | → (iv) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    pooled = _pooled_transitions(metrics)
    for name in methods:
        for d in metrics["leave_delays"]:
            table = pooled.get((name, d))
            if table is None:
                continue
            for cls, row in zip(LOSS_CLASSES, table, strict=True):
                cells = " | ".join(str(v) for v in row)
                lines.append(f"| {name} | {delay_label(d)} | {cls} | {cells} |")
    lines += [
        "",
        f"## hit を失った問の分解（a = {a}，種の和．L_leave ≥ R の行は省く）",
        "",
        "| 方式 | R | L_join | L_leave | 問 | hit | (i) 離脱 | (ii) 見えない | (iii) 選ばれない | うち候補外 | うち上位 m 外 | (iv) 枠を取られた |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {x['method']} | {x['refresh']} | {x['join_delay']} | {delay_label(x['leave_delay'])} | {x['n']} | {x['n_hit']} "
            f"| {x['n_gold_offline']} | {x['n_gold_invisible']} | {x['n_not_selected']} "
            f"| {_count(x['n_not_candidate'])} | {_count(x['n_not_top_m'])} | {x['n_slot_taken']} |"
            for x in by_condition(
                [x for x in metrics["losses"] if x["availability"] == a and effective(x)]
            )
        ),
        "",
        "## 全条件（値は種の平均 [最小, 最大]．L_leave ≥ R の行は省く）",
        "",
        f"| 方式 | a | R | L_join | L_leave | hit@{m} | 正解が稼働中のときの hit@{m} | 問い合わせたノード | うち離脱していたノード |",
        "|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {s['method']} | {s['availability']} | {s['refresh']} | {s['join_delay']} | {delay_label(s['leave_delay'])} "
            f"| {_stat(s['hit'])} | {_stat(s['hit_given_gold_online'])} "
            f"| {s['contacted']['mean']:.2f} | {s['wasted']['mean']:.2f} |"
            for s in by_condition([s for s in metrics["summary"] if effective(s)])
        ),
    ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
