"""参加の通知の遅れ L ごとのメンバー表の見え方と hit の回復のオフラインの評価（Iteration 16）．

`atrium churn-join-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（規則は journal の Iteration 16 の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. 採点・時刻表・質問の順番・方式の構成は Iteration 15（atrium.churn_cascade_eval）と同じにする．
2. メンバー表は，R 問ごとの更新（EQ7a の stale_view）に「参加の通知」を足したものとする．直前の更新 u(t) = ⌊t/R⌋·R より後の刻み j
   （u(t) < j ≤ t − L）に参加した（online[j] かつ ¬online[j−1]）ノードを表に足す．離脱は通知せず，更新のときにだけ表から消える．
   通知なし（L = ∞．delay = None）は EQ7a・Iteration 15 と同じ表になる．L ≥ R では t − L < u(t) なので通知は効かない．
3. 通知の遅れ L ∈ {∞, 0, 1, 10, 100} × 稼働率 a × 更新間隔 R × 時刻表の種ごとに，Iteration 15 の 8 構成を同じ規則で選ぶ．
4. 退行ゲート G1〜G4，判定 H-J1，記録（全方式の L ごとの回復の割合 ρ・contacted・wasted，E-2，a = 0.7 の R = 1 → 最大の
   問ごとの遷移表）を出す．ゲートが 1 つでも通らなければ判定しない．
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
    FLOOD_METHOD,
    JUDGED_CASCADES,
    SINGLE_STAGE_METHODS,
    Cascade,
    LossBreakdown,
    gate_g4,
    node_of_device,
    route_cascade_under_churn,
    run_record,
    single_stage_breakdown,
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
    route_under_churn,
    simulate_availability,
    stale_view,
)
from atrium.config import AtriumConfig
from atrium.device_probe_eval import device_index_of_inbox
from atrium.manifest import Placement
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

# 事前登録の通知の遅れ（問の数）．None は通知なし（L = ∞）で，先頭に置いて同じ時刻表の基準を先に求める
JOIN_DELAYS = (0, 1, 10, 100)
DELAYS: tuple[int | None, ...] = (None, *JOIN_DELAYS)
NO_NOTICE_LABEL = "inf"
MIN_REFRESH = min(REFRESH_STEPS)
MAX_REFRESH = max(REFRESH_STEPS)
# H-J1：device_probe（D = 12）の a = 0.7・R = 最大で，L = 100 の回復の割合 ρ が全ての種でこの値以上
HJ1_METHOD = "device_probe_d12"
HJ1_DELAY = 100
HJ1_MIN_RECOVERY = 0.5
# E-2：device_probe（D = 12）で L = 0 にしても残る低下と，その件数の内訳の増分
E2_METHOD = HJ1_METHOD
E2_DELAY = 0
E2_COUNT_FIELDS = (
    "n_gold_offline",
    "n_gold_invisible",
    "n_not_selected",
    "n_not_candidate",
    "n_not_top_m",
    "n_slot_taken",
)
# 問ごとの遷移表（B43 (c)）は a = 0.7 に限る（出力の大きさを抑えるため）
TRANSITION_AVAILABILITY = JUDGED_AVAILABILITY
# 区分の順番は run_record の件数と同じ（hit，(i)〜(iv)）
LOSS_CLASSES = ("hit", "gold_offline", "gold_invisible", "not_selected", "slot_taken")
RUN_KEY_FIELDS = ("method", "availability", "refresh", "seed")
RunKey = tuple[str, float, int, int]


def delay_label(delay: int | None) -> str:
    """通知の遅れの表記（metrics.json の辞書のキーと報告に使う．通知なしは NO_NOTICE_LABEL）．"""
    return NO_NOTICE_LABEL if delay is None else str(delay)


def join_notified_view(online: BoolArray, refresh: int, delay: int | None) -> BoolArray:
    """refresh 問ごとの更新に参加の通知（遅れ delay 問）を足したメンバー表で，各問の時点で見えているノード．

    delay が None なら stale_view と同じ．通知は参加の事実だけを伝えるので，通知の後に離脱したノードも次の更新まで表に残る．
    """
    stale = stale_view(online, refresh)
    if delay is None:
        return stale
    if delay < 0:
        raise ValueError(f"delay must be >= 0 or None, got {delay}")
    # 刻み 0 は直前の状態が無いので参加として数えない（u(t) ≥ 0 なので区間 (u(t), t − L] にも入らない）
    joined = np.zeros_like(online)
    joined[1:] = online[1:] & ~online[:-1]
    cumulative = np.cumsum(joined, axis=0)
    t = np.arange(online.shape[0])
    last = (t // refresh) * refresh
    end = t - delay
    has_window = end > last
    # 区間 (last, end] の参加の数．区間が空の刻みは end を 0 に丸めた値を使うが，has_window で落とす
    joins_in_window = cumulative[np.clip(end, 0, None)] - cumulative[last]
    notified = has_window[:, None] & (joins_in_window > 0)
    out: BoolArray = stale | notified
    return out


def route_methods(
    first: F32Array,
    second: F32Array,
    gold: Sequence[int],
    node_of: I64Array,
    device_of_inbox: I64Array,
    device_node: I64Array,
    view: BoolArray,
    online: BoolArray,
    m: int,
    cascades: Sequence[Cascade],
) -> list[tuple[str, ChurnOutcome, LossBreakdown]]:
    """1 つのメンバー表で全ての構成を選ぶ（Iteration 15 の evaluate の内側の処理と同じ順番・同じ関数）．"""
    out: list[tuple[str, ChurnOutcome, LossBreakdown]] = []
    for name, scores in (("multi_centroid", first), (FLOOD_METHOD, second)):
        flood = name == FLOOD_METHOD
        outcome = route_under_churn(scores, gold, node_of, view, online, m, flood)
        loss = single_stage_breakdown(scores, gold, node_of, view, online, m, flood, outcome.hit)
        out.append((name, outcome, loss))
    for c in cascades:
        outcome, loss = route_cascade_under_churn(
            first, second, gold, node_of, device_of_inbox, device_node, view, online, m, c
        )
        out.append((c.name, outcome, loss))
    return out


def loss_class(out: ChurnOutcome, loss: LossBreakdown) -> I64Array:
    """問ごとの区分の番号（LOSS_CLASSES の順）．区分は互いに排他で全ての問を覆うはずなので，そうでなければ止める．"""
    masks = np.stack(
        [out.hit, loss.gold_offline, loss.gold_invisible, loss.not_selected, loss.slot_taken]
    )
    per_question = masks.sum(axis=0)
    if not (per_question == 1).all():
        bad = int((per_question != 1).sum())
        raise ValueError(f"{bad} questions are not in exactly one loss class")
    out_class: I64Array = masks.argmax(axis=0).astype(np.int64)
    return out_class


def transition_table(before: I64Array, after: I64Array) -> list[list[int]]:
    """問ごとの区分の遷移の件数（行は before，列は after の区分．LOSS_CLASSES の順の 5 × 5）．"""
    n_classes = len(LOSS_CLASSES)
    table = np.zeros((n_classes, n_classes), np.int64)
    np.add.at(table, (before, after), 1)
    out: list[list[int]] = table.tolist()
    return out


def delay_run_record(
    method: str,
    availability: float,
    refresh: int,
    delay: int | None,
    seed: int,
    out: ChurnOutcome,
    loss: LossBreakdown,
) -> dict[str, Any]:
    """1 実行の記録（Iteration 15 の run_record に通知の遅れ delay を足す．通知なしは None）．"""
    return {**run_record(method, availability, refresh, seed, out, loss), "delay": delay}


def _run_key(r: dict[str, Any]) -> RunKey:
    """（方式，稼働率，更新間隔，種）．"""
    return (r["method"], r["availability"], r["refresh"], r["seed"])


def _run_id(r: dict[str, Any]) -> dict[str, Any]:
    """ゲートの不一致の記録に載せる実行の識別子．"""
    return {k: r[k] for k in (*RUN_KEY_FIELDS, "delay")}


def summarize_by_delay(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """（方式，稼働率，更新間隔，通知の遅れ）ごとに，時刻表の種をまたいだ平均・最小・最大（churn_eval.summarize_runs に遅れを足したもの）．"""
    groups: dict[tuple[str, float, int, int | None], list[dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault((*_run_key(r)[:3], r["delay"]), []).append(r)
    summary = []
    for (method, availability, refresh, delay), rs in groups.items():
        entry: dict[str, Any] = {
            "method": method,
            "availability": availability,
            "refresh": refresh,
            "delay": delay,
        }
        for f in STAT_FIELDS:
            values = [float(x[f]) for x in rs]
            entry[f] = {"mean": float(np.mean(values)), "min": min(values), "max": max(values)}
        summary.append(entry)
    return summary


def summarize_losses_by_delay(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """（方式，稼働率，更新間隔，通知の遅れ）ごとの件数の種の和（二段の方式以外の内訳は None）．"""
    groups: dict[tuple[str, float, int, int | None], list[dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault((*_run_key(r)[:3], r["delay"]), []).append(r)
    out = []
    for (method, availability, refresh, delay), rs in groups.items():
        entry: dict[str, Any] = {
            "method": method,
            "availability": availability,
            "refresh": refresh,
            "delay": delay,
        }
        for f in COUNT_FIELDS:
            values = [x[f] for x in rs]
            entry[f] = None if any(v is None for v in values) else int(sum(values))
        out.append(entry)
    return out


# ── 退行ゲート ──────────────────────────────────────────────────────────────


def gate_g1(
    runs: Sequence[dict[str, Any]], reference: dict[str, Any], m: int, n_test: int
) -> dict[str, Any]:
    """G1：L = ∞ の全ての実行が，Iteration 15 の runs と 1/n_test 以内で一致し，どちらにも欠けが無いか．

    照合する項目は STAT_FIELDS の全てと COUNT_FIELDS の全て（件数は n_test で割った差）．None かどうかも一致させる．
    """
    tolerance = 1.0 / n_test
    ref = {_run_key(r): r for r in reference["runs"]}
    ours = {_run_key(r): r for r in runs if r["delay"] is None}
    missing_here = [list(k) for k in ref if k not in ours]
    missing_in_reference = [list(k) for k in ours if k not in ref]
    max_abs_diff = {f: 0.0 for f in (*STAT_FIELDS, *COUNT_FIELDS)}
    none_mismatches = []
    matched = 0
    for key, r in ours.items():
        if key not in ref:
            continue
        matched += 1
        expected = ref[key]
        for f in STAT_FIELDS:
            max_abs_diff[f] = max(max_abs_diff[f], abs(float(r[f]) - float(expected[f])))
        for f in COUNT_FIELDS:
            got, want = r[f], expected[f]
            if (got is None) != (want is None):
                none_mismatches.append({**_run_id(r), "field": f})
            elif got is not None:
                max_abs_diff[f] = max(max_abs_diff[f], abs(got - want) / n_test)
    return {
        "tolerance": tolerance,
        "matched": matched,
        "missing_here": missing_here,
        "missing_in_reference": missing_in_reference,
        "none_mismatches": none_mismatches,
        "max_abs_diff": max_abs_diff,
        "passed": reference["m"] == m
        and reference["n_test"] == n_test
        and matched > 0
        and not missing_here
        and not missing_in_reference
        and not none_mismatches
        and all(v <= tolerance + 1e-12 for v in max_abs_diff.values()),
    }


def gate_g2(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """G2：L ≥ R の実行が，同じ（方式，a，R，種）の L = ∞ と全ての項目で完全に一致するか．"""
    base = {_run_key(r): r for r in runs if r["delay"] is None}
    fields = (*STAT_FIELDS, *COUNT_FIELDS)
    mismatches = []
    compared = 0
    for r in runs:
        if r["delay"] is None or r["delay"] < r["refresh"]:
            continue
        compared += 1
        expected = base[_run_key(r)]
        differing = [f for f in fields if r[f] != expected[f]]
        if differing:
            mismatches.append({**_run_id(r), "fields": differing})
    expected_count = sum(1 for r in base.values() for d in JOIN_DELAYS if d >= r["refresh"])
    return {
        "compared": compared,
        "expected_count": expected_count,
        "mismatches": mismatches,
        "passed": compared > 0 and compared == expected_count and not mismatches,
    }


def gate_g3(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """G3：flood_score の L = 0 の hit が，同じ（a，種）の R = 1（L = ∞）の hit と，全ての R で完全に一致するか．

    L = 0 では稼働中の全ノードが表で見えるので，返事のある候補は R = 1 と同じ集合になる（contacted・wasted は一致しない）．
    """
    fresh = {
        (r["availability"], r["seed"]): r["n_hit"]
        for r in runs
        if r["method"] == FLOOD_METHOD and r["refresh"] == MIN_REFRESH and r["delay"] is None
    }
    mismatches = []
    compared = 0
    for r in runs:
        if r["method"] != FLOOD_METHOD or r["delay"] != 0:
            continue
        compared += 1
        expected = fresh[(r["availability"], r["seed"])]
        if r["n_hit"] != expected:
            mismatches.append({**_run_id(r), "n_hit": r["n_hit"], "expected": expected})
    return {
        "compared": compared,
        "mismatches": mismatches,
        "passed": compared > 0 and compared == len(fresh) * len(REFRESH_STEPS) and not mismatches,
    }


# ── 判定と記録 ──────────────────────────────────────────────────────────────


def _verdict(flag: bool, gates_passed: bool, yes: str, no: str) -> str:
    """判定の文言（ゲートを通らなければ判定しない）．"""
    if not gates_passed:
        return "退行ゲートを通らないため判定しない"
    return yes if flag else no


def recovery_by_method(runs: Sequence[dict[str, Any]], methods: Sequence[str]) -> dict[str, Any]:
    """a = 0.7 で，方式・通知の遅れごとの回復の割合 ρ（種ごと）と，R = 最大の hit（種ごと）・contacted・wasted（種の平均）．

    ρ = (hit(R=最大, L) − hit(R=最大, L=∞)) / (hit(R=1) − hit(R=最大, L=∞))．問の数は実行によらず等しいので件数で計算する．
    分母が 0 以下の種は None とする．
    """
    by = {
        (r["method"], r["seed"], r["refresh"], r["delay"]): r
        for r in runs
        if r["availability"] == JUDGED_AVAILABILITY
    }
    seeds = range(N_SCHEDULE_SEEDS)
    out: dict[str, Any] = {}
    for method in methods:
        fresh = [by[(method, s, MIN_REFRESH, None)] for s in seeds]
        at_max = {d: [by[(method, s, MAX_REFRESH, d)] for s in seeds] for d in DELAYS}
        rho: dict[str, list[float | None]] = {}
        for delay in JOIN_DELAYS:
            per_seed: list[float | None] = []
            for f, stale, notified in zip(fresh, at_max[None], at_max[delay], strict=True):
                denom = f["n_hit"] - stale["n_hit"]
                per_seed.append((notified["n_hit"] - stale["n_hit"]) / denom if denom > 0 else None)
            rho[delay_label(delay)] = per_seed
        out[method] = {
            "hit_fresh": [r["hit"] for r in fresh],
            "hit": {delay_label(d): [r["hit"] for r in rs] for d, rs in at_max.items()},
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
    return out


def judge_hj1(recovery: dict[str, Any], gates_passed: bool) -> dict[str, Any]:
    """H-J1：HJ1_METHOD の L = HJ1_DELAY の ρ が全ての種で HJ1_MIN_RECOVERY 以上か（分母が 0 以下の種は不成立）．"""
    entry = recovery[HJ1_METHOD]
    rho: list[float | None] = entry["rho"][delay_label(HJ1_DELAY)]
    supported = all(x is not None and x >= HJ1_MIN_RECOVERY for x in rho)
    return {
        "method": HJ1_METHOD,
        "availability": JUDGED_AVAILABILITY,
        "refresh": MAX_REFRESH,
        "delay": HJ1_DELAY,
        "min_recovery": HJ1_MIN_RECOVERY,
        "hit_fresh": entry["hit_fresh"],
        "hit_no_notice": entry["hit"][NO_NOTICE_LABEL],
        "hit_notified": entry["hit"][delay_label(HJ1_DELAY)],
        "rho": rho,
        "supported": supported,
        "verdict": _verdict(supported, gates_passed, "支持", "支持されない"),
    }


def record_e2(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """E-2（記録）：E2_METHOD の a = 0.7 で，R = 1 → 最大・L = E2_DELAY にしても残る hit の低下と，件数の内訳の増分（種ごと）．"""
    by = {
        (r["seed"], r["refresh"], r["delay"]): r
        for r in runs
        if r["method"] == E2_METHOD and r["availability"] == JUDGED_AVAILABILITY
    }
    per_seed = []
    for s in range(N_SCHEDULE_SEEDS):
        fresh = by[(s, MIN_REFRESH, None)]
        notified = by[(s, MAX_REFRESH, E2_DELAY)]
        stale = by[(s, MAX_REFRESH, None)]
        per_seed.append(
            {
                "seed": s,
                "hit_fresh": fresh["hit"],
                "hit_notified": notified["hit"],
                "hit_no_notice": stale["hit"],
                "remaining_drop": fresh["n_hit"] - notified["n_hit"],
                "recovered": notified["n_hit"] - stale["n_hit"],
                "count_gain": {f: notified[f] - fresh[f] for f in E2_COUNT_FIELDS},
            }
        )
    return {
        "method": E2_METHOD,
        "availability": JUDGED_AVAILABILITY,
        "refresh": MAX_REFRESH,
        "delay": E2_DELAY,
        "per_seed": per_seed,
    }


# ── 評価の本体 ────────────────────────────────────────────────────────────


def evaluate(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    placement: Placement,
    cascade_reference: dict[str, Any],
) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．cascade_reference は Iteration 15 の churn-cascade-eval の metrics.json（G1 の基準）．"""
    from atrium.data_enronqa import kmeans_centroids

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    names = [ib.name for ib in inboxes]
    test = load_query_set(cfg, paths, "test")
    n_test = len(test.qids)
    # 採点・番号の付け方・構成は churn_cascade_eval.evaluate と同じ（G1 で Iteration 15 との一致を確かめる）
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

    # 時刻表ごとに，更新間隔と通知の遅れを変えて経路を選ぶ（乱数の引き方は EQ7a・Iteration 15 と同じ）
    runs: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    for availability in AVAILABILITIES:
        for s in range(N_SCHEDULE_SEEDS):
            rng = np.random.default_rng([seed, s, round(availability * 100)])
            online = simulate_availability(
                len(placement.nodes), n_test, availability, MEAN_UP_STEPS, rng
            )
            # REFRESH_STEPS は昇順，DELAYS は None が先頭なので，R = 1・L = ∞ の区分を R = 最大より先に求められる
            fresh_class: dict[str, I64Array] = {}
            for refresh in REFRESH_STEPS:
                for delay in DELAYS:
                    view = join_notified_view(online, refresh, delay)
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
                            delay_run_record(name, availability, refresh, delay, s, out, loss)
                        )
                        if availability != TRANSITION_AVAILABILITY:
                            continue
                        if refresh == MIN_REFRESH and delay is None:
                            fresh_class[name] = loss_class(out, loss)
                        elif refresh == MAX_REFRESH:
                            table = transition_table(fresh_class[name], loss_class(out, loss))
                            transitions.append(
                                {"method": name, "delay": delay, "seed": s, "table": table}
                            )
        logger.info("availability %.2f done", availability)

    gates = {
        "g1": gate_g1(runs, cascade_reference, m, n_test),
        "g2": gate_g2(runs),
        "g3": gate_g3(runs),
        "g4": gate_g4(runs),
    }
    gates_passed = all(g["passed"] for g in gates.values())
    recovery = recovery_by_method(runs, methods)
    return {
        "n_inboxes": len(names),
        "n_nodes": len(placement.nodes),
        "n_devices": n_devices,
        "n_test": n_test,
        "m": m,
        "n_centroids": e.n_centroids,
        "mean_up_steps": MEAN_UP_STEPS,
        "n_schedule_seeds": N_SCHEDULE_SEEDS,
        "delays": list(DELAYS),
        "methods": methods,
        "cascades": [{"name": c.name, "kind": c.kind, "size": c.size} for c in cascades],
        "loss_classes": list(LOSS_CLASSES),
        "gates": {**gates, "passed": gates_passed},
        "hj1": judge_hj1(recovery, gates_passed),
        "recovery": recovery,
        "e2": record_e2(runs),
        "transitions": {
            "availability": TRANSITION_AVAILABILITY,
            "from_refresh": MIN_REFRESH,
            "to_refresh": MAX_REFRESH,
            "tables": transitions,
        },
        "summary": summarize_by_delay(runs),
        "losses": summarize_losses_by_delay(runs),
        "runs": runs,
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


def _delay_order(delay: int | None) -> int:
    """報告の行の並び（通知なしを先頭にする）．"""
    return -1 if delay is None else delay


def _pooled_transitions(metrics: dict[str, Any]) -> dict[tuple[str, int | None], list[list[int]]]:
    """（方式，通知の遅れ）ごとの遷移表の種の和．"""
    n_classes = len(LOSS_CLASSES)
    pooled: dict[tuple[str, int | None], list[list[int]]] = {}
    for t in metrics["transitions"]["tables"]:
        acc = pooled.setdefault((t["method"], t["delay"]), [[0] * n_classes for _ in LOSS_CLASSES])
        for i, row in enumerate(t["table"]):
            for j, v in enumerate(row):
                acc[i][j] += v
    return pooled


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    gates = metrics["gates"]
    g1, g2, g3, g4 = gates["g1"], gates["g2"], gates["g3"], gates["g4"]
    hj1, e2, recovery = metrics["hj1"], metrics["e2"], metrics["recovery"]
    methods: list[str] = metrics["methods"]
    a = hj1["availability"]
    delays_text = ", ".join(delay_label(d) for d in metrics["delays"])

    def ok(passed: bool) -> str:
        return "通過" if passed else "**不通過**"

    def by_method(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda x: (
                methods.index(x["method"]),
                -x["availability"],
                x["refresh"],
                _delay_order(x["delay"]),
            ),
        )

    def effective(x: dict[str, Any]) -> bool:
        # L ≥ R の行は G2 で L = inf と同じことを確かめるので，表には出さない（metrics.json には全て残す）
        return x["delay"] is None or x["delay"] < x["refresh"]

    lines = [
        "# 参加の通知の遅れ L ごとのメンバー表の見え方と hit の回復のオフラインの評価（Iteration 16）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個（ノード {metrics['n_nodes']} 台，デバイス {metrics['n_devices']} 台），"
        f"test {metrics['n_test']} 問，m = {m}，中心の数 C = {metrics['n_centroids']}，"
        f"稼働中の平均の長さ {metrics['mean_up_steps']} 問，時刻表の種 {metrics['n_schedule_seeds']} 個",
        f"- 通知の遅れ L（問）：{delays_text}（inf は通知なし．EQ7a・Iteration 15 と同じ表）",
        f"- 退行ゲート：{'全て通過' if gates['passed'] else '**不通過あり（判定しない）**'}",
        "",
        "## 退行ゲート",
        "",
        f"- G1（L = inf の全ての実行が Iteration 15 と {g1['tolerance']:.6f} 以内．欠け無し）：{ok(g1['passed'])}"
        f"（照合 {g1['matched']} 件，こちらの欠け {len(g1['missing_here'])} 件，基準の欠け {len(g1['missing_in_reference'])} 件，"
        f"None の不一致 {len(g1['none_mismatches'])} 件，差の最大 {max(g1['max_abs_diff'].values()):.6f}）",
        f"- G2（L ≥ R の実行が L = inf と完全に一致）：{ok(g2['passed'])}"
        f"（照合 {g2['compared']} 件 / 期待 {g2['expected_count']} 件，不一致 {len(g2['mismatches'])} 件）",
        f"- G3（flood_score の L = 0 の hit が同じ (a, 種) の R = 1 と完全に一致）：{ok(g3['passed'])}"
        f"（照合 {g3['compared']} 件，不一致 {len(g3['mismatches'])} 件）",
        f"- G4（hit と (i)〜(iv) の和が問の数に等しい）：{ok(g4['passed'])}（不一致 {len(g4['violations'])} 件）",
        "",
        f"## H-J1（{hj1['method']}，a = {a}，R = {hj1['refresh']}，L = {hj1['delay']}）",
        "",
        f"| 種 | hit（R = 1） | hit（R = {hj1['refresh']}，L = inf） | hit（R = {hj1['refresh']}，L = {hj1['delay']}） | ρ |",
        "|---|---|---|---|---|",
        *(
            f"| {s} | {f:.4f} | {n:.4f} | {y:.4f} | {_num(r, 4)} |"
            for s, (f, n, y, r) in enumerate(
                zip(
                    hj1["hit_fresh"],
                    hj1["hit_no_notice"],
                    hj1["hit_notified"],
                    hj1["rho"],
                    strict=True,
                )
            )
        ),
        "",
        f"判定（全ての種で ρ ≥ {hj1['min_recovery']}．分母が 0 以下の種は不成立）：{hj1['verdict']}",
        "",
        f"## 全方式の L ごとの回復の割合 ρ（記録．a = {a}，R = {MAX_REFRESH}）",
        "",
        "| 方式 | L | ρ（種ごと） | hit（種ごと） | contacted（種の平均） | wasted（種の平均） |",
        "|---|---|---|---|---|---|",
    ]
    for name in methods:
        rec = recovery[name]
        for d in metrics["delays"]:
            label = delay_label(d)
            rho_text = "-" if d is None else ", ".join(_num(x, 4) for x in rec["rho"][label])
            hits = ", ".join(f"{x:.4f}" for x in rec["hit"][label])
            lines.append(
                f"| {name} | {label} | {rho_text} | {hits} | {rec['contacted'][label]:.2f} | {rec['wasted'][label]:.2f} |"
            )
    lines += [
        "",
        f"## E-2（記録．{e2['method']}，a = {e2['availability']}，R = 1 → {e2['refresh']}，L = {e2['delay']}）",
        "",
        "| 種 | hit（R = 1） | hit（L = 0） | hit（L = inf） | 残る低下（問） | 回復（問） | "
        + " | ".join(f"{f} の増分" for f in E2_COUNT_FIELDS)
        + " |",
        "|" + "---|" * (6 + len(E2_COUNT_FIELDS)),
        *(
            f"| {p['seed']} | {p['hit_fresh']:.4f} | {p['hit_notified']:.4f} | {p['hit_no_notice']:.4f} "
            f"| {p['remaining_drop']} | {p['recovered']} | "
            + " | ".join(_count(p["count_gain"][f]) for f in E2_COUNT_FIELDS)
            + " |"
            for p in e2["per_seed"]
        ),
        "",
        f"## 問ごとの遷移（記録．a = {metrics['transitions']['availability']}，R = 1 の区分 → R = {MAX_REFRESH} の区分，種の和）",
        "",
        "区分：hit，(i) 正解のノードが離脱，(ii) 稼働中だが見えない，(iii) 見えて稼働中だが選ばれない，(iv) 枠を取られた．",
        "",
        "| 方式 | L | R = 1 の区分 | → hit | → (i) | → (ii) | → (iii) | → (iv) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    pooled = _pooled_transitions(metrics)
    for name in methods:
        for d in metrics["delays"]:
            table = pooled.get((name, d))
            if table is None:
                continue
            for cls, row in zip(LOSS_CLASSES, table, strict=True):
                cells = " | ".join(str(v) for v in row)
                lines.append(f"| {name} | {delay_label(d)} | {cls} | {cells} |")
    lines += [
        "",
        f"## hit を失った問の分解（a = {a}，種の和．L ≥ R の行は省く）",
        "",
        "| 方式 | R | L | 問 | hit | (i) 離脱 | (ii) 見えない | (iii) 選ばれない | うち候補外 | うち上位 m 外 | (iv) 枠を取られた |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {x['method']} | {x['refresh']} | {delay_label(x['delay'])} | {x['n']} | {x['n_hit']} "
            f"| {x['n_gold_offline']} | {x['n_gold_invisible']} | {x['n_not_selected']} "
            f"| {_count(x['n_not_candidate'])} | {_count(x['n_not_top_m'])} | {x['n_slot_taken']} |"
            for x in by_method(
                [x for x in metrics["losses"] if x["availability"] == a and effective(x)]
            )
        ),
        "",
        "## 全条件（値は種の平均 [最小, 最大]．L ≥ R の行は省く）",
        "",
        f"| 方式 | a | R | L | hit@{m} | 正解が稼働中のときの hit@{m} | 問い合わせたノード | うち離脱していたノード |",
        "|---|---|---|---|---|---|---|---|",
        *(
            f"| {s['method']} | {s['availability']} | {s['refresh']} | {delay_label(s['delay'])} "
            f"| {_stat(s['hit'])} | {_stat(s['hit_given_gold_online'])} "
            f"| {s['contacted']['mean']:.2f} | {s['wasted']['mean']:.2f} |"
            for s in by_method([s for s in metrics["summary"] if effective(s)])
        ),
    ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
