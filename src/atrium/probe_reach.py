"""centroid_probe の実機化（Iteration 4）の事前確認：K ごとに，1 問で probe が届くデバイスの数と，上位 K の再現率をオフラインで出す．

`atrium probe-reach` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（規則は journal の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. 1 段目の採点（multi_centroid．中心の数は data.enronqa.n_centroids）と，2 段目の採点（flood_score の密ベクトル）を全受信箱で求める．
2. 質問の集合は 2 つ：T4 と同じ実機の質問（questions.jsonl の受信箱ごとの先頭 LIVE_QUESTION_LIMIT 問）と，test の全問．
3. K ごとに，1 段目の上位 K 個の受信箱を持つデバイスの数（届くデバイスの数）の平均・中央値・最小・最大・度数，デバイスあたりに probe するシャードの数，
   上位 K の再現率（正解の受信箱が 1 段目の上位 K に入る割合），2 段目まで通した hit@m を出す．
4. 実機の照合用に，実機の質問の K = KS_FOR_SELECTION での選ばれる受信箱（上位 m 個）を質問ごとに出す．
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.advert_eval import (
    Inbox,
    hit_at,
    load_inboxes,
    load_query_set,
    ranks_of_gold,
    score_flood_dense,
    score_multi,
)
from atrium.arrays import F32Array, I64Array
from atrium.benchmarks import load_questions
from atrium.cascade_eval import cascade_scores
from atrium.config import AtriumConfig
from atrium.manifest import Placement
from atrium.paths import DatasetPaths
from atrium.routing.advert import top_m

logger = logging.getLogger(__name__)

K_VALUES = (4, 8, 15, 30, 50, 75, 150)
# 実機の照合用に，選ばれる受信箱を質問ごとに出す K
KS_FOR_SELECTION = (30, 75)
# T4 と同じ実機の質問の数（questions.jsonl の受信箱ごとの先頭．150 受信箱で 300 問）
LIVE_QUESTION_LIMIT = 2


def candidates_of(first: F32Array, k: int) -> I64Array:
    """1 段目の採点の上位 k 個の受信箱の番号（行ごと．同点は番号の小さい方を先にする．cascade_scores と同じ規則）．"""
    order: I64Array = np.argsort(-first, axis=1, kind="stable")[:, :k]
    return order


def devices_reached(candidates: I64Array, node_of_inbox: I64Array) -> I64Array:
    """質問ごとに，候補の受信箱を持つデバイス（ノード）の数．"""
    return np.asarray([len(np.unique(node_of_inbox[row])) for row in candidates], dtype=np.int64)


def summarize_counts(counts: I64Array) -> dict[str, Any]:
    """届くデバイスの数の平均・中央値・最小・最大・度数．"""
    return {
        "mean": float(np.mean(counts)),
        "median": float(np.median(counts)),
        "min": int(np.min(counts)),
        "max": int(np.max(counts)),
        "histogram": {str(k): v for k, v in sorted(Counter(counts.tolist()).items())},
    }


def selected_sources(cascaded: F32Array, names: Sequence[str], m: int) -> list[list[str]]:
    """2 段目まで通した採点（候補以外は -inf）の，質問ごとの上位 m 個の受信箱の名前（実機の top_m と同じ規則）．"""
    return [top_m(names, row.tolist(), m) for row in cascaded]


def reach_for_set(
    first: F32Array,
    second: F32Array,
    gold: Sequence[int],
    node_of_inbox: I64Array,
    names: Sequence[str],
    m: int,
) -> dict[str, Any]:
    """質問の集合 1 個について，K ごとの届くデバイスの数・再現率・hit@m を出す．"""
    first_ranks = ranks_of_gold(first, gold)
    rows = []
    for k in K_VALUES:
        counts = devices_reached(candidates_of(first, k), node_of_inbox)
        casc_ranks = ranks_of_gold(cascade_scores(first, second, k), gold)
        rows.append(
            {
                "k": k,
                "devices_reached": summarize_counts(counts),
                "shards_per_device_mean": float(np.mean(k / counts)),
                "recall_at_k": hit_at(first_ranks, k),
                f"hit@{m}": hit_at(casc_ranks, m),
            }
        )
    return {"n": len(gold), "by_k": rows}


def evaluate(cfg: AtriumConfig, paths: DatasetPaths, placement: Placement) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．placement は実機の配置（実験の results/<run>/placement.json）．"""
    from atrium.data_enronqa import kmeans_centroids

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes: list[Inbox] = load_inboxes(cfg, paths)
    names = [ib.name for ib in inboxes]
    node_index = {sid: i for i, node in enumerate(placement.nodes) for sid in node.shard_ids}
    missing = [n for n in names if n not in node_index]
    if missing:
        raise ValueError(f"inboxes without a node in the placement: {missing[:5]}")
    node_of_inbox = np.asarray([node_index[n] for n in names], dtype=np.int64)
    inboxes_per_node = Counter(node_of_inbox.tolist())

    centers = [kmeans_centroids(ib.emb, e.n_centroids, seed) for ib in inboxes]
    test = load_query_set(cfg, paths, "test")
    first = score_multi(test.emb, centers)
    second = score_flood_dense(test.emb, inboxes)

    # T4 と同じ実機の質問：questions.jsonl の受信箱ごとの先頭 question_limit 問（test の部分集合）
    live_questions = load_questions(paths.questions, LIVE_QUESTION_LIMIT)
    row_of = {q: i for i, q in enumerate(test.qids)}
    live_rows = np.asarray([row_of[q.qid] for q in live_questions], dtype=np.int64)
    live_gold = [test.gold[i] for i in live_rows]

    selection = {}
    for k in KS_FOR_SELECTION:
        cascaded = cascade_scores(first[live_rows], second[live_rows], k)
        selection[str(k)] = dict(
            zip(
                [q.qid for q in live_questions],
                selected_sources(cascaded, names, m),
                strict=True,
            )
        )

    return {
        "n_inboxes": len(names),
        "n_nodes": len(placement.nodes),
        "m": m,
        "n_centroids": e.n_centroids,
        "inboxes_per_node": summarize_counts(
            np.asarray([inboxes_per_node[i] for i in range(len(placement.nodes))], dtype=np.int64)
        ),
        "live_300": reach_for_set(
            first[live_rows], second[live_rows], live_gold, node_of_inbox, names, m
        ),
        "test": reach_for_set(first, second, test.gold, node_of_inbox, names, m),
        "selected_sources": selection,
        "duration_s": time.perf_counter() - start,
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    ipn = metrics["inboxes_per_node"]
    lines = [
        "# centroid_probe の届くデバイスの数と再現率（Iteration 4．オフライン）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個，ノード {metrics['n_nodes']} 台，m = {m}，中心の数 C = {metrics['n_centroids']}",
        f"- デバイスあたりの受信箱の数：平均 {ipn['mean']:.2f}，最小 {ipn['min']}，最大 {ipn['max']}，度数 {ipn['histogram']}",
    ]
    for key, title in (("live_300", "実機と同じ質問"), ("test", "test の全問")):
        block = metrics[key]
        lines += [
            "",
            f"## {title}（n = {block['n']}）",
            "",
            f"| K | 届くデバイス 平均 | 中央値 | 最小 | 最大 | デバイスあたりのシャード（平均） | 上位 K の再現率 | hit@{m} |",
            "|---|---|---|---|---|---|---|---|",
            *(
                f"| {r['k']} | {r['devices_reached']['mean']:.3f} | {r['devices_reached']['median']:.1f} "
                f"| {r['devices_reached']['min']} | {r['devices_reached']['max']} "
                f"| {r['shards_per_device_mean']:.3f} | {r['recall_at_k']:.4f} | {r[f'hit@{m}']:.4f} |"
                for r in block["by_k"]
            ),
        ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
