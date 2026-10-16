"""二段の方式（centroid_probe・device_probe）の参加・離脱への頑健さのオフラインの評価（Iteration 15）．

`atrium churn-cascade-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（規則は journal の Iteration 15 の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. 1 段目（multi_centroid．中心の数は data.enronqa.n_centroids）と 2 段目（flood_score の密ベクトル）の採点を test の全問で求める．
2. 時刻表・メンバー表・質問の順番は EQ7a（atrium.churn_eval）と同じ（稼働率 a，更新間隔 R，時刻表の種 N_SCHEDULE_SEEDS 個）．
3. 二段の方式は，メンバー表で見えている受信箱だけを 1 段目で採点して候補を選ぶ．centroid_probe は上位 K 個，device_probe は
   得点をデバイスごとに最大値でまとめた上位 D 台に載る全ての受信箱（同点はデバイスの番号の小さい方．実機の top_devices と同じ）．
   候補の載るノードへ probe を送り，返事のあった（実際に稼働中の）候補の中で 2 段目の得点の上位 m 個を選ぶ．返事の無かった分は補わない．
   問い合わせたノードは probe を送ったノード，無駄な問い合わせはそのうち離脱していたノードとする（本照会は数えない）．
4. 対照の multi_centroid・flood_score は atrium.churn_eval.route_under_churn でそのまま選ぶ（EQ7a と同じ値になるはず）．
5. hit を失った問を，(i) 正解のノードが離脱，(ii) 稼働中だがメンバー表で見えない，(iii) 見えて稼働中だが選ばれない，
   (iv) 1 段の方式で離脱済みの受信箱が上位 m 個の枠を占めた，に分ける（互いに排他）．二段の方式の (iii) は
   「候補に入らない」と「候補に入ったが上位 m 個に入らない」に分けて記録する．
6. 退行ゲート G1〜G4，判定 H-C2・E-1，記録 H-C3 を出す．ゲートが 1 つでも通らなければ判定しない．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from atrium.advert_eval import load_inboxes, load_query_set, score_flood_dense, score_multi
from atrium.arrays import F32Array, I64Array
from atrium.churn_eval import (
    AVAILABILITIES,
    JUDGED_AVAILABILITY,
    MEAN_UP_STEPS,
    N_SCHEDULE_SEEDS,
    REFRESH_STEPS,
    BoolArray,
    ChurnOutcome,
    judge_hc1,
    route_under_churn,
    simulate_availability,
    stale_view,
    summarize_runs,
)
from atrium.config import AtriumConfig
from atrium.device_probe_eval import device_index_of_inbox, device_scores
from atrium.manifest import Placement
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

CascadeKind = Literal["centroid_probe", "device_probe"]


@dataclass(frozen=True)
class Cascade:
    """評価する二段の方式の構成 1 個（方式と，候補の数 K またはデバイスの数 D）．"""

    name: str
    kind: CascadeKind
    size: int


# 事前登録の 4 構成（Iteration 4・7 の実機で測った K と D）．G3 の全数の構成は evaluate で配置から足す
JUDGED_CASCADES = (
    Cascade("centroid_probe_k75", "centroid_probe", 75),
    Cascade("centroid_probe_k30", "centroid_probe", 30),
    Cascade("device_probe_d24", "device_probe", 24),
    Cascade("device_probe_d12", "device_probe", 12),
)
SINGLE_STAGE_METHODS = ("multi_centroid", "flood_score")
FLOOD_METHOD = "flood_score"
# H-C2：device_probe（D = 12）の相対的な低下が multi_centroid より全ての種で小さい
HC2_METHOD = "device_probe_d12"
HC2_BASELINE = "multi_centroid"
# E-1：全ての種で (ii) の増加の割合がこの値以上なら確かめたとする（backlog B41）
E1_MIN_SHARE = 0.8
# G1 は device-probe-eval・cascade-eval と同じ許容
G1_TOLERANCE = 5e-4
COUNT_FIELDS = (
    "n",
    "n_hit",
    "n_gold_offline",
    "n_gold_invisible",
    "n_not_selected",
    "n_not_candidate",
    "n_not_top_m",
    "n_slot_taken",
)


@dataclass(frozen=True)
class LossBreakdown:
    """hit を失った問の分類（問ごとの印．互いに排他）．not_candidate・not_top_m は二段の方式だけが持つ．"""

    gold_offline: BoolArray
    gold_invisible: BoolArray
    not_selected: BoolArray
    slot_taken: BoolArray
    not_candidate: BoolArray | None
    not_top_m: BoolArray | None


def node_of_device(device_of_inbox: I64Array, node_of: I64Array) -> I64Array:
    """デバイスの番号（受信箱の名前の初出順）→ ノードの番号（placement.nodes の順）の表．

    2 つの番号の付け方は一般に一致しない．1 台のデバイスが 2 つのノードにまたがる配置は，デバイス単位の可視性が決まらないので止める．
    """
    n_dev = int(device_of_inbox.max()) + 1
    out = np.full(n_dev, -1, np.int64)
    for dev, node in zip(device_of_inbox.tolist(), node_of.tolist(), strict=True):
        if int(out[dev]) not in (-1, node):
            raise ValueError(f"device {dev} maps to nodes {int(out[dev])} and {node}")
        out[dev] = node
    return out


def visible_top_k(first: F32Array, view_inbox: BoolArray, k: int) -> BoolArray:
    """見えている受信箱の中で 1 段目の上位 k 個の印（同点は受信箱の番号の小さい方．cascade_scores と同じ規則）．"""
    masked = np.where(view_inbox, first, -np.inf)
    top = np.argsort(-masked, axis=1, kind="stable")[:, :k]
    mask = np.zeros(first.shape, np.bool_)
    np.put_along_axis(mask, top, True, axis=1)
    # 見えている受信箱が k 個に満たないと，-inf の受信箱が上位に入るので除く
    out: BoolArray = mask & view_inbox
    return out


def visible_top_devices(
    first: F32Array,
    view_inbox: BoolArray,
    device_of_inbox: I64Array,
    device_visible: BoolArray,
    d: int,
) -> BoolArray:
    """見えている受信箱の得点をデバイスごとに最大値でまとめ，見えているデバイスの上位 d 台に載る受信箱の印．

    同点はデバイスの番号の小さい方を先にする．実機の top_devices は見えているデータ源だけで初出順を数えるが，
    見えているデバイスどうしの前後は全体の初出順と変わらないので，見えないデバイスを -inf にした stable な並べ替えと同じになる．
    """
    masked: F32Array = np.where(view_inbox, first, -np.inf).astype(np.float32)
    per_device = device_scores(masked, device_of_inbox, "max")
    top = np.argsort(-per_device, axis=1, kind="stable")[:, :d]
    chosen = np.zeros(per_device.shape, np.bool_)
    np.put_along_axis(chosen, top, True, axis=1)
    chosen &= device_visible
    out: BoolArray = chosen[:, device_of_inbox]
    return out


def probed_nodes(candidates: BoolArray, node_of: I64Array, n_nodes: int) -> BoolArray:
    """候補の受信箱を持つノードの印 (問, ノード)（probe を送る先）．"""
    out = np.zeros((candidates.shape[0], n_nodes), np.bool_)
    for node in range(n_nodes):
        out[:, node] = candidates[:, node_of == node].any(axis=1)
    return out


def route_cascade_under_churn(
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
) -> tuple[ChurnOutcome, LossBreakdown]:
    """二段の方式で各問の受信箱を選び，hit・probe を送ったノードの数・離脱していたノードの数と，hit を失った理由を返す．

    2 段目の順位は route_under_churn と同じく，正解と同点の受信箱を正解より上に数える．
    """
    t = np.arange(first.shape[0])
    gold_arr = np.asarray(gold)
    view_inbox = view[:, node_of]
    online_inbox = online[:, node_of]
    if cascade.kind == "centroid_probe":
        candidates = visible_top_k(first, view_inbox, cascade.size)
    else:
        candidates = visible_top_devices(
            first, view_inbox, device_of_inbox, view[:, device_node], cascade.size
        )
    probed = probed_nodes(candidates, node_of, view.shape[1])
    # 返事のあった候補だけから選ぶ（離脱済みの候補は最後の m 個の枠を取らない）
    answered = candidates & online_inbox
    gold_score = second[t, gold_arr]
    rank = ((second >= gold_score[:, None]) & answered).sum(axis=1)
    hit = answered[t, gold_arr] & (rank <= m)
    gold_online = online_inbox[t, gold_arr]
    gold_visible = view_inbox[t, gold_arr]
    gold_candidate = candidates[t, gold_arr]
    reachable = gold_online & gold_visible
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
    return outcome, loss


def single_stage_breakdown(
    scores: F32Array,
    gold: Sequence[int],
    node_of: I64Array,
    view: BoolArray,
    online: BoolArray,
    m: int,
    flood: bool,
    hit: BoolArray,
) -> LossBreakdown:
    """route_under_churn（1 段の方式）で hit を失った問の理由．

    (iv) は flood_score 以外で，離脱済みの受信箱を除けば正解が m 位以内に入る問（見えている離脱済みの受信箱に枠を取られた）．
    """
    t = np.arange(scores.shape[0])
    gold_arr = np.asarray(gold)
    view_inbox = view[:, node_of]
    online_inbox = online[:, node_of]
    gold_online = online_inbox[t, gold_arr]
    gold_visible = view_inbox[t, gold_arr]
    reachable_miss = gold_online & gold_visible & ~hit
    if flood:
        slot_taken = np.zeros_like(hit)
    else:
        gold_score = scores[t, gold_arr]
        live_rank = ((scores >= gold_score[:, None]) & view_inbox & online_inbox).sum(axis=1)
        slot_taken = reachable_miss & (live_rank <= m)
    return LossBreakdown(
        gold_offline=~gold_online,
        gold_invisible=gold_online & ~gold_visible,
        not_selected=reachable_miss & ~slot_taken,
        slot_taken=slot_taken,
        not_candidate=None,
        not_top_m=None,
    )


def run_record(
    method: str,
    availability: float,
    refresh: int,
    seed: int,
    out: ChurnOutcome,
    loss: LossBreakdown,
) -> dict[str, Any]:
    """1 実行の記録（EQ7a の runs と同じ項目に，hit を失った理由の件数を足す）．"""
    return {
        "method": method,
        "availability": availability,
        "refresh": refresh,
        "seed": seed,
        "hit": float(out.hit.mean()),
        "hit_given_gold_online": float(out.hit[out.gold_online].mean())
        if out.gold_online.any()
        else 0.0,
        "gold_online_rate": float(out.gold_online.mean()),
        "contacted": float(out.contacted.mean()),
        "wasted": float(out.wasted.mean()),
        "n": len(out.hit),
        "n_hit": int(out.hit.sum()),
        "n_gold_offline": int(loss.gold_offline.sum()),
        "n_gold_invisible": int(loss.gold_invisible.sum()),
        "n_not_selected": int(loss.not_selected.sum()),
        "n_not_candidate": None if loss.not_candidate is None else int(loss.not_candidate.sum()),
        "n_not_top_m": None if loss.not_top_m is None else int(loss.not_top_m.sum()),
        "n_slot_taken": int(loss.slot_taken.sum()),
    }


def summarize_losses(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """（方式，稼働率，更新間隔）ごとの件数の種の和（二段の方式以外の内訳は None）．"""
    groups: dict[tuple[str, float, int], list[dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault((r["method"], r["availability"], r["refresh"]), []).append(r)
    out = []
    for (method, availability, refresh), rs in groups.items():
        entry: dict[str, Any] = {"method": method, "availability": availability, "refresh": refresh}
        for f in COUNT_FIELDS:
            values = [x[f] for x in rs]
            entry[f] = None if any(v is None for v in values) else int(sum(values))
        out.append(entry)
    return out


# ── 退行ゲート ──────────────────────────────────────────────────────────────


def gate_g1(runs: Sequence[dict[str, Any]], reference: dict[str, Any], m: int) -> dict[str, Any]:
    """G1：a = 1 の事前登録の 4 構成の hit@m が，Iteration 7（device-probe-eval）の test の値と G1_TOLERANCE 以内か．"""
    curve = reference["curve"]["test"]
    expected_all = {
        **{f"device_probe_d{r['d']}": r[f"max_hit@{m}"] for r in curve["device_probe"]},
        **{f"centroid_probe_k{r['k']}": r[f"hit@{m}"] for r in curve["centroid_probe"]},
    }
    expected = {c.name: float(expected_all[c.name]) for c in JUDGED_CASCADES}
    max_abs_diff = {}
    for name, value in expected.items():
        got = [r["hit"] for r in runs if r["method"] == name and r["availability"] == 1.0]
        max_abs_diff[name] = max(abs(g - value) for g in got)
    return {
        "tolerance": G1_TOLERANCE,
        "expected": expected,
        "max_abs_diff": max_abs_diff,
        "passed": reference["m"] == m and all(v <= G1_TOLERANCE for v in max_abs_diff.values()),
    }


def gate_g2(
    runs: Sequence[dict[str, Any]], reference: dict[str, Any], m: int, n_test: int
) -> dict[str, Any]:
    """G2：multi_centroid・flood_score の (a, R, 種) ごとの hit・contacted・wasted が EQ7a の runs と 1/n_test 以内か．"""
    tolerance = 1.0 / n_test
    ref = {
        (r["method"], r["availability"], r["refresh"], r["seed"]): r
        for r in reference["runs"]
        if r["method"] in SINGLE_STAGE_METHODS
    }
    max_abs_diff = {f: 0.0 for f in ("hit", "contacted", "wasted")}
    matched = 0
    missing = []
    for r in runs:
        if r["method"] not in SINGLE_STAGE_METHODS:
            continue
        key = (r["method"], r["availability"], r["refresh"], r["seed"])
        if key not in ref:
            missing.append(list(key))
            continue
        matched += 1
        for f in max_abs_diff:
            max_abs_diff[f] = max(max_abs_diff[f], abs(r[f] - ref[key][f]))
    return {
        "tolerance": tolerance,
        "matched": matched,
        "missing": missing,
        "max_abs_diff": max_abs_diff,
        "passed": reference["m"] == m
        and not missing
        and matched == len(ref)
        and all(v <= tolerance + 1e-12 for v in max_abs_diff.values()),
    }


def gate_g3(runs: Sequence[dict[str, Any]], full_methods: Sequence[str]) -> dict[str, Any]:
    """G3：K = 受信箱の数・D = デバイスの数の二段の方式が，全ての実行で flood_score と n_hit・contacted・wasted で完全に一致するか．"""
    flood = {
        (r["availability"], r["refresh"], r["seed"]): r for r in runs if r["method"] == FLOOD_METHOD
    }
    mismatches = []
    compared = 0
    for r in runs:
        if r["method"] not in full_methods:
            continue
        compared += 1
        f = flood[(r["availability"], r["refresh"], r["seed"])]
        for field in ("n_hit", "contacted", "wasted"):
            if r[field] != f[field]:
                mismatches.append(
                    {
                        "method": r["method"],
                        "availability": r["availability"],
                        "refresh": r["refresh"],
                        "seed": r["seed"],
                        "field": field,
                    }
                )
    return {
        "methods": list(full_methods),
        "compared": compared,
        "mismatches": mismatches,
        "passed": not mismatches and compared == len(full_methods) * len(flood),
    }


def gate_g4(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """G4：全ての実行で hit と (i)〜(iv) の件数の和が問の数に等しく，(iii) の内訳の和が (iii) に等しいか．"""
    violations = []
    for r in runs:
        total = (
            r["n_hit"]
            + r["n_gold_offline"]
            + r["n_gold_invisible"]
            + r["n_not_selected"]
            + r["n_slot_taken"]
        )
        split_ok = (
            r["n_not_candidate"] is None
            or r["n_not_candidate"] + r["n_not_top_m"] == r["n_not_selected"]
        )
        if total != r["n"] or not split_ok:
            violations.append(
                {k: r[k] for k in ("method", "availability", "refresh", "seed", "n")}
                | {"total": total}
            )
    return {"violations": violations, "passed": not violations}


# ── 判定 ──────────────────────────────────────────────────────────────────


def _verdict(flag: bool, gates_passed: bool, yes: str, no: str) -> str:
    """判定の文言（ゲートを通らなければ判定しない）．"""
    if not gates_passed:
        return "退行ゲートを通らないため判定しない"
    return yes if flag else no


def judge_hc2(hc3: dict[str, Any], gates_passed: bool) -> dict[str, Any]:
    """H-C2：HC2_METHOD の相対的な低下が HC2_BASELINE より全ての種で小さいか（hc3 は churn_eval.judge_hc1 の出力）．"""
    drops = hc3["per_method"][HC2_METHOD]["rel_drop"]
    base = hc3["per_method"][HC2_BASELINE]["rel_drop"]
    diffs = [d - b for d, b in zip(drops, base, strict=True)]
    supported = all(x < 0 for x in diffs)
    return {
        "method": HC2_METHOD,
        "baseline": HC2_BASELINE,
        "availability": hc3["availability"],
        "max_refresh": hc3["max_refresh"],
        "rel_drop": drops,
        "baseline_rel_drop": base,
        "minus_baseline": diffs,
        "supported": supported,
        "verdict": _verdict(supported, gates_passed, "支持", "支持されない"),
    }


def judge_e1(runs: Sequence[dict[str, Any]], gates_passed: bool) -> dict[str, Any]:
    """E-1：flood_score の a = 0.7 で，R = 1 → 最大の hit の減少の件数のうち (ii) の増加の件数の割合．

    時刻表は R に依らないので，正解が稼働中の問の数は R で変わらない．したがって件数の比は「正解が稼働中の問に限った hit の低下」
    のうち (ii) の増分で説明される割合に等しい（gold_online_same で確かめる）．全ての種で E1_MIN_SHARE 以上なら確かめたとし，
    減少が 0 以下の種は不成立とする．種の和の比は記録だけ（backlog B41）．
    """
    max_refresh = max(REFRESH_STEPS)
    by = {
        (r["seed"], r["refresh"]): r
        for r in runs
        if r["method"] == FLOOD_METHOD and r["availability"] == JUDGED_AVAILABILITY
    }
    per_seed = []
    for s in range(N_SCHEDULE_SEEDS):
        first, last = by[(s, 1)], by[(s, max_refresh)]
        drop = first["n_hit"] - last["n_hit"]
        gain = last["n_gold_invisible"] - first["n_gold_invisible"]
        per_seed.append(
            {
                "seed": s,
                "hit_drop": drop,
                "invisible_gain": gain,
                "share": gain / drop if drop > 0 else None,
                "gold_online_same": first["n"] - first["n_gold_offline"]
                == last["n"] - last["n_gold_offline"],
            }
        )
    total_drop = sum(p["hit_drop"] for p in per_seed)
    total_gain = sum(p["invisible_gain"] for p in per_seed)
    confirmed = all(p["share"] is not None and p["share"] >= E1_MIN_SHARE for p in per_seed)
    return {
        "method": FLOOD_METHOD,
        "availability": JUDGED_AVAILABILITY,
        "max_refresh": max_refresh,
        "min_share": E1_MIN_SHARE,
        "per_seed": per_seed,
        "pooled_share": total_gain / total_drop if total_drop > 0 else None,
        "confirmed": confirmed,
        "verdict": _verdict(confirmed, gates_passed, "確かめた", "確かめられない"),
    }


# ── 評価の本体 ────────────────────────────────────────────────────────────


def evaluate(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    placement: Placement,
    device_probe_reference: dict[str, Any],
    churn_reference: dict[str, Any],
) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．device_probe_reference は Iteration 7 の device-probe-eval，churn_reference は EQ7a の metrics.json．"""
    from atrium.data_enronqa import kmeans_centroids

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    names = [ib.name for ib in inboxes]
    test = load_query_set(cfg, paths, "test")
    n_test = len(test.qids)
    # ノードの番号は churn_eval と同じ placement.nodes の順，デバイスの番号は device_probe_eval と同じ初出順
    node_index = {sid: i for i, node in enumerate(placement.nodes) for sid in node.shard_ids}
    node_of = np.array([node_index[n] for n in names], np.int64)
    device_of_inbox = device_index_of_inbox(names, placement)
    device_node = node_of_device(device_of_inbox, node_of)
    n_devices = len(device_node)

    # 1. 採点（EQ7a の multi_centroid・flood_score と同じ関数）
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

    # 2〜5. 時刻表ごとに，メンバー表の更新間隔を変えて経路を選ぶ（乱数の引き方は EQ7a と同じ）
    runs: list[dict[str, Any]] = []
    for availability in AVAILABILITIES:
        for s in range(N_SCHEDULE_SEEDS):
            rng = np.random.default_rng([seed, s, round(availability * 100)])
            online = simulate_availability(
                len(placement.nodes), n_test, availability, MEAN_UP_STEPS, rng
            )
            for refresh in REFRESH_STEPS:
                view = stale_view(online, refresh)
                for name, scores in (("multi_centroid", first), (FLOOD_METHOD, second)):
                    flood = name == FLOOD_METHOD
                    out = route_under_churn(scores, gold, node_of, view, online, m, flood)
                    loss = single_stage_breakdown(
                        scores, gold, node_of, view, online, m, flood, out.hit
                    )
                    runs.append(run_record(name, availability, refresh, s, out, loss))
                for c in cascades:
                    out, loss = route_cascade_under_churn(
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
                    )
                    runs.append(run_record(c.name, availability, refresh, s, out, loss))
        logger.info("availability %.2f done", availability)

    # 6. ゲートと判定
    gates = {
        "g1": gate_g1(runs, device_probe_reference, m),
        "g2": gate_g2(runs, churn_reference, m, n_test),
        "g3": gate_g3(runs, [c.name for c in full]),
        "g4": gate_g4(runs),
    }
    gates_passed = all(g["passed"] for g in gates.values())
    hc3 = judge_hc1(runs, methods)
    return {
        "n_inboxes": len(names),
        "n_nodes": len(placement.nodes),
        "n_devices": n_devices,
        "n_test": n_test,
        "m": m,
        "n_centroids": e.n_centroids,
        "mean_up_steps": MEAN_UP_STEPS,
        "n_schedule_seeds": N_SCHEDULE_SEEDS,
        "methods": methods,
        "cascades": [{"name": c.name, "kind": c.kind, "size": c.size} for c in cascades],
        "gates": {**gates, "passed": gates_passed},
        "hc2": judge_hc2(hc3, gates_passed),
        "hc3": hc3,
        "e1": judge_e1(runs, gates_passed),
        "summary": summarize_runs(runs),
        "losses": summarize_losses(runs),
        "runs": runs,
        "duration_s": time.perf_counter() - start,
    }


def _num(value: float | None, digits: int) -> str:
    """None を「-」にした数値の表記．"""
    return "-" if value is None else f"{value:.{digits}f}"


def _count(value: int | None) -> str:
    """None を「-」にした件数の表記．"""
    return "-" if value is None else str(value)


def _stat(s: dict[str, float]) -> str:
    """種をまたいだ平均 [最小, 最大] の表記．"""
    return f"{s['mean']:.4f} [{s['min']:.4f}, {s['max']:.4f}]"


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    gates = metrics["gates"]
    g1, g2, g3, g4 = gates["g1"], gates["g2"], gates["g3"], gates["g4"]
    hc2, hc3, e1 = metrics["hc2"], metrics["hc3"], metrics["e1"]
    a, max_refresh = hc3["availability"], hc3["max_refresh"]
    methods: list[str] = metrics["methods"]

    def ok(passed: bool) -> str:
        return "通過" if passed else "**不通過**"

    def by_method(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows, key=lambda x: (methods.index(x["method"]), -x["availability"], x["refresh"])
        )

    lines = [
        "# 二段の方式の参加・離脱への頑健さのオフラインの評価（Iteration 15）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個（ノード {metrics['n_nodes']} 台，デバイス {metrics['n_devices']} 台），"
        f"test {metrics['n_test']} 問，m = {m}，中心の数 C = {metrics['n_centroids']}，"
        f"稼働中の平均の長さ {metrics['mean_up_steps']} 問，時刻表の種 {metrics['n_schedule_seeds']} 個",
        f"- 退行ゲート：{'全て通過' if gates['passed'] else '**不通過あり（判定しない）**'}",
        "",
        "## 退行ゲート",
        "",
        f"- G1（a = 1 の hit@{m} が Iteration 7 と {g1['tolerance']} 以内）：{ok(g1['passed'])}（差の最大 "
        + ", ".join(f"{k} {v:.6f}" for k, v in g1["max_abs_diff"].items())
        + "）",
        f"- G2（multi_centroid・flood_score が EQ7a と {g2['tolerance']:.6f} 以内）：{ok(g2['passed'])}"
        f"（照合 {g2['matched']} 件，欠け {len(g2['missing'])} 件，差の最大 "
        + ", ".join(f"{k} {v:.6f}" for k, v in g2["max_abs_diff"].items())
        + "）",
        f"- G3（{'・'.join(g3['methods'])} が flood_score と完全に一致）：{ok(g3['passed'])}"
        f"（照合 {g3['compared']} 件，不一致 {len(g3['mismatches'])} 件）",
        f"- G4（hit と (i)〜(iv) の和が問の数に等しい）：{ok(g4['passed'])}（不一致 {len(g4['violations'])} 件）",
        "",
        f"## H-C2（a = {a}，R = 1 → {max_refresh} の hit@{m} の相対的な低下）",
        "",
        f"| 種 | {hc2['method']} | {hc2['baseline']} | 差 |",
        "|---|---|---|---|",
        *(
            f"| {s} | {x:.4f} | {y:.4f} | {d:+.4f} |"
            for s, (x, y, d) in enumerate(
                zip(hc2["rel_drop"], hc2["baseline_rel_drop"], hc2["minus_baseline"], strict=True)
            )
        ),
        "",
        f"判定（全ての種で {hc2['method']} の方が小さい）：{hc2['verdict']}",
        "",
        "## H-C3（記録．相対的な低下と flood_score との差）",
        "",
        "flood_score の相対的な低下（種ごと）："
        + ", ".join(f"{x:.4f}" for x in hc3["flood_rel_drop"]),
        "",
        "| 方式 | 相対的な低下（種ごと） | flood_score との差（種ごと） |",
        "|---|---|---|",
        *(
            f"| {name} | {', '.join(f'{x:.4f}' for x in p['rel_drop'])} "
            f"| {', '.join(f'{x:+.4f}' for x in p['minus_flood'])} |"
            for name, p in hc3["per_method"].items()
        ),
        "",
        f"## E-1（flood_score，a = {a}，R = 1 → {max_refresh}）",
        "",
        "| 種 | hit の減少（問） | (ii) の増加（問） | 割合 | 正解が稼働中の問の数が R で等しい |",
        "|---|---|---|---|---|",
        *(
            f"| {p['seed']} | {p['hit_drop']} | {p['invisible_gain']} | {_num(p['share'], 4)} | {p['gold_online_same']} |"
            for p in e1["per_seed"]
        ),
        "",
        f"- 種の和の比（記録．判定に使わない）：{_num(e1['pooled_share'], 4)}",
        f"- 判定（全ての種で {e1['min_share']} 以上）：{e1['verdict']}",
        "",
        f"## hit を失った問の分解（a = {a}，種の和）",
        "",
        "| 方式 | R | 問 | hit | (i) 離脱 | (ii) 見えない | (iii) 選ばれない | うち候補外 | うち上位 m 外 | (iv) 枠を取られた |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {x['method']} | {x['refresh']} | {x['n']} | {x['n_hit']} | {x['n_gold_offline']} "
            f"| {x['n_gold_invisible']} | {x['n_not_selected']} | {_count(x['n_not_candidate'])} "
            f"| {_count(x['n_not_top_m'])} | {x['n_slot_taken']} |"
            for x in by_method([x for x in metrics["losses"] if x["availability"] == a])
        ),
        "",
        "## 全条件（値は種の平均 [最小, 最大]）",
        "",
        f"| 方式 | a | R | hit@{m} | 正解が稼働中のときの hit@{m} | 問い合わせたノード | うち離脱していたノード |",
        "|---|---|---|---|---|---|---|",
        *(
            f"| {s['method']} | {s['availability']} | {s['refresh']} | {_stat(s['hit'])} "
            f"| {_stat(s['hit_given_gold_online'])} | {s['contacted']['mean']:.2f} | {s['wasted']['mean']:.2f} |"
            for s in by_method(metrics["summary"])
        ),
    ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
