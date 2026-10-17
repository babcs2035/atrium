"""term_probe・hybrid_probe の要約の成果物と，オフラインの選択を作る（p0006 P4）．

`atrium build-summary` が制御点のコンテナで実行する．atrium.frontier_eval の入力（本文の埋め込みは work_dir のキャッシュ）から，
配置ごとの要約（語の統計の CORI 重みと本文の重心）を `<data_dir>/<dataset>/summary/` へ書く（既存の成果物とは別の新しいサブディレクトリ）．
あわせて，同じ関数（P2 の frontier_methods）で求めた，test の質問ごとの「選ばれるデータ源」を results に書く．
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
    cori_scores,
    cori_weights,
    device_first_stage,
    source_centers,
    term_index,
    top_devices,
    zscore_rows,
)
from atrium.manifest import Placement
from atrium.paths import DatasetPaths
from atrium.routing.advert import top_m
from atrium.routing.summary import write_summary

logger = logging.getLogger(__name__)

# 作る要約の語の数（P2 反復 2 の term_stats_large・hybrid_summary と同じ）
TERM_SIZE = 64000


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
    write_summary(out_dir, base.names, groups, vocab, weights, cf, centers, TERM_SIZE)

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
    results_dir.mkdir(parents=True, exist_ok=True)
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
    }
    (results_dir / "build_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary
