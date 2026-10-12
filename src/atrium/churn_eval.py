"""RQ-C のオフラインの評価（p0004 EQ7a）：専門家の参加・離脱と質問者のメンバー表の古さが，見つけてもらえる度合いと無駄な問い合わせに与える影響．

`atrium churn-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（journal の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. EQ2 の test の質問に，各方式のスコアを 1 回だけ計算する．公開情報の大きさは EQ2 で選んで config.yaml に入れた値
   （data.enronqa の card_subjects・term_sketch_size・n_centroids），問い合わせる数 m は routing.top_m.enronqa．
2. 受信箱は placement.json の配置に従い，ノード単位で参加・離脱する．各ノードの状態は 2 状態のマルコフ連鎖で，1 問を 1 刻みとする．
   稼働率 a，稼働中の平均の長さ MEAN_UP_STEPS 問．最初の状態は定常分布から引く．質問の順番は experiment.seed で並べ替える．
3. 質問者は R 問ごとにだけメンバー表を更新し，その時点で稼働中のノードだけが見えているとする．
   - 公開情報を使う方式と ragroute：見えている受信箱の中でスコアの上位 m 個に問い合わせる．離脱したノードへの問い合わせは無駄になる．
   - flood_score：見えているノード全部に問い合わせ，返事のあった（実際に稼働中の）受信箱の中で上位 m 個を選ぶ．
     実機の質問者は 1 台でも失敗すると問全体を失敗にする（gather_or_cancel）が，ここでは返事のあった分で続けるとする．
4. hit@m は「正解の受信箱が稼働中で，選ばれた m 個に入る」割合．正解の受信箱が稼働中の問に限った値も出す．

離脱の無い条件（a = 1）の hit@m は EQ2 の test の hit@m と一致するはずなので，退行の確認に使う．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from atrium.arrays import F32Array, I64Array
from atrium.config import AtriumConfig
from atrium.manifest import Placement
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

BoolArray = npt.NDArray[np.bool_]

AVAILABILITIES = (1.0, 0.9, 0.7, 0.5)
REFRESH_STEPS = (1, 10, 100, 1000)
MEAN_UP_STEPS = 200
N_SCHEDULE_SEEDS = 5
# 実機の質問者で動く方式だけを評価する（BM25 の flood は H-A3 の確認用で，実機には無い）
METHODS = ("card_sim", "term_sketch", "centroid_sim", "multi_centroid", "flood_score", "ragroute")
FLOOD_METHODS = frozenset({"flood_score"})
# H-C1 の判定に使う条件（事前登録．multi_centroid は EQ2 で hit が最も高かった公開情報の方式）
JUDGED_AVAILABILITY = 0.7
JUDGED_METHOD = "multi_centroid"
STAT_FIELDS = ("hit", "hit_given_gold_online", "gold_online_rate", "contacted", "wasted")


def simulate_availability(
    n_nodes: int, n_steps: int, availability: float, mean_up: float, rng: np.random.Generator
) -> BoolArray:
    """各刻みの各ノードの稼働状態（[刻み, ノード]）．稼働率が availability になるよう停止中の平均の長さを決める．"""
    if availability >= 1.0:
        return np.ones((n_steps, n_nodes), np.bool_)
    # 定常分布で a / mean_up = (1 - a) / mean_down が成り立つ
    p_down = 1.0 / mean_up
    p_up = availability / (mean_up * (1.0 - availability))
    out = np.empty((n_steps, n_nodes), np.bool_)
    state = rng.random(n_nodes) < availability
    for t in range(n_steps):
        out[t] = state
        u = rng.random(n_nodes)
        state = np.where(state, u >= p_down, u < p_up)
    return out


def stale_view(online: BoolArray, refresh: int) -> BoolArray:
    """refresh 問ごとに更新するメンバー表で，各問の時点で見えているノード（直前の更新の時点の状態）．"""
    last = (np.arange(online.shape[0]) // refresh) * refresh
    out: BoolArray = online[last]
    return out


@dataclass(frozen=True)
class ChurnOutcome:
    """問ごとの結果．"""

    hit: BoolArray
    gold_online: BoolArray
    contacted: I64Array
    wasted: I64Array


def route_under_churn(
    scores: F32Array,
    gold: Sequence[int],
    node_of: I64Array,
    view: BoolArray,
    online: BoolArray,
    m: int,
    flood: bool,
) -> ChurnOutcome:
    """各問で問い合わせる受信箱を選び，hit と問い合わせたノード・離脱していたノードの数を数える．

    順位は advert_eval.ranks_of_gold と同じく，正解と同点の受信箱を正解より上に数える．
    """
    t = np.arange(scores.shape[0])
    gold_arr = np.asarray(gold)
    view_inbox = view[:, node_of]
    online_inbox = online[:, node_of]
    # flood は返事のあった受信箱の中から選ぶ．それ以外は見えている受信箱の中から選ぶ（離脱済みの受信箱も枠を占める）
    candidates = view_inbox & online_inbox if flood else view_inbox
    gold_score = scores[t, gold_arr]
    rank = ((scores >= gold_score[:, None]) & candidates).sum(axis=1)
    gold_online = online_inbox[t, gold_arr]
    hit = candidates[t, gold_arr] & gold_online & (rank <= m)
    if flood:
        contacted_nodes = view
    else:
        masked = np.where(view_inbox, scores, -np.inf)
        top = np.argsort(-masked, axis=1, kind="stable")[:, :m]
        chosen = np.take_along_axis(view_inbox, top, axis=1)
        contacted_nodes = np.zeros_like(view)
        rows = np.broadcast_to(t[:, None], top.shape)
        contacted_nodes[rows[chosen], node_of[top][chosen]] = True
    return ChurnOutcome(
        hit=hit,
        gold_online=gold_online,
        contacted=contacted_nodes.sum(axis=1),
        wasted=(contacted_nodes & ~online).sum(axis=1),
    )


def summarize_runs(runs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """（方式，稼働率，更新間隔）ごとに，時刻表の種をまたいだ平均・最小・最大．"""
    groups: dict[tuple[str, float, int], list[dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault((r["method"], r["availability"], r["refresh"]), []).append(r)
    summary = []
    for (method, availability, refresh), rs in groups.items():
        entry: dict[str, Any] = {"method": method, "availability": availability, "refresh": refresh}
        for f in STAT_FIELDS:
            values = [float(x[f]) for x in rs]
            entry[f] = {"mean": float(np.mean(values)), "min": min(values), "max": max(values)}
        summary.append(entry)
    return summary


def judge_hc1(runs: Sequence[dict[str, Any]], methods: Sequence[str]) -> dict[str, Any]:
    """H-C1：メンバー表が古くなると（R = 1 → 最大），hit@m の相対的な低下は公開情報を使う方式の方が flood_score より大きい．

    判定は JUDGED_AVAILABILITY の JUDGED_METHOD で，全ての種で flood_score より大きいときだけ支持とする．
    """
    max_refresh = max(REFRESH_STEPS)

    def rel_drop(method: str, seed: int) -> float:
        hit = {
            r["refresh"]: r["hit"]
            for r in runs
            if r["method"] == method
            and r["seed"] == seed
            and r["availability"] == JUDGED_AVAILABILITY
        }
        return float(1.0 - hit[max_refresh] / hit[1]) if hit[1] > 0 else 0.0

    seeds = range(N_SCHEDULE_SEEDS)
    flood = [rel_drop("flood_score", s) for s in seeds]
    per_method = {}
    for method in methods:
        if method in FLOOD_METHODS:
            continue
        drops = [rel_drop(method, s) for s in seeds]
        diffs = [d - f for d, f in zip(drops, flood, strict=True)]
        per_method[method] = {
            "rel_drop": drops,
            "minus_flood": diffs,
            "larger_in_all_seeds": all(d > 0 for d in diffs),
        }
    return {
        "availability": JUDGED_AVAILABILITY,
        "max_refresh": max_refresh,
        "judged_method": JUDGED_METHOD,
        "flood_rel_drop": flood,
        "per_method": per_method,
        "supported": per_method[JUDGED_METHOD]["larger_in_all_seeds"],
    }


def regression_check(
    no_churn: dict[str, float], reference: dict[str, Any], m: int, n_test: int
) -> dict[str, Any]:
    """離脱の無い hit@m が EQ2 の test の値と 1 問以内で一致するか（card の埋め込みは GPU で計算し直すので末尾の桁が揺れうる）．"""
    tolerance = 1.0 / n_test
    diff = {name: hit - reference["test"][name][f"hit@{m}"] for name, hit in no_churn.items()}
    passed = reference["m"] == m and all(abs(d) <= tolerance + 1e-12 for d in diff.values())
    return {"tolerance": tolerance, "diff": diff, "passed": passed}


def evaluate(
    cfg: AtriumConfig, paths: DatasetPaths, placement: Placement, reference: dict[str, Any]
) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．reference は EQ2 の metrics.json（退行の確認に使う）．"""
    from atrium.advert_eval import (
        build_methods,
        hit_at,
        load_inboxes,
        load_query_set,
        ranks_of_gold,
    )

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    test = load_query_set(cfg, paths, "test")
    node_index = {sid: i for i, node in enumerate(placement.nodes) for sid in node.shard_ids}
    node_of = np.array([node_index[ib.name] for ib in inboxes], np.int64)

    # 1. 各方式のスコア（EQ2 で選んだ公開情報の大きさ．所有者の特定は使わないので probe は 1 問だけ渡す）
    params = {
        "card_sim": e.card_subjects,
        "term_sketch": e.term_sketch_size,
        "multi_centroid": e.n_centroids,
    }
    scores: dict[str, F32Array] = {}
    for method in build_methods(cfg, inboxes, paths):
        if method.name not in METHODS:
            continue
        scores[method.name], _, _ = method.run(
            params.get(method.name, 0), test.emb, test.texts, test.emb[:1], test.texts[:1]
        )
        logger.info("scored %s", method.name)
    no_churn = {name: hit_at(ranks_of_gold(s, test.gold), m) for name, s in scores.items()}

    # 2・3. 時刻表ごとに，メンバー表の更新間隔を変えて経路を選ぶ
    seed = cfg.experiment.seed
    order = np.random.default_rng(seed).permutation(len(test.qids))
    gold = [test.gold[i] for i in order]
    runs: list[dict[str, Any]] = []
    for availability in AVAILABILITIES:
        for s in range(N_SCHEDULE_SEEDS):
            rng = np.random.default_rng([seed, s, round(availability * 100)])
            online = simulate_availability(
                len(placement.nodes), len(order), availability, MEAN_UP_STEPS, rng
            )
            for refresh in REFRESH_STEPS:
                view = stale_view(online, refresh)
                for name, sc in scores.items():
                    out = route_under_churn(
                        sc[order], gold, node_of, view, online, m, name in FLOOD_METHODS
                    )
                    runs.append(
                        {
                            "method": name,
                            "availability": availability,
                            "refresh": refresh,
                            "seed": s,
                            "hit": float(out.hit.mean()),
                            "hit_given_gold_online": float(out.hit[out.gold_online].mean())
                            if out.gold_online.any()
                            else 0.0,
                            "gold_online_rate": float(out.gold_online.mean()),
                            "contacted": float(out.contacted.mean()),
                            "wasted": float(out.wasted.mean()),
                        }
                    )
        logger.info("availability %.2f done", availability)
    return {
        "n_inboxes": len(inboxes),
        "n_nodes": len(placement.nodes),
        "n_test": len(test.qids),
        "m": m,
        "params": params,
        "mean_up_steps": MEAN_UP_STEPS,
        "n_schedule_seeds": N_SCHEDULE_SEEDS,
        "no_churn_hit": no_churn,
        "regression": regression_check(no_churn, reference, m, len(test.qids)),
        "summary": summarize_runs(runs),
        "hc1": judge_hc1(runs, list(scores)),
        "runs": runs,
        "duration_s": time.perf_counter() - start,
    }


def _stat(s: dict[str, float]) -> str:
    return f"{s['mean']:.3f} [{s['min']:.3f}, {s['max']:.3f}]"


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    reg = metrics["regression"]
    diffs = ", ".join(f"{k} {v:+.4f}" for k, v in reg["diff"].items())
    lines = [
        "# EQ7a：RQ-C のオフラインの評価（専門家の参加・離脱とメンバー表の古さ）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個（ノード {metrics['n_nodes']} 台），test {metrics['n_test']} 問，m = {m}，"
        f"公開情報の大きさ {metrics['params']}",
        f"- 稼働中の平均の長さ {metrics['mean_up_steps']} 問，時刻表の種 {metrics['n_schedule_seeds']} 個（値は平均 [最小, 最大]）",
        f"- 退行の確認（離脱なしの hit@{m} と EQ2 の差が 1 問以内）：{'通過' if reg['passed'] else '不一致'}（{diffs}）",
        "",
        f"| 方式 | 稼働率 a | 更新間隔 R | hit@{m} | 正解が稼働中のときの hit@{m} | 正解が稼働中の割合 | 問い合わせたノード | うち離脱していたノード |",
        "|---|---|---|---|---|---|---|---|",
    ]
    rows = sorted(
        metrics["summary"],
        key=lambda x: (METHODS.index(x["method"]), -x["availability"], x["refresh"]),
    )
    for s in rows:
        lines.append(
            f"| {s['method']} | {s['availability']} | {s['refresh']} | {_stat(s['hit'])} | "
            f"{_stat(s['hit_given_gold_online'])} | {s['gold_online_rate']['mean']:.3f} | "
            f"{s['contacted']['mean']:.2f} | {s['wasted']['mean']:.2f} |"
        )
    h = metrics["hc1"]
    flood = ", ".join(f"{x:.3f}" for x in h["flood_rel_drop"])
    lines += [
        "",
        f"## H-C1（a = {h['availability']}，R = 1 → {h['max_refresh']} の hit@{m} の相対的な低下）",
        "",
        f"flood_score の相対的な低下（種ごと）：{flood}",
        "",
        "| 方式 | 相対的な低下（種ごと） | flood_score との差（種ごと） | 全ての種で flood_score より大きい |",
        "|---|---|---|---|",
    ]
    for name, p in h["per_method"].items():
        drops = ", ".join(f"{x:.3f}" for x in p["rel_drop"])
        minus = ", ".join(f"{x:+.3f}" for x in p["minus_flood"])
        lines.append(
            f"| {name} | {drops} | {minus} | {'はい' if p['larger_in_all_seeds'] else 'いいえ'} |"
        )
    lines += ["", f"判定（{h['judged_method']}）：{'支持' if h['supported'] else '支持されない'}"]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
