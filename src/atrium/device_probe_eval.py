"""device_probe（デバイスを単位に上位を選ぶ二段の方式．Iteration 7）のオフラインの評価：D ごとの hit@m と，centroid_probe との比較．

`atrium device-probe-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（規則は journal の Iteration 7 の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. 1 段目の採点（multi_centroid．中心の数は data.enronqa.n_centroids）と，2 段目の採点（flood_score の密ベクトル）を全受信箱で求める．
2. 受信箱の得点をデバイス（実機の配置のノード）ごとにまとめ（主は最大値．参考に和），上位 D 台に載る全ての受信箱を候補とし，
   候補の 2 段目の採点の上位 m 個を選ぶ．1 問あたりの要求の数は D（probe）＋ m（本照会）である．
3. 同じ採点で centroid_probe（上位 K 個）の hit@m と届くデバイスの数を出す（比べる相手．Iteration 1・4 の値との一致を確かめる）．
4. D の選択（dev だけを見る）：比べる K（75 と 30）ごとに，最大値でまとめた device_probe の dev の hit@m が，同じ K の
   centroid_probe の dev の hit@m 以上になる最小の D．その D が「見込みあり」（D ＋ m ≤ REQUEST_RATIO ×（K の dev の届くデバイスの数の平均 ＋ m））のときだけ，実機で測る．
5. test で，選んだ D の hit@m が同じ K の centroid_probe の hit@m − TEST_TOLERANCE 以上か（劣らないこと）を確かめる．
6. 正しさの確認：D = デバイスの数で flood_score と，最大値でまとめた上位 D 台が「届くデバイスの数が D 以下の上位 K 個」を全て含むこと，
   Iteration 1（cascade-eval）の centroid_probe の dev・test の値と一致すること．
7. 実機の照合用に，実機の質問（questions.jsonl の受信箱ごとの先頭 2 問）の，選んだ D での選ばれる受信箱を出す．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np

from atrium.advert_eval import (
    hit_at,
    load_inboxes,
    load_query_set,
    ranks_of_gold,
    score_flood_dense,
    score_multi,
)
from atrium.arrays import F32Array, I64Array
from atrium.benchmarks import load_questions
from atrium.cascade_eval import cascade_scores, hit_stats
from atrium.config import AtriumConfig
from atrium.manifest import Placement
from atrium.paths import DatasetPaths
from atrium.probe_reach import LIVE_QUESTION_LIMIT, candidates_of, devices_reached, selected_sources

logger = logging.getLogger(__name__)

Aggregate = Literal["max", "sum"]
# 主は最大値（事前登録）．和は参考（判定に使わない）
AGGREGATES: tuple[Aggregate, ...] = ("max", "sum")
# 偶数の D を全デバイスまで（30 台なら 2〜30）
D_STEP = 2
# centroid_probe の比べる K（Iteration 4・6 の実機で測った 2 点）と，曲線に並べる K
TARGET_KS = (75, 30)
K_CURVE = (4, 8, 15, 30, 50, 75)
# 見込みあり：device_probe の要求の数が centroid_probe の要求の数の REQUEST_RATIO 倍以下
# （質問者が律速の間は処理量が 1 問あたりの要求の数に反比例するので，処理量で約 1.11 倍以上の見込み）
REQUEST_RATIO = 0.9
# test で centroid_probe に劣らないとみなす hit@m の差
TEST_TOLERANCE = 0.01
# Iteration 1 の値との一致（cascade-eval と同じ許容）
REGRESSION_TOLERANCE = 5e-4


def device_index_of_inbox(names: Sequence[str], placement: Placement) -> I64Array:
    """受信箱ごとのデバイスの番号．番号は names の中でそのデバイスが最初に現れた順（実機の top_devices の同点の規則と同じ）．"""
    node_of_shard = {sid: node.url for node in placement.nodes for sid in node.shard_ids}
    missing = [n for n in names if n not in node_of_shard]
    if missing:
        raise ValueError(f"inboxes without a node in the placement: {missing[:5]}")
    order: dict[str, int] = {}
    for n in names:
        order.setdefault(node_of_shard[n], len(order))
    return np.asarray([order[node_of_shard[n]] for n in names], dtype=np.int64)


def device_scores(first: F32Array, device_of_inbox: I64Array, aggregate: Aggregate) -> F32Array:
    """1 段目の得点をデバイスごとにまとめた (問, デバイス) の行列（max か sum）．"""
    n_dev = int(device_of_inbox.max()) + 1
    out = np.full((first.shape[0], n_dev), -np.inf if aggregate == "max" else 0.0, np.float32)
    for dev in range(n_dev):
        cols = first[:, device_of_inbox == dev]
        out[:, dev] = cols.max(axis=1) if aggregate == "max" else cols.sum(axis=1)
    return out


def device_candidate_mask(
    first: F32Array, device_of_inbox: I64Array, d: int, aggregate: Aggregate
) -> np.ndarray[Any, np.dtype[np.bool_]]:
    """上位 d 台のデバイスに載る受信箱の印 (問, 受信箱)．同点はデバイスの番号の小さい方を先にする．"""
    top = np.argsort(-device_scores(first, device_of_inbox, aggregate), axis=1, kind="stable")[
        :, :d
    ]
    mask: np.ndarray[Any, np.dtype[np.bool_]] = (
        device_of_inbox[None, None, :] == top[:, :, None]
    ).any(axis=1)
    return mask


def masked_scores(second: F32Array, mask: np.ndarray[Any, np.dtype[np.bool_]]) -> F32Array:
    """候補の 2 段目の採点（候補以外は -inf）．"""
    out: F32Array = np.where(mask, second, -np.inf).astype(np.float32)
    return out


def containment_violations(first: F32Array, device_of_inbox: I64Array, d: int, k: int) -> int:
    """最大値でまとめた上位 d 台が，届くデバイスの数が d 以下の問の上位 k 個を含まない問の数（正しい実装なら 0）．"""
    top_k = candidates_of(first, k)
    reach = devices_reached(top_k, device_of_inbox)
    mask = device_candidate_mask(first, device_of_inbox, d, "max")
    covered = np.take_along_axis(mask, top_k, axis=1).all(axis=1)
    return int(np.sum((reach <= d) & ~covered))


def choose_d(dev_hit_by_d: dict[int, float], target: float) -> int | None:
    """dev の hit@m が target 以上になる最小の D（無ければ None）．"""
    for d in sorted(dev_hit_by_d):
        if dev_hit_by_d[d] >= target:
            return d
    return None


def is_promising(d: int, reach_mean: float, m: int) -> bool:
    """D ＋ m（要求の数）が，centroid_probe の要求の数（届くデバイスの数 ＋ m）の REQUEST_RATIO 倍以下か．"""
    return d + m <= REQUEST_RATIO * (reach_mean + m)


def evaluate(
    cfg: AtriumConfig, paths: DatasetPaths, placement: Placement, reference: dict[str, Any]
) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．reference は Iteration 1 の cascade-eval の metrics.json．"""
    from atrium.data_enronqa import kmeans_centroids

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    names = [ib.name for ib in inboxes]
    device_of_inbox = device_index_of_inbox(names, placement)
    n_dev = int(device_of_inbox.max()) + 1
    d_values = [*range(D_STEP, n_dev, D_STEP), n_dev]

    centers = [kmeans_centroids(ib.emb, e.n_centroids, seed) for ib in inboxes]
    sets = {split: load_query_set(cfg, paths, split) for split in ("dev", "test")}
    first = {s: score_multi(q.emb, centers) for s, q in sets.items()}
    second = {s: score_flood_dense(q.emb, inboxes) for s, q in sets.items()}

    # 手順 2・3：D ごと（まとめ方ごと）と K ごとの順位
    device_ranks: dict[str, dict[Aggregate, dict[int, Any]]] = {
        s: {
            agg: {
                d: ranks_of_gold(
                    masked_scores(
                        second[s], device_candidate_mask(first[s], device_of_inbox, d, agg)
                    ),
                    sets[s].gold,
                )
                for d in d_values
            }
            for agg in AGGREGATES
        }
        for s in sets
    }
    k_ranks = {
        s: {k: ranks_of_gold(cascade_scores(first[s], second[s], k), sets[s].gold) for k in K_CURVE}
        for s in sets
    }
    k_reach = {
        s: {
            k: float(np.mean(devices_reached(candidates_of(first[s], k), device_of_inbox)))
            for k in K_CURVE
        }
        for s in sets
    }
    flood_ranks = {s: ranks_of_gold(second[s], sets[s].gold) for s in sets}

    # 手順 4・5：D の選択（dev）と test での確認（最大値でまとめたものだけ）
    dev_hit_max = {d: hit_at(device_ranks["dev"]["max"][d], m) for d in d_values}
    choices: list[dict[str, Any]] = []
    for k in TARGET_KS:
        target = hit_at(k_ranks["dev"][k], m)
        d = choose_d(dev_hit_max, target)
        promising = d is not None and is_promising(d, k_reach["dev"][k], m)
        test_cp = hit_at(k_ranks["test"][k], m)
        test_dp = None if d is None else hit_at(device_ranks["test"]["max"][d], m)
        choices.append(
            {
                "k": k,
                "dev_target_hit": target,
                "dev_reach_mean": k_reach["dev"][k],
                "chosen_d": d,
                "promising": promising,
                "request_ratio": None if d is None else (d + m) / (k_reach["dev"][k] + m),
                "test_centroid_probe_hit": test_cp,
                "test_device_probe_hit": test_dp,
                "test_reach_mean": k_reach["test"][k],
                "test_not_inferior": test_dp is not None and test_dp >= test_cp - TEST_TOLERANCE,
            }
        )

    # 手順 6：正しさの確認
    flood_test = hit_at(flood_ranks["test"], m)
    containment = {
        f"d{d}_k{k}": containment_violations(first["test"], device_of_inbox, d, k)
        for d in d_values
        for k in TARGET_KS
    }
    ref_dev = {int(k): v for k, v in reference["dev_hit_by_k"].items()}
    ref_test = {c["k"]: c[f"hit@{m}"] for c in reference["test"]["curve"]}
    diffs = {
        **{f"dev_k{k}": hit_at(k_ranks["dev"][k], m) - ref_dev[k] for k in K_CURVE},
        **{f"test_k{k}": hit_at(k_ranks["test"][k], m) - ref_test[k] for k in K_CURVE},
        "test_flood": flood_test - reference["test"]["flood_score"][f"hit@{m}"],
        "test_d_all_vs_flood": hit_at(device_ranks["test"]["max"][n_dev], m) - flood_test,
    }
    checks = {
        "tolerance": REGRESSION_TOLERANCE,
        "diffs": diffs,
        "containment_violations": containment,
        "passed": all(abs(v) <= REGRESSION_TOLERANCE for v in diffs.values())
        and all(v == 0 for v in containment.values()),
    }

    # 手順 7：実機の照合用の選択（見込みありの D だけ）
    live_questions = load_questions(paths.questions, LIVE_QUESTION_LIMIT)
    row_of = {q: i for i, q in enumerate(sets["test"].qids)}
    live_rows = np.asarray([row_of[q.qid] for q in live_questions], dtype=np.int64)
    live_first, live_second = first["test"][live_rows], second["test"][live_rows]
    live_gold = [sets["test"].gold[i] for i in live_rows]
    selection: dict[str, dict[str, list[str]]] = {}
    live_hit: dict[str, float] = {}
    for c in choices:
        live_d: int | None = c["chosen_d"]
        if live_d is None or not c["promising"] or str(live_d) in selection:
            continue
        cascaded = masked_scores(
            live_second, device_candidate_mask(live_first, device_of_inbox, live_d, "max")
        )
        selection[str(live_d)] = dict(
            zip([q.qid for q in live_questions], selected_sources(cascaded, names, m), strict=True)
        )
        live_hit[str(live_d)] = hit_at(ranks_of_gold(cascaded, live_gold), m)

    curve = {
        s: {
            "device_probe": [
                {
                    "d": d,
                    "inboxes_probed_mean": float(
                        device_candidate_mask(first[s], device_of_inbox, d, "max")
                        .sum(axis=1)
                        .mean()
                    ),
                    **{f"{agg}_hit@{m}": hit_at(device_ranks[s][agg][d], m) for agg in AGGREGATES},
                    **(hit_stats(device_ranks[s]["max"][d], m, seed) if s == "test" else {}),
                }
                for d in d_values
            ],
            "centroid_probe": [
                {"k": k, "reach_mean": k_reach[s][k], f"hit@{m}": hit_at(k_ranks[s][k], m)}
                for k in K_CURVE
            ],
            "flood_score": {f"hit@{m}": hit_at(flood_ranks[s], m)},
        }
        for s in sets
    }
    return {
        "n_inboxes": len(names),
        "n_devices": n_dev,
        "n_dev": len(sets["dev"].qids),
        "n_test": len(sets["test"].qids),
        "m": m,
        "n_centroids": e.n_centroids,
        "rule": (
            f"for K in {list(TARGET_KS)}: smallest D in {d_values} with dev max-aggregated hit@{m} >= "
            f"dev centroid_probe(K) hit@{m}; live only if D + m <= {REQUEST_RATIO} x (dev reach(K) + m)"
        ),
        "choices": choices,
        "checks": checks,
        "curve": curve,
        "live_selected_sources": selection,
        f"live_hit@{m}": live_hit,
        "duration_s": time.perf_counter() - start,
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    checks = metrics["checks"]

    def num(value: float | None, digits: int) -> str:
        return "-" if value is None else f"{value:.{digits}f}"

    lines = [
        "# device_probe のオフラインの評価（Iteration 7）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個，デバイス {metrics['n_devices']} 台，dev {metrics['n_dev']} 問，test {metrics['n_test']} 問，m = {m}，中心の数 C = {metrics['n_centroids']}",
        f"- D の規則：{metrics['rule']}",
        f"- 正しさの確認：{'通過' if checks['passed'] else '**不通過（判定に進まない）**'}"
        f"（差の最大 {max(abs(v) for v in checks['diffs'].values()):.5f}，"
        f"包含の違反 {sum(checks['containment_violations'].values())} 問）",
        "",
        "## D の選択（dev）と test での確認",
        "",
        f"| 比べる K | dev の目標 hit@{m} | K の dev の届くデバイス | 選んだ D | 要求の数の比 | 見込みあり | test の K の hit@{m} | test の D の hit@{m} | 劣らない |",
        "|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {c['k']} | {c['dev_target_hit']:.4f} | {c['dev_reach_mean']:.3f} | {c['chosen_d']} "
            f"| {num(c['request_ratio'], 3)} | {c['promising']} "
            f"| {c['test_centroid_probe_hit']:.4f} "
            f"| {num(c['test_device_probe_hit'], 4)} "
            f"| {c['test_not_inferior']} |"
            for c in metrics["choices"]
        ),
    ]
    for split in ("dev", "test"):
        block = metrics["curve"][split]
        lines += [
            "",
            f"## {split} の曲線",
            "",
            f"| D | probe する受信箱（平均） | hit@{m}（最大値） | hit@{m}（和．参考） |",
            "|---|---|---|---|",
            *(
                f"| {r['d']} | {r['inboxes_probed_mean']:.2f} | {r[f'max_hit@{m}']:.4f} | {r[f'sum_hit@{m}']:.4f} |"
                for r in block["device_probe"]
            ),
            "",
            f"| centroid_probe の K | 届くデバイス（平均） | hit@{m} |",
            "|---|---|---|",
            *(
                f"| {r['k']} | {r['reach_mean']:.3f} | {r[f'hit@{m}']:.4f} |"
                for r in block["centroid_probe"]
            ),
            f"| flood_score | {metrics['n_devices']} | {block['flood_score'][f'hit@{m}']:.4f} |",
        ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
