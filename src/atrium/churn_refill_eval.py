"""返事の無い probe の枠を 1 段目の次の順位で補う変種（probe_refill）の hit と問い合わせの費用のオフラインの評価（Iteration 18）．

`atrium churn-refill-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（規則は journal の Iteration 18 の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. 採点・時刻表・質問の順番・方式の構成は Iteration 16（atrium.churn_join_eval）と同じにする．メンバー表は
   Iteration 16 の join_notified_view(online, R, L_join) で，離脱は通知しない．
2. 二段の方式は，見えている単位（centroid_probe は受信箱，device_probe はデバイス）を 1 段目の得点で並べ
   （churn_cascade_eval.visible_top_k・visible_top_devices と同じ並べ方），先頭 S 個（S は K または D）に probe を送る．
   返事の無いこと（単位の載るノードが離脱済み）は送った時点で分かるとする（接続の拒否に当たる．時間切れの待ちは模さない）．
   - none：補わない（Iteration 15・16 と同じ）．
   - once：1 往復目で返事の無かった f 個の枠を，見えている順位の S + 1〜S + f 番目で 1 回だけ補う（最大 2 往復）．
   - until_full：補った分に返事が無ければ同じ規則で補い続け，返事のある単位が S 個に揃うか見えている単位が尽きたら止める．
     返事のある単位は「見えていて稼働中」の単位の上位 S 個と同じになる．
   補う相手は順位だけで決め，前の往復で返事の無かったノードに載る受信箱も飛ばさない（centroid_probe だけで起きる）．
   返事のあった候補の中で 2 段目の上位 m 個を選ぶ規則と，hit・(i)〜(iv) の分け方は変えない．
   contacted は全ての往復の probe の先のノードの和集合，wasted はそのうち離脱していたノードの数とする．
   probe の要求の数は，往復ごとに新たに候補に入った単位の載るノードの数の和とする．
3. 1 段だけの方式と全数の構成には補う相手が無いので，none と同じ値になる（G2）．記録だけの対照として device_probe_d15（none のみ）を足す．
4. 退行ゲート G1〜G6，判定 H-P1，記録（ρ_refill・往復の情報・対照・E-2 相当・他の a・遷移表）を出す．
   ゲートが 1 つでも通らなければ判定しない．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
    probed_nodes,
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
)
from atrium.churn_join_eval import (
    E2_COUNT_FIELDS,
    LOSS_CLASSES,
    delay_label,
    join_notified_view,
    loss_class,
    route_methods,
    transition_table,
)
from atrium.churn_join_eval import gate_g1 as join_gate_g1
from atrium.config import AtriumConfig
from atrium.device_probe_eval import device_index_of_inbox, device_scores
from atrium.manifest import Placement
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

Refill = Literal["none", "once", "until_full"]
NO_REFILL: Refill = "none"
REFILLS: tuple[Refill, ...] = (NO_REFILL, "once", "until_full")
REFILLED: tuple[Refill, ...] = REFILLS[1:]
# 参加の通知の遅れ（Iteration 16 の表）．None（通知なし）を先頭に置き，遷移表の基準（R = 1・L_join = ∞・none）を先に求める
JOIN_DELAY_GRID: tuple[int | None, ...] = (None, 0, 100)
MIN_REFRESH = min(REFRESH_STEPS)
MAX_REFRESH = max(REFRESH_STEPS)
# 離脱の無い稼働率（G3．このときと R = 1 では見えているのに離脱済みの単位が無い）
NO_CHURN_AVAILABILITY = 1.0
# 記録だけの対照：once と同じ程度の問い合わせの数を 1 往復で送る場合（none のみ）
CONTROL = Cascade("device_probe_d15", "device_probe", 15)
# H-P1：device_probe（D = 12）の a = 0.7・R = 最大・L_join = 100 で，once の ρ_refill が全ての種でこの値以上
HP1_METHOD = "device_probe_d12"
HP1_REFILL: Refill = "once"
HP1_JOIN_DELAY = 100
HP1_MIN_RECOVERY = 0.20
# G5：L_join = 0・until_full では返事のある候補が R = 1 と同じになる（主の判定には使わない）
G5_JOIN_DELAY = 0
G5_REFILL: Refill = "until_full"
# ρ_refill・遷移表を記録する二段の 4 構成（事前登録の構成）
RECORDED_METHODS = tuple(c.name for c in JUDGED_CASCADES)
# 判定の a 以外で ρ_refill を記録する稼働率（a = 1 は低下が無いので分母が 0 になる）
EXTRA_RECOVERY_AVAILABILITIES = (0.9, 0.5)
TRANSITION_AVAILABILITY = JUDGED_AVAILABILITY
TRANSITION_JOIN_DELAY = HP1_JOIN_DELAY
# 往復の情報（1 段だけの方式は None）
ROUND_FIELDS = (
    "second_round_rate",
    "rounds_mean",
    "rounds_max",
    "probe_requests",
    "n_probe_requests",
)
# G2・G3 で完全な一致を求める項目
EXACT_FIELDS = (*STAT_FIELDS, *COUNT_FIELDS, "n_contacted", *ROUND_FIELDS)
RUN_KEY_FIELDS = ("method", "availability", "refresh", "seed", "join_delay")
RunKey = tuple[str, float, int, int, int | None]
ConditionKey = tuple[str, float, int, int | None, str]


# ── 補う規則 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RefillResult:
    """1 つの補い方の問ごとの結果（answered は返事のあった候補の受信箱の印，rounds は往復の数，requests は probe の要求の数）．"""

    outcome: ChurnOutcome
    loss: LossBreakdown
    answered: BoolArray
    rounds: I64Array
    requests: I64Array


def ranked_units(
    first: F32Array,
    view: BoolArray,
    node_of: I64Array,
    device_of_inbox: I64Array,
    device_node: I64Array,
    cascade: Cascade,
) -> tuple[I64Array, I64Array, I64Array]:
    """単位を 1 段目の得点の順に並べた (問, 順位) の単位の番号，見えている単位の数（問ごと），単位の載るノードの番号を返す．

    並べ方は visible_top_k・visible_top_devices と同じ（見えない単位を −inf にした stable な並べ替え）なので，
    見えている単位が先頭に来て，その先頭 S 個が none の候補になる．
    """
    view_inbox = view[:, node_of]
    if cascade.kind == "centroid_probe":
        scores: F32Array = np.where(view_inbox, first, -np.inf)
        visible = view_inbox
        unit_node = node_of
    else:
        masked: F32Array = np.where(view_inbox, first, -np.inf).astype(np.float32)
        scores = device_scores(masked, device_of_inbox, "max")
        visible = view[:, device_node]
        unit_node = device_node
    order: I64Array = np.argsort(-scores, axis=1, kind="stable").astype(np.int64)
    n_visible: I64Array = visible.sum(axis=1).astype(np.int64)
    return order, n_visible, unit_node


def round_ends(
    dead_cum: I64Array, size: int, n_visible: I64Array, refill: Refill
) -> list[I64Array]:
    """往復ごとの probe の範囲の境目（並べた順位の位置．先頭は 0，r 往復目は [ends[r−1], ends[r])）．

    dead_cum[q, i] は問 q の先頭 i 個の見えている単位のうち返事の無いものの数．r + 1 往復目は，r 往復目で返事の無かった数だけ
    次の順位を足す．見えている単位の数を超えない．until_full は足す数が全ての問で 0 になるまで続ける．
    """
    rows = np.arange(len(n_visible))
    first_end: I64Array = np.minimum(size, n_visible)
    ends: list[I64Array] = [np.zeros_like(first_end), first_end]
    if refill == NO_REFILL:
        return ends
    # 足す往復では少なくとも 1 個の単位が加わるので，往復の数は単位の数を超えない
    for _ in range(dead_cum.shape[1]):
        prev, cur = ends[-2], ends[-1]
        nxt: I64Array = np.minimum(cur + dead_cum[rows, cur] - dead_cum[rows, prev], n_visible)
        if refill == "once":
            ends.append(nxt)
            return ends
        if (nxt == cur).all():
            return ends
        ends.append(nxt)
    raise RuntimeError("until_full did not converge")


def _units_in_range(order: I64Array, start: I64Array, end: I64Array) -> BoolArray:
    """並べた順位の位置 [start, end) にある単位の印 (問, 単位)．"""
    pos = np.arange(order.shape[1])
    in_range = (pos >= start[:, None]) & (pos < end[:, None])
    mask = np.zeros(order.shape, np.bool_)
    np.put_along_axis(mask, order, in_range, axis=1)
    return mask


def _inboxes_of_units(units: BoolArray, cascade: Cascade, device_of_inbox: I64Array) -> BoolArray:
    """単位の印を受信箱の印に直す（device_probe はデバイスに載る全ての受信箱）．"""
    if cascade.kind == "centroid_probe":
        return units
    out: BoolArray = units[:, device_of_inbox]
    return out


def route_cascade_with_refill(
    first: F32Array,
    second: F32Array,
    gold: Sequence[int],
    node_of: I64Array,
    device_of_inbox: I64Array,
    device_node: I64Array,
    view: BoolArray,
    online: BoolArray,
    m: int,
    cascade: Cascade,
    refills: Sequence[Refill],
) -> dict[Refill, RefillResult]:
    """二段の方式で，補い方ごとに各問の受信箱を選び，hit・問い合わせの数・hit を失った理由・往復の情報を返す．

    候補が決まった後の規則（返事のあった候補の 2 段目の上位 m 個，正解と同点の受信箱を正解より上に数える，(i)〜(iv) の分け方）は
    churn_cascade_eval.route_cascade_under_churn と同じで，none はそれと同じ結果になる．
    """
    n_questions = first.shape[0]
    n_nodes = view.shape[1]
    t = np.arange(n_questions)
    gold_arr = np.asarray(gold)
    view_inbox = view[:, node_of]
    online_inbox = online[:, node_of]
    order, n_visible, unit_node = ranked_units(
        first, view, node_of, device_of_inbox, device_node, cascade
    )
    visible_pos = np.arange(order.shape[1])[None, :] < n_visible[:, None]
    dead_sorted = np.take_along_axis(~online[:, unit_node], order, axis=1) & visible_pos
    dead_cum: I64Array = np.concatenate(
        [np.zeros((n_questions, 1), np.int64), np.cumsum(dead_sorted, axis=1, dtype=np.int64)],
        axis=1,
    )
    gold_score = second[t, gold_arr]
    gold_online = online_inbox[t, gold_arr]
    gold_visible = view_inbox[t, gold_arr]
    reachable = gold_online & gold_visible
    out: dict[Refill, RefillResult] = {}
    for refill in refills:
        ends = round_ends(dead_cum, cascade.size, n_visible, refill)
        rounds = np.zeros(n_questions, np.int64)
        requests = np.zeros(n_questions, np.int64)
        for start, end in zip(ends, ends[1:], strict=False):
            new = _inboxes_of_units(_units_in_range(order, start, end), cascade, device_of_inbox)
            rounds += end > start
            requests += probed_nodes(new, node_of, n_nodes).sum(axis=1)
        candidates = _inboxes_of_units(
            _units_in_range(order, ends[0], ends[-1]), cascade, device_of_inbox
        )
        probed = probed_nodes(candidates, node_of, n_nodes)
        # 返事のあった候補だけから選ぶ（route_cascade_under_churn と同じ）
        answered = candidates & online_inbox
        rank = ((second >= gold_score[:, None]) & answered).sum(axis=1)
        hit = answered[t, gold_arr] & (rank <= m)
        gold_candidate = candidates[t, gold_arr]
        not_candidate = reachable & ~gold_candidate
        not_top_m = reachable & gold_candidate & (rank > m)
        outcome = ChurnOutcome(
            hit=hit,
            gold_online=gold_online,
            contacted=probed.sum(axis=1),
            wasted=(probed & ~online).sum(axis=1),
        )
        loss = LossBreakdown(
            gold_offline=~gold_online,
            gold_invisible=gold_online & ~gold_visible,
            not_selected=not_candidate | not_top_m,
            slot_taken=np.zeros_like(hit),
            not_candidate=not_candidate,
            not_top_m=not_top_m,
        )
        out[refill] = RefillResult(outcome, loss, answered, rounds, requests)
    return out


def refill_run_record(
    method: str,
    availability: float,
    refresh: int,
    join_delay: int | None,
    refill: Refill,
    seed: int,
    out: ChurnOutcome,
    loss: LossBreakdown,
    rounds: I64Array | None,
    requests: I64Array | None,
) -> dict[str, Any]:
    """1 実行の記録（Iteration 15 の run_record に，L_join・補い方・問い合わせの総数・往復の情報を足す．1 段だけの方式の往復の情報は None）．"""
    record = {
        **run_record(method, availability, refresh, seed, out, loss),
        "join_delay": join_delay,
        "refill": refill,
        "n_contacted": int(out.contacted.sum()),
    }
    if rounds is None or requests is None:
        return record | {f: None for f in ROUND_FIELDS}
    return record | {
        "second_round_rate": float((rounds >= 2).mean()),
        "rounds_mean": float(rounds.mean()),
        "rounds_max": int(rounds.max()),
        "probe_requests": float(requests.mean()),
        "n_probe_requests": int(requests.sum()),
    }


def count_g6_violations(results: dict[Refill, RefillResult]) -> int:
    """返事のある候補の集合が none ⊆ once ⊆ until_full にならない問の数（G6）．"""
    none, once, full = (results[r].answered for r in REFILLS)
    bad = ((none & ~once) | (once & ~full)).any(axis=1)
    return int(bad.sum())


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
    control: Cascade,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """時刻表ごとに，更新間隔・L_join・補い方を変えて全ての構成を選び，実行の記録・遷移表・G6 の照合を返す．

    乱数の引き方・問の数・ノードの数・メンバー表は Iteration 16 と同じにする（G1 で照合する）．
    """
    n_steps = len(gold)
    runs: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    g6_checks: list[dict[str, Any]] = []
    for availability in AVAILABILITIES:
        for s in range(N_SCHEDULE_SEEDS):
            rng = np.random.default_rng([seed, s, round(availability * 100)])
            online = simulate_availability(n_nodes, n_steps, availability, MEAN_UP_STEPS, rng)
            # REFRESH_STEPS は昇順，JOIN_DELAY_GRID は None が先頭，REFILLS は none が先頭なので，
            # 遷移表の基準（R = 1・L_join = ∞・none）を R = 最大より先に求められる
            fresh_class: dict[str, I64Array] = {}
            for refresh in REFRESH_STEPS:
                for join_delay in JOIN_DELAY_GRID:
                    view = join_notified_view(online, refresh, join_delay)
                    condition = (availability, refresh, join_delay)
                    single = route_methods(
                        first,
                        second,
                        gold,
                        node_of,
                        device_of_inbox,
                        device_node,
                        view,
                        online,
                        m,
                        (),
                    )
                    for name, out, loss in single:
                        for refill in REFILLS:
                            runs.append(
                                refill_run_record(
                                    name, *condition, refill, s, out, loss, None, None
                                )
                            )
                    for c in (*cascades, control):
                        refills = (NO_REFILL,) if c is control else REFILLS
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
                            c,
                            refills,
                        )
                        for refill, res in results.items():
                            runs.append(
                                refill_run_record(
                                    c.name,
                                    *condition,
                                    refill,
                                    s,
                                    res.outcome,
                                    res.loss,
                                    res.rounds,
                                    res.requests,
                                )
                            )
                        if c is control:
                            continue
                        g6_checks.append(
                            {
                                "method": c.name,
                                "availability": availability,
                                "refresh": refresh,
                                "join_delay": join_delay,
                                "seed": s,
                                "violations": count_g6_violations(results),
                            }
                        )
                        if (
                            c.name not in RECORDED_METHODS
                            or availability != TRANSITION_AVAILABILITY
                        ):
                            continue
                        if refresh == MIN_REFRESH and join_delay is None:
                            base = results[NO_REFILL]
                            fresh_class[c.name] = loss_class(base.outcome, base.loss)
                        elif refresh == MAX_REFRESH and join_delay == TRANSITION_JOIN_DELAY:
                            for refill, res in results.items():
                                table = transition_table(
                                    fresh_class[c.name], loss_class(res.outcome, res.loss)
                                )
                                transitions.append(
                                    {"method": c.name, "refill": refill, "seed": s, "table": table}
                                )
        logger.info("availability %.2f done", availability)
    return runs, transitions, g6_checks


def _group_by_condition(runs: Sequence[dict[str, Any]]) -> dict[ConditionKey, list[dict[str, Any]]]:
    """（方式，稼働率，更新間隔，L_join，補い方）ごとに時刻表の種の実行をまとめる．"""
    groups: dict[ConditionKey, list[dict[str, Any]]] = {}
    for r in runs:
        key = (r["method"], r["availability"], r["refresh"], r["join_delay"], r["refill"])
        groups.setdefault(key, []).append(r)
    return groups


def _condition_entry(key: ConditionKey) -> dict[str, Any]:
    """summary・losses の行の条件の項目．"""
    method, availability, refresh, join_delay, refill = key
    return {
        "method": method,
        "availability": availability,
        "refresh": refresh,
        "join_delay": join_delay,
        "refill": refill,
    }


def summarize_by_refill(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """条件ごとに，時刻表の種をまたいだ平均・最小・最大（往復の情報が None の方式は None）．"""
    summary = []
    for key, rs in _group_by_condition(runs).items():
        entry = _condition_entry(key)
        for f in (*STAT_FIELDS, *ROUND_FIELDS):
            raw = [x[f] for x in rs]
            if any(v is None for v in raw):
                entry[f] = None
                continue
            values = [float(v) for v in raw]
            entry[f] = {"mean": float(np.mean(values)), "min": min(values), "max": max(values)}
        summary.append(entry)
    return summary


def summarize_losses_by_refill(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """条件ごとの件数・問い合わせの総数・probe の要求の総数の種の和（None を含む項目は None）．"""
    out = []
    for key, rs in _group_by_condition(runs).items():
        entry = _condition_entry(key)
        for f in (*COUNT_FIELDS, "n_contacted", "n_probe_requests"):
            values = [x[f] for x in rs]
            entry[f] = None if any(v is None for v in values) else int(sum(values))
        out.append(entry)
    return out


# ── 退行ゲート ──────────────────────────────────────────────────────────────


def _run_key(r: dict[str, Any]) -> RunKey:
    """（方式，稼働率，更新間隔，種，L_join）．"""
    return (r["method"], r["availability"], r["refresh"], r["seed"], r["join_delay"])


def _run_id(r: dict[str, Any]) -> dict[str, Any]:
    """ゲートの不一致の記録に載せる実行の識別子．"""
    return {k: r[k] for k in (*RUN_KEY_FIELDS, "refill")}


def gate_g1(
    runs: Sequence[dict[str, Any]], join_reference: dict[str, Any], m: int, n_test: int
) -> dict[str, Any]:
    """G1：none の L_join ごとの全ての実行（対照を除く）が，Iteration 16 の同じ delay の runs と一致するか．

    照合は L_join ごとに churn_join_eval.gate_g1（1/n_test 以内，欠け無し，件数の None の有無も一致）で行う．
    Iteration 16 の runs は（方式，a，R，種）が delay ごとに重なるので，先に delay で絞らないと基準が 1 つに潰れる．
    """
    per_delay: dict[str, Any] = {}
    for join_delay in JOIN_DELAY_GRID:
        ours = [
            {**r, "delay": None}
            for r in runs
            if r["refill"] == NO_REFILL
            and r["method"] != CONTROL.name
            and r["join_delay"] == join_delay
        ]
        reference = {
            "m": join_reference["m"],
            "n_test": join_reference["n_test"],
            "runs": [r for r in join_reference["runs"] if r["delay"] == join_delay],
        }
        per_delay[delay_label(join_delay)] = join_gate_g1(ours, reference, m, n_test)
    return {
        "by_join_delay": per_delay,
        "matched": sum(g["matched"] for g in per_delay.values()),
        "passed": all(g["passed"] for g in per_delay.values()),
    }


def _gate_same_as_no_refill(
    runs: Sequence[dict[str, Any]], include: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """include を満たす none 以外の実行が，同じ（方式，a，R，種，L_join）の none と EXACT_FIELDS で完全に一致するか．"""
    base = {_run_key(r): r for r in runs if r["refill"] == NO_REFILL and include(r)}
    mismatches = []
    compared = 0
    for r in runs:
        if r["refill"] == NO_REFILL or not include(r):
            continue
        compared += 1
        expected = base[_run_key(r)]
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


def gate_g2(runs: Sequence[dict[str, Any]], no_target_methods: Sequence[str]) -> dict[str, Any]:
    """G2：補う相手の無い構成（1 段だけの方式と全数の構成）の none 以外の実行が，同じ条件の none と完全に一致するか．"""
    return {
        "methods": list(no_target_methods),
        **_gate_same_as_no_refill(runs, lambda r: r["method"] in no_target_methods),
    }


def gate_g3(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """G3：a = 1 または R = 1 の none 以外の実行（対照を除く）が，同じ条件の none と完全に一致するか．

    どちらの条件でも見えているのに離脱済みの単位が無いので，補う枠が生じない．
    """
    return _gate_same_as_no_refill(
        runs,
        lambda r: (
            r["method"] != CONTROL.name
            and (r["availability"] == NO_CHURN_AVAILABILITY or r["refresh"] == MIN_REFRESH)
        ),
    )


def gate_g5(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """G5：L_join = 0・until_full の二段の 4 構成の実行が，同じ（構成，a，種）の R = 1 の実行と全ての件数で完全に一致するか．

    L_join = 0 の表は稼働中の全ノードを含むので，until_full で返事のある候補は R = 1 の候補と同じになる．
    R = 1 の基準は none・L_join = ∞ の実行とする（R = 1 では表が稼働中の集合そのもので，どの条件でも同じ）．
    contacted・wasted は一致しないので照合しない．
    """
    fresh = {
        (r["method"], r["availability"], r["seed"]): r
        for r in runs
        if r["method"] in RECORDED_METHODS
        and r["refresh"] == MIN_REFRESH
        and r["join_delay"] is None
        and r["refill"] == NO_REFILL
    }
    mismatches = []
    compared = 0
    for r in runs:
        if (
            r["method"] not in RECORDED_METHODS
            or r["join_delay"] != G5_JOIN_DELAY
            or r["refill"] != G5_REFILL
        ):
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


def gate_g6(checks: Sequence[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    """G6：二段の構成の全ての実行の全ての問で，返事のある候補が none ⊆ once ⊆ until_full か（違反の問の数が 0）．"""
    violating = [c for c in checks if c["violations"] > 0]
    return {
        "compared": len(checks),
        "expected_count": expected_count,
        "n_violating_questions": sum(c["violations"] for c in checks),
        "violating_runs": violating,
        "passed": len(checks) > 0 and len(checks) == expected_count and not violating,
    }


# ── 判定と記録 ──────────────────────────────────────────────────────────────


def _verdict(flag: bool, gates_passed: bool, yes: str, no: str) -> str:
    """判定の文言（ゲートを通らなければ判定しない）．"""
    if not gates_passed:
        return "退行ゲートを通らないため判定しない"
    return yes if flag else no


def _ratio(numerator: int, denominator: int) -> float | None:
    """分母が 0 以下なら None にした比．"""
    return numerator / denominator if denominator > 0 else None


def recovery_by_method(
    runs: Sequence[dict[str, Any]], methods: Sequence[str], availability: float
) -> dict[str, Any]:
    """稼働率 availability・R = 最大で，方式・L_join ごとの ρ_refill（種ごと）と，補い方ごとの hit・問い合わせ・往復の情報．

    ρ_refill = (n_hit(R=最大, L_join, refill) − n_hit(R=最大, L_join, none)) / (n_hit(R=1) − n_hit(R=最大, L_join, none))．
    n_hit(R=1) は none・L_join = ∞ の R = 1 の実行．分母が 0 以下の種は None とする．
    """
    by = {
        (r["method"], r["seed"], r["refresh"], r["join_delay"], r["refill"]): r
        for r in runs
        if r["availability"] == availability
    }
    seeds = range(N_SCHEDULE_SEEDS)
    out: dict[str, Any] = {}
    for method in methods:
        fresh = [by[(method, s, MIN_REFRESH, None, NO_REFILL)] for s in seeds]
        per_join: dict[str, Any] = {}
        for join_delay in JOIN_DELAY_GRID:
            at_max = {
                rf: [by[(method, s, MAX_REFRESH, join_delay, rf)] for s in seeds] for rf in REFILLS
            }
            base = at_max[NO_REFILL]
            denominators = [f["n_hit"] - b["n_hit"] for f, b in zip(fresh, base, strict=True)]
            rho: dict[str, list[float | None]] = {}
            for rf in REFILLED:
                rho[rf] = [
                    _ratio(r["n_hit"] - b["n_hit"], d)
                    for r, b, d in zip(at_max[rf], base, denominators, strict=True)
                ]
            per_join[delay_label(join_delay)] = {
                "denominator": denominators,
                "rho": rho,
                "hit": {rf: [r["hit"] for r in rs] for rf, rs in at_max.items()},
                "n_hit": {rf: [r["n_hit"] for r in rs] for rf, rs in at_max.items()},
                "contacted": {
                    rf: float(np.mean([r["contacted"] for r in rs])) for rf, rs in at_max.items()
                },
                "wasted": {
                    rf: float(np.mean([r["wasted"] for r in rs])) for rf, rs in at_max.items()
                },
                "probe_requests": {
                    rf: float(np.mean([r["probe_requests"] for r in rs]))
                    for rf, rs in at_max.items()
                },
                "second_round_rate": {
                    rf: float(np.mean([r["second_round_rate"] for r in rs]))
                    for rf, rs in at_max.items()
                },
                "rounds_mean": {
                    rf: float(np.mean([r["rounds_mean"] for r in rs])) for rf, rs in at_max.items()
                },
                "rounds_max": {rf: max(r["rounds_max"] for r in rs) for rf, rs in at_max.items()},
            }
        out[method] = {
            "hit_fresh": [r["hit"] for r in fresh],
            "n_hit_fresh": [r["n_hit"] for r in fresh],
            "by_join_delay": per_join,
        }
    return out


def judge_hp1(recovery: dict[str, Any], gates_passed: bool) -> dict[str, Any]:
    """H-P1：HP1_METHOD の L_join = HP1_JOIN_DELAY・HP1_REFILL の ρ_refill が全ての種で HP1_MIN_RECOVERY 以上か（分母が 0 以下の種は不成立）．"""
    entry = recovery[HP1_METHOD]
    joined = entry["by_join_delay"][delay_label(HP1_JOIN_DELAY)]
    rho: list[float | None] = joined["rho"][HP1_REFILL]
    supported = all(x is not None and x >= HP1_MIN_RECOVERY for x in rho)
    return {
        "method": HP1_METHOD,
        "availability": JUDGED_AVAILABILITY,
        "refresh": MAX_REFRESH,
        "join_delay": HP1_JOIN_DELAY,
        "refill": HP1_REFILL,
        "min_recovery": HP1_MIN_RECOVERY,
        "hit_fresh": entry["hit_fresh"],
        "hit_no_refill": joined["hit"][NO_REFILL],
        "hit_refill": joined["hit"][HP1_REFILL],
        "n_hit_fresh": entry["n_hit_fresh"],
        "n_hit_no_refill": joined["n_hit"][NO_REFILL],
        "n_hit_refill": joined["n_hit"][HP1_REFILL],
        "denominator": joined["denominator"],
        "rho": rho,
        "supported": supported,
        "verdict": _verdict(supported, gates_passed, "支持", "支持されない"),
    }


def _main_condition_runs(
    runs: Sequence[dict[str, Any]],
) -> dict[tuple[str, int, str], dict[str, Any]]:
    """主の条件（a = 0.7・R = 最大・L_join = HP1_JOIN_DELAY）の実行を（方式，種，補い方）で引く表．"""
    return {
        (r["method"], r["seed"], r["refill"]): r
        for r in runs
        if r["availability"] == JUDGED_AVAILABILITY
        and r["refresh"] == MAX_REFRESH
        and r["join_delay"] == HP1_JOIN_DELAY
    }


def record_control(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """対照（記録）：主の条件で，HP1_METHOD の once と CONTROL（none）の hit の差・問い合わせと，回復した問の数 / 増えた問い合わせの数（種ごと）．

    増えた問い合わせの数は，HP1_METHOD の none に対する contacted の問の和の増分．分母が 0 以下なら None．
    """
    by = _main_condition_runs(runs)
    per_seed = []
    for s in range(N_SCHEDULE_SEEDS):
        base = by[(HP1_METHOD, s, NO_REFILL)]
        refilled = by[(HP1_METHOD, s, HP1_REFILL)]
        control = by[(CONTROL.name, s, NO_REFILL)]
        per_seed.append(
            {
                "seed": s,
                "hit_no_refill": base["hit"],
                "hit_refill": refilled["hit"],
                "hit_control": control["hit"],
                "hit_diff_refill_minus_control": refilled["hit"] - control["hit"],
                "n_hit_diff_refill_minus_control": refilled["n_hit"] - control["n_hit"],
                "contacted_no_refill": base["contacted"],
                "contacted_refill": refilled["contacted"],
                "contacted_control": control["contacted"],
                "wasted_refill": refilled["wasted"],
                "wasted_control": control["wasted"],
                "probe_requests_refill": refilled["probe_requests"],
                "probe_requests_control": control["probe_requests"],
                "recovered_per_added_contact_refill": _ratio(
                    refilled["n_hit"] - base["n_hit"], refilled["n_contacted"] - base["n_contacted"]
                ),
                "recovered_per_added_contact_control": _ratio(
                    control["n_hit"] - base["n_hit"], control["n_contacted"] - base["n_contacted"]
                ),
            }
        )
    return {
        "method": HP1_METHOD,
        "refill": HP1_REFILL,
        "control": CONTROL.name,
        "availability": JUDGED_AVAILABILITY,
        "refresh": MAX_REFRESH,
        "join_delay": HP1_JOIN_DELAY,
        "per_seed": per_seed,
    }


def record_e2(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """E-2 相当（記録）：主の条件の HP1_METHOD・once で残る低下と，R = 1 および同じ条件の none に対する件数の増分（種ごと）．"""
    by = _main_condition_runs(runs)
    fresh = {
        r["seed"]: r
        for r in runs
        if r["method"] == HP1_METHOD
        and r["availability"] == JUDGED_AVAILABILITY
        and r["refresh"] == MIN_REFRESH
        and r["join_delay"] is None
        and r["refill"] == NO_REFILL
    }
    per_seed = []
    for s in range(N_SCHEDULE_SEEDS):
        refilled = by[(HP1_METHOD, s, HP1_REFILL)]
        base = by[(HP1_METHOD, s, NO_REFILL)]
        per_seed.append(
            {
                "seed": s,
                "hit_fresh": fresh[s]["hit"],
                "hit_refill": refilled["hit"],
                "hit_no_refill": base["hit"],
                "remaining_drop": fresh[s]["n_hit"] - refilled["n_hit"],
                "recovered": refilled["n_hit"] - base["n_hit"],
                "count_gain": {f: refilled[f] - fresh[s][f] for f in E2_COUNT_FIELDS},
                "count_gain_vs_no_refill": {f: refilled[f] - base[f] for f in E2_COUNT_FIELDS},
            }
        )
    return {
        "method": HP1_METHOD,
        "refill": HP1_REFILL,
        "availability": JUDGED_AVAILABILITY,
        "refresh": MAX_REFRESH,
        "join_delay": HP1_JOIN_DELAY,
        "per_seed": per_seed,
    }


def aggregate_runs(
    runs: Sequence[dict[str, Any]],
    transitions: Sequence[dict[str, Any]],
    g6_checks: Sequence[dict[str, Any]],
    join_reference: dict[str, Any],
    no_target_methods: Sequence[str],
    g6_expected_count: int,
    m: int,
    n_test: int,
) -> dict[str, Any]:
    """退行ゲート・判定・記録をまとめる．join_reference は Iteration 16 の churn-join-eval の metrics.json（G1 の基準）．"""
    gates = {
        "g1": gate_g1(runs, join_reference, m, n_test),
        "g2": gate_g2(runs, no_target_methods),
        "g3": gate_g3(runs),
        "g4": gate_g4(runs),
        "g5": gate_g5(runs),
        "g6": gate_g6(g6_checks, g6_expected_count),
    }
    gates_passed = all(g["passed"] for g in gates.values())
    recovery = recovery_by_method(runs, RECORDED_METHODS, JUDGED_AVAILABILITY)
    return {
        "gates": {**gates, "passed": gates_passed},
        "hp1": judge_hp1(recovery, gates_passed),
        "recovery": recovery,
        "recovery_other_availabilities": {
            str(a): recovery_by_method(runs, [HP1_METHOD], a) for a in EXTRA_RECOVERY_AVAILABILITIES
        },
        "control": record_control(runs),
        "e2": record_e2(runs),
        "transitions": {
            "availability": TRANSITION_AVAILABILITY,
            "join_delay": TRANSITION_JOIN_DELAY,
            "from_refresh": MIN_REFRESH,
            "from_join_delay": None,
            "from_refill": NO_REFILL,
            "to_refresh": MAX_REFRESH,
            "tables": list(transitions),
        },
        "summary": summarize_by_refill(runs),
        "losses": summarize_losses_by_refill(runs),
        "runs": list(runs),
    }


def evaluate(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    placement: Placement,
    join_reference: dict[str, Any],
) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．join_reference は Iteration 16 の churn-join-eval の metrics.json（G1 の基準）．"""
    from atrium.data_enronqa import kmeans_centroids

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    names = [ib.name for ib in inboxes]
    test = load_query_set(cfg, paths, "test")
    n_test = len(test.qids)
    # 採点・番号の付け方・構成は churn_join_eval.evaluate と同じ（G1 で Iteration 16 との一致を確かめる）
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
    methods = [*SINGLE_STAGE_METHODS, *(c.name for c in cascades), CONTROL.name]
    no_target_methods = [*SINGLE_STAGE_METHODS, *(c.name for c in full)]
    g6_expected_count = (
        len(cascades)
        * len(AVAILABILITIES)
        * len(REFRESH_STEPS)
        * len(JOIN_DELAY_GRID)
        * N_SCHEDULE_SEEDS
    )
    runs, transitions, g6_checks = simulate_runs(
        first,
        second,
        gold,
        node_of,
        device_of_inbox,
        device_node,
        len(placement.nodes),
        m,
        cascades,
        CONTROL,
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
        "refills": list(REFILLS),
        "methods": methods,
        "cascades": [{"name": c.name, "kind": c.kind, "size": c.size} for c in cascades],
        # aggregate_runs の "control"（対照の記録）と分けるため，構成はキーを変える
        "control_cascade": {"name": CONTROL.name, "kind": CONTROL.kind, "size": CONTROL.size},
        "loss_classes": list(LOSS_CLASSES),
        **aggregate_runs(
            runs,
            transitions,
            g6_checks,
            join_reference,
            no_target_methods,
            g6_expected_count,
            m,
            n_test,
        ),
        "duration_s": time.perf_counter() - start,
    }


# ── 報告 ──────────────────────────────────────────────────────────────────


def _num(value: float | None, digits: int) -> str:
    """None を「-」にした数値の表記．"""
    return "-" if value is None else f"{value:.{digits}f}"


def _count(value: int | None) -> str:
    """None を「-」にした件数の表記．"""
    return "-" if value is None else str(value)


def _stat(s: dict[str, float] | None, digits: int = 4) -> str:
    """種をまたいだ平均 [最小, 最大] の表記（None は「-」）．"""
    if s is None:
        return "-"
    return f"{s['mean']:.{digits}f} [{s['min']:.{digits}f}, {s['max']:.{digits}f}]"


def _rho_text(values: list[float | None]) -> str:
    """種ごとの ρ_refill の表記（全ての種で分母が 0 以下なら「定義できない」）．"""
    if all(v is None for v in values):
        return "定義できない（分母が 0 以下）"
    return ", ".join(_num(v, 4) for v in values)


def _delay_order(delay: int | None) -> int:
    """報告の行の並び（通知なしを先頭にする）．"""
    return -1 if delay is None else delay


def _pooled_transitions(metrics: dict[str, Any]) -> dict[tuple[str, str], list[list[int]]]:
    """（方式，補い方）ごとの遷移表の種の和．"""
    n_classes = len(LOSS_CLASSES)
    pooled: dict[tuple[str, str], list[list[int]]] = {}
    for t in metrics["transitions"]["tables"]:
        acc = pooled.setdefault((t["method"], t["refill"]), [[0] * n_classes for _ in LOSS_CLASSES])
        for i, row in enumerate(t["table"]):
            for j, v in enumerate(row):
                acc[i][j] += v
    return pooled


def _recovery_rows(name: str, rec: dict[str, Any]) -> list[str]:
    """1 つの方式の L_join・補い方ごとの ρ_refill・hit・問い合わせ・往復の情報の表の行．"""
    rows = []
    for join_label, entry in rec["by_join_delay"].items():
        for refill in REFILLS:
            rho = "-" if refill == NO_REFILL else _rho_text(entry["rho"][refill])
            hit_text = ", ".join(f"{x:.4f}" for x in entry["hit"][refill])
            rows.append(
                f"| {name} | {join_label} | {refill} | {rho} | {hit_text} "
                f"| {entry['contacted'][refill]:.2f} | {entry['wasted'][refill]:.2f} "
                f"| {entry['probe_requests'][refill]:.2f} | {entry['second_round_rate'][refill]:.4f} "
                f"| {entry['rounds_mean'][refill]:.3f} | {entry['rounds_max'][refill]} |"
            )
    return rows


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    gates = metrics["gates"]
    g1, g2, g3, g4, g5, g6 = (gates[k] for k in ("g1", "g2", "g3", "g4", "g5", "g6"))
    hp1, e2, control, recovery = (
        metrics["hp1"],
        metrics["e2"],
        metrics["control"],
        metrics["recovery"],
    )
    methods: list[str] = metrics["methods"]
    no_target = set(g2["methods"])
    a = hp1["availability"]
    join_text = ", ".join(delay_label(d) for d in metrics["join_delays"])

    def ok(passed: bool) -> str:
        return "通過" if passed else "**不通過**"

    def g1_part(label: str, g: dict[str, Any]) -> str:
        return (
            f"L_join = {label}：照合 {g['matched']} 件，こちらの欠け {len(g['missing_here'])} 件，"
            f"基準の欠け {len(g['missing_in_reference'])} 件，None の不一致 {len(g['none_mismatches'])} 件，"
            f"差の最大 {max(g['max_abs_diff'].values()):.6f}"
        )

    def by_condition(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda x: (
                methods.index(x["method"]),
                -x["availability"],
                x["refresh"],
                _delay_order(x["join_delay"]),
                REFILLS.index(x["refill"]),
            ),
        )

    def effective(x: dict[str, Any]) -> bool:
        # G2・G3 で none と同じことを確かめる行は表に出さない（metrics.json には全て残す）
        if x["refill"] == NO_REFILL:
            return True
        return (
            x["method"] not in no_target
            and x["availability"] != NO_CHURN_AVAILABILITY
            and x["refresh"] != MIN_REFRESH
        )

    tolerance = next(iter(g1["by_join_delay"].values()))["tolerance"]
    lines = [
        "# 返事の無い probe の枠を次の順位で補ったときの hit と問い合わせの費用のオフラインの評価（Iteration 18）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個（ノード {metrics['n_nodes']} 台，デバイス {metrics['n_devices']} 台），"
        f"test {metrics['n_test']} 問，m = {m}，中心の数 C = {metrics['n_centroids']}，"
        f"稼働中の平均の長さ {metrics['mean_up_steps']} 問，時刻表の種 {metrics['n_schedule_seeds']} 個",
        f"- 参加の通知の遅れ L_join（問）：{join_text}（inf は通知なし．離脱は通知しない．Iteration 16 の表）",
        f"- 補い方：{', '.join(metrics['refills'])}．対照 {metrics['control_cascade']['name']} は none のみ",
        f"- 実行 {len(metrics['runs'])} 件",
        f"- 退行ゲート：{'全て通過' if gates['passed'] else '**不通過あり（判定しない）**'}",
        "",
        "## 退行ゲート",
        "",
        f"- G1（none の全ての実行が Iteration 16 の同じ L と {tolerance:.6f} 以内．欠け無し）：{ok(g1['passed'])}"
        f"（照合 {g1['matched']} 件．"
        + "．".join(g1_part(label, g) for label, g in g1["by_join_delay"].items())
        + "）",
        f"- G2（補う相手の無い構成の none 以外が none と完全に一致）：{ok(g2['passed'])}"
        f"（照合 {g2['compared']} 件 / 期待 {g2['expected_count']} 件，不一致 {len(g2['mismatches'])} 件）",
        f"- G3（a = 1.0 または R = 1 の none 以外が none と完全に一致）：{ok(g3['passed'])}"
        f"（照合 {g3['compared']} 件 / 期待 {g3['expected_count']} 件，不一致 {len(g3['mismatches'])} 件）",
        f"- G4（hit と (i)〜(iv) の和が問の数に等しい）：{ok(g4['passed'])}（不一致 {len(g4['violations'])} 件）",
        f"- G5（L_join = {G5_JOIN_DELAY}・{G5_REFILL} の二段の 4 構成が同じ (構成, a, 種) の R = 1 と全ての件数で完全に一致）："
        f"{ok(g5['passed'])}（照合 {g5['compared']} 件 / 期待 {g5['expected_count']} 件，不一致 {len(g5['mismatches'])} 件）",
        f"- G6（二段の構成の全ての問で返事のある候補が none ⊆ once ⊆ until_full）：{ok(g6['passed'])}"
        f"（照合 {g6['compared']} 実行 / 期待 {g6['expected_count']} 実行，違反 {g6['n_violating_questions']} 問）",
        "",
        f"## H-P1（{hp1['method']}，a = {a}，R = {hp1['refresh']}，L_join = {hp1['join_delay']}，{hp1['refill']}）",
        "",
        f"| 種 | n_hit（R = 1） | n_hit（none） | n_hit（{hp1['refill']}） | 分母（問） | 回復（問） | ρ_refill |",
        "|---|---|---|---|---|---|---|",
        *(
            f"| {s} | {nf} | {nn} | {ny} | {d} | {ny - nn} | {_num(r, 4)} |"
            for s, (nf, nn, ny, d, r) in enumerate(
                zip(
                    hp1["n_hit_fresh"],
                    hp1["n_hit_no_refill"],
                    hp1["n_hit_refill"],
                    hp1["denominator"],
                    hp1["rho"],
                    strict=True,
                )
            )
        ),
        "",
        f"判定（全ての種で ρ_refill ≥ {hp1['min_recovery']}．分母が 0 以下の種は不成立）：{hp1['verdict']}",
        "",
        f"## 二段の 4 構成の ρ_refill・問い合わせ・往復（記録．a = {a}，R = {MAX_REFRESH}）",
        "",
        "ρ_refill の基準は同じ L_join の none．contacted・wasted・probe の要求の数・往復の数の平均は種の平均，往復の数の最大は種の最大．",
        "",
        "| 方式 | L_join | 補い方 | ρ_refill（種ごと） | hit（種ごと） | contacted | wasted | probe の要求 "
        "| 2 往復目の要る問の割合 | 往復の数の平均 | 往復の数の最大 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in RECORDED_METHODS:
        lines += _recovery_rows(name, recovery[name])
    lines += [
        "",
        f"## {HP1_METHOD} の ρ_refill（記録．a = {', '.join(metrics['recovery_other_availabilities'])}，R = {MAX_REFRESH}）",
        "",
        "| a | L_join | 補い方 | ρ_refill（種ごと） | hit（種ごと） | contacted | wasted | probe の要求 "
        "| 2 往復目の要る問の割合 | 往復の数の平均 | 往復の数の最大 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a_label, rec in metrics["recovery_other_availabilities"].items():
        lines += [
            f"| {a_label} " + row[row.index("|", 1) :]
            for row in _recovery_rows("", rec[HP1_METHOD])
        ]
    lines += [
        "",
        f"## 対照（記録．{control['method']} の {control['refill']} と {control['control']} の none．a = {control['availability']}，"
        f"R = {control['refresh']}，L_join = {control['join_delay']}）",
        "",
        "回復 / 増分は，none に対する回復した問の数 / 増えた問い合わせの数（問の和）．",
        "",
        "| 種 | hit（none） | hit（refill） | hit（対照） | 差（refill − 対照） | contacted（none / refill / 対照） "
        "| wasted（refill / 対照） | probe の要求（refill / 対照） | 回復 / 増分（refill） | 回復 / 増分（対照） |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {p['seed']} | {p['hit_no_refill']:.4f} | {p['hit_refill']:.4f} | {p['hit_control']:.4f} "
            f"| {p['hit_diff_refill_minus_control']:+.4f}（{p['n_hit_diff_refill_minus_control']:+d} 問） "
            f"| {p['contacted_no_refill']:.2f} / {p['contacted_refill']:.2f} / {p['contacted_control']:.2f} "
            f"| {p['wasted_refill']:.2f} / {p['wasted_control']:.2f} "
            f"| {p['probe_requests_refill']:.2f} / {p['probe_requests_control']:.2f} "
            f"| {_num(p['recovered_per_added_contact_refill'], 4)} | {_num(p['recovered_per_added_contact_control'], 4)} |"
            for p in control["per_seed"]
        ),
        "",
        f"## E-2 相当（記録．{e2['method']}，{e2['refill']}，a = {e2['availability']}，R = 1 → {e2['refresh']}，L_join = {e2['join_delay']}）",
        "",
        "増分は R = 1（none・L_join = inf）に対する値で，括弧の中は同じ条件の none に対する値．",
        "",
        "| 種 | hit（R = 1） | hit（refill） | hit（none） | 残る低下（問） | 回復（問） | "
        + " | ".join(f"{f} の増分" for f in E2_COUNT_FIELDS)
        + " |",
        "|" + "---|" * (6 + len(E2_COUNT_FIELDS)),
        *(
            f"| {p['seed']} | {p['hit_fresh']:.4f} | {p['hit_refill']:.4f} | {p['hit_no_refill']:.4f} "
            f"| {p['remaining_drop']} | {p['recovered']} | "
            + " | ".join(
                f"{_count(p['count_gain'][f])}（{_count(p['count_gain_vs_no_refill'][f])}）"
                for f in E2_COUNT_FIELDS
            )
            + " |"
            for p in e2["per_seed"]
        ),
        "",
        f"## 問ごとの遷移（記録．a = {metrics['transitions']['availability']}，R = 1（none・L_join = inf）の区分 → "
        f"R = {MAX_REFRESH}・L_join = {metrics['transitions']['join_delay']} の区分，種の和）",
        "",
        "区分：hit，(i) 正解のノードが離脱，(ii) 稼働中だが見えない，(iii) 見えて稼働中だが選ばれない，(iv) 枠を取られた．",
        "",
        "| 方式 | 補い方 | R = 1 の区分 | → hit | → (i) | → (ii) | → (iii) | → (iv) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    pooled = _pooled_transitions(metrics)
    for name in RECORDED_METHODS:
        for refill in REFILLS:
            table = pooled.get((name, refill))
            if table is None:
                continue
            for cls, row in zip(LOSS_CLASSES, table, strict=True):
                cells = " | ".join(str(v) for v in row)
                lines.append(f"| {name} | {refill} | {cls} | {cells} |")
    lines += [
        "",
        f"## hit を失った問の分解（a = {a}，種の和．G2・G3 で none と同じ行は省く）",
        "",
        "| 方式 | R | L_join | 補い方 | 問 | hit | (i) 離脱 | (ii) 見えない | (iii) 選ばれない | うち候補外 | うち上位 m 外 "
        "| (iv) 枠を取られた | 問い合わせの和 | probe の要求の和 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {x['method']} | {x['refresh']} | {delay_label(x['join_delay'])} | {x['refill']} | {x['n']} | {x['n_hit']} "
            f"| {x['n_gold_offline']} | {x['n_gold_invisible']} | {x['n_not_selected']} "
            f"| {_count(x['n_not_candidate'])} | {_count(x['n_not_top_m'])} | {x['n_slot_taken']} "
            f"| {x['n_contacted']} | {_count(x['n_probe_requests'])} |"
            for x in by_condition(
                [x for x in metrics["losses"] if x["availability"] == a and effective(x)]
            )
        ),
        "",
        "## 全条件（値は種の平均 [最小, 最大]．G2・G3 で none と同じ行は省く）",
        "",
        f"| 方式 | a | R | L_join | 補い方 | hit@{m} | 正解が稼働中のときの hit@{m} | 問い合わせたノード | うち離脱していたノード "
        "| probe の要求 | 2 往復目の要る問の割合 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {s['method']} | {s['availability']} | {s['refresh']} | {delay_label(s['join_delay'])} | {s['refill']} "
            f"| {_stat(s['hit'])} | {_stat(s['hit_given_gold_online'])} "
            f"| {s['contacted']['mean']:.2f} | {s['wasted']['mean']:.2f} "
            f"| {_stat(s['probe_requests'], 2)} | {_stat(s['second_round_rate'])} |"
            for s in by_condition([s for s in metrics["summary"] if effective(s)])
        ),
    ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
