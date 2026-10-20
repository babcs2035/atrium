"""term_probe・hybrid_probe・bm25_probe の要約の成果物と，オフラインの選択を作る（p0006 P4・p0007 A4）．

`atrium build-summary` が制御点のコンテナで実行する．atrium.frontier_eval の入力（本文の埋め込みは work_dir のキャッシュ）から，
配置ごとの要約（語の統計の CORI 重みと本文の重心）を `<data_dir>/<dataset>/summary/` へ書く（既存の成果物とは別の新しいサブディレクトリ）．
bm25_probe（p0007 A4）のために，各デバイスが公開済みの文書頻度の合計・文書の数・長さの平均（`bm25_stats.npz`）も同じ場所へ書く
（個々のデバイスの値は含まない．既存のファイルの中身は変わらない）．
あわせて，同じ関数（P2 の frontier_methods）で求めた，test の質問ごとの「選ばれるデータ源」を results に書く
（term_probe・hybrid_probe は m = routing.top_m．bm25_probe は m = routing.top_m の `bm25_probe` と m = 3 の `bm25_probe_m3`．
bm25_probe の得点そのものは `bm25_probe_fused.npz`）．
実機の質問者（atrium.routing.summary）の選択と一致することを retrieval_only の実行で確かめる（EQ7b と同じ形）．
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from atrium.config import AtriumConfig
from atrium.device_probe_eval import device_index_of_inbox
from atrium.frontier_eval import EnronqaBase
from atrium.frontier_methods import (
    BM25_FIRST_STAGE,
    FUSE_BM25_WEIGHTS,
    bm25_index,
    bm25_scores,
    cori_scores,
    cori_weights,
    device_first_stage,
    source_centers,
    term_index,
    top_devices,
    zscore_masked,
    zscore_rows,
)
from atrium.manifest import Placement
from atrium.paths import DatasetPaths
from atrium.routing.advert import top_m
from atrium.routing.summary import Bm25Stats, write_summary

logger = logging.getLogger(__name__)

# 作る要約の語の数（P2 反復 2 の term_stats_large・hybrid_summary と同じ）
TERM_SIZE = 64000
# bm25_probe の A4 の事前登録の値（m = answer.owner_m = 3．`bm25_probe_m3` の選択に使う）
BM25_PROBE_OWNER_M = 3


def build(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    placement: Placement,
    work_dir: Path | None,
    out_dir: Path,
    results_dir: Path,
) -> dict[str, Any]:
    """要約の成果物を out_dir に，オフラインの選択を results_dir に書き，その要約（件数・大きさ）を返す．"""
    base = EnronqaBase(cfg, paths, work_dir)
    device_of_source = device_index_of_inbox(base.names, placement)
    data = base.data(device_of_source, "n30")
    weights, cf = cori_weights(data, TERM_SIZE)
    vocab = term_index(data)["vocab"]
    centers = source_centers(data, "body")
    groups = [
        [n for n, d in zip(base.names, device_of_source.tolist(), strict=True) if d == dev]
        for dev in range(data.n_devices)
    ]
    # bm25_probe の IDF の元：各デバイスが公開した語の文書頻度の合計（cori_weights が data.cache に置く）．
    # 全体の文書の数と長さの平均は，全デバイスの n_docs と語数の合計から求まる値
    if BM25_FIRST_STAGE[0] != TERM_SIZE:
        raise ValueError("TERM_SIZE must equal frontier_methods.BM25_FIRST_STAGE[0]")
    bm25_idx = bm25_index(data)
    bm25_stats = Bm25Stats(
        published_df=np.asarray(data.cache[f"published_df_{TERM_SIZE}"].sum(axis=0)).ravel(),
        n_docs=int(bm25_idx["n_docs"]),
        avg_doc_len=float(bm25_idx["avg_doc_len"]),
    )
    write_summary(out_dir, base.names, groups, vocab, weights, cf, centers, TERM_SIZE, bm25_stats)

    # オフラインの選択（P2 の関数）：質問ごとの上位 D 台に probe し，probe の最高スコアの上位 m 個
    terms = cori_scores(vocab, weights, cf, data.q_tokens["test"])
    hybrid = zscore_rows(terms) + cfg.routing.summary_weight * zscore_rows(
        device_first_stage(data, "test", "body")
    )
    selections: dict[str, dict[str, list[str]]] = {}
    qids = base.sets["test"].qids
    for name, dscores, depth in (
        ("term_probe", terms, cfg.routing.term_probe_d["enronqa"]),
        ("hybrid_probe", hybrid, cfg.routing.hybrid_probe_d["enronqa"]),
    ):
        mask = top_devices(dscores, depth, device_of_source)
        masked = np.where(mask, data.second["test"], -np.inf)
        selections[name] = {
            qid: top_m(base.names, row.tolist(), data.m)
            for qid, row in zip(qids, masked, strict=True)
        }

    # bm25_probe（A3 の bm25_fuse・published．重み wb = routing.bm25_weight．1 段目の D は routing.bm25_probe_d）
    bm25_weight = cfg.routing.bm25_weight
    if bm25_weight not in FUSE_BM25_WEIGHTS:
        raise ValueError(
            f"bm25_weight {bm25_weight} is not in A3's offline grid {FUSE_BM25_WEIGHTS}"
        )
    mask = top_devices(hybrid, cfg.routing.bm25_probe_d["enronqa"], device_of_source)
    fused = zscore_masked(data.second["test"], mask) + bm25_weight * zscore_masked(
        bm25_scores(data, "test", "published"), mask
    )
    fused_masked = np.where(mask, fused, -np.inf)
    for key, m in (("bm25_probe", data.m), ("bm25_probe_m3", BM25_PROBE_OWNER_M)):
        selections[key] = {
            qid: top_m(base.names, row.tolist(), m)
            for qid, row in zip(qids, fused_masked, strict=True)
        }
    results_dir.mkdir(parents=True, exist_ok=True)
    # 同点を許す照合（実機の選択が，得点の同じ別の受信箱を選んだだけかを確かめる）に使う
    np.savez_compressed(
        results_dir / "bm25_probe_fused.npz",
        qids=np.asarray(qids),
        names=np.asarray(base.names),
        fused=fused_masked.astype(np.float32),
    )
    (results_dir / "offline_selection.json").write_text(
        json.dumps(selections, ensure_ascii=False), encoding="utf-8"
    )
    summary = {
        "n_sources": len(base.names),
        "n_devices": data.n_devices,
        "vocab_size": len(vocab),
        "term_size": TERM_SIZE,
        "term_probe_d": cfg.routing.term_probe_d["enronqa"],
        "hybrid_probe_d": cfg.routing.hybrid_probe_d["enronqa"],
        "summary_weight": cfg.routing.summary_weight,
        "bm25_probe_d": cfg.routing.bm25_probe_d["enronqa"],
        "bm25_weight": bm25_weight,
        "bm25_n_docs": bm25_stats.n_docs,
        "bm25_avg_doc_len": bm25_stats.avg_doc_len,
    }
    (results_dir / "build_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary
