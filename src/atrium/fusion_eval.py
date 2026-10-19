"""A2 のオフラインの評価（p0007 §6.2・I3）：受信箱の中と中央の，密ベクトル・BM25・RRF の Recall@K と，回答まで流すゲートの判定．

`atrium fusion-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

- 受信箱の中の Recall@K：質問の正解メールが，**持ち主の受信箱の中**の上位 K 通に入る割合（受信箱の選択は含めない）．
  BM25 はその受信箱だけの統計（ノードの `retrieval.fusion: rrf` と同じ）．
- 中央の Recall@K：全メールの中の上位 K 通に正解が入る割合．BM25 は全体で統計を共有（中央の `central_local` と同じ）．
- ゲート（事前登録．journal の「p0007 A2（事前登録）」）：dev の受信箱の中の Recall@K で，RRF − 密ベクトルのみ ≥ 0.02 なら回答まで流す．
  verdict.json に書く（test の値は確認として併記し，判断には使わない）．
- 整合の確認：中央の密ベクトル・BM25 の Recall@K が p0006 P1（`--reference` の metrics.json）と一致すること，
  `atrium.hybrid.Bm25Index` の上位 K 通が既存の `central_eval.bm25_top_k` と一致する割合．
- 出力は集計値だけ（メールの本文は含まない）．
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from atrium.advert_eval import Inbox, QuerySet, load_inboxes, load_query_set
from atrium.arrays import F64Array
from atrium.central_eval import (
    QUERY_CHUNK,
    bm25_top_k,
    summarize_recall,
)
from atrium.config import AtriumConfig
from atrium.hybrid import Bm25Index, ranked_indices, rrf_top_k
from atrium.paths import DatasetPaths
from atrium.routing.advert import tokenize
from atrium.verdict import make_verdict, paired_bootstrap_ci, write_verdict

logger = logging.getLogger(__name__)

# 事前登録のゲート：dev の受信箱の中の Recall@K で，RRF が密ベクトルのみより 0.02 以上高いときだけ回答まで流す
GATE_MARGIN = 0.02
# p0006 P1 の値との一致の許容（Recall の小数 4 桁の丸めの幅）
REFERENCE_TOLERANCE = 5e-4
METHODS = ("dense", "bm25", "rrf")


def _method_flags(
    dense: F64Array, bm25: F64Array, gold_idx: int, k: int, depth: int
) -> dict[str, float]:
    """1 問について，3 方式の上位 k 通に正解の行が入るか（1.0 / 0.0）．"""
    top = {
        "dense": ranked_indices(dense, k).tolist(),
        "bm25": ranked_indices(bm25, k).tolist(),
        "rrf": rrf_top_k(dense, bm25, k, depth),
    }
    return {name: float(gold_idx in rows) for name, rows in top.items()}


def in_inbox_flags(
    inboxes: list[Inbox], queries: QuerySet, gold_paths: list[str], k: int, depth: int
) -> dict[str, F64Array]:
    """受信箱の中の Recall の旗（方式 → 質問ごとの 1.0 / 0.0）．受信箱ごとに，その受信箱だけの統計の BM25 を作る．"""
    bm25_of = [Bm25Index(tokenize(t) for t in ib.texts) for ib in inboxes]
    row_of = [{p: i for i, p in enumerate(ib.paths)} for ib in inboxes]
    flags: dict[str, list[float]] = {m: [] for m in METHODS}
    for qi, (owner, path) in enumerate(zip(queries.gold, gold_paths, strict=True)):
        dense = (inboxes[owner].emb @ queries.emb[qi]).astype(np.float64)
        bm25 = bm25_of[owner].scores(tokenize(queries.texts[qi]))
        for name, flag in _method_flags(dense, bm25, row_of[owner][path], k, depth).items():
            flags[name].append(flag)
    return {m: np.array(v) for m, v in flags.items()}


def central_flags(
    inboxes: list[Inbox], queries: QuerySet, gold_paths: list[str], k: int, depth: int
) -> tuple[dict[str, F64Array], float]:
    """全メールの中の Recall の旗と，Bm25Index の上位 k 通が既存の bm25_top_k と一致する問の割合．"""
    doc_ids = [p for ib in inboxes for p in ib.paths]
    emb = np.concatenate([ib.emb for ib in inboxes])
    row_of = {p: i for i, p in enumerate(doc_ids)}
    index = Bm25Index(tokenize(t) for ib in inboxes for t in ib.texts)
    flags: dict[str, list[float]] = {m: [] for m in METHODS}
    own_top: list[set[int]] = []
    for start in range(0, len(queries.qids), QUERY_CHUNK):
        block = queries.emb[start : start + QUERY_CHUNK] @ emb.T
        for offset, row in enumerate(block):
            qi = start + offset
            bm25 = index.scores(tokenize(queries.texts[qi]))
            for name, flag in _method_flags(
                row.astype(np.float64), bm25, row_of[gold_paths[qi]], k, depth
            ).items():
                flags[name].append(flag)
            own_top.append(set(ranked_indices(bm25, k).tolist()))
    texts = (t for ib in inboxes for t in ib.texts)
    existing = bm25_top_k([tokenize(x) for x in queries.texts], [tokenize(t) for t in texts], k)
    same = np.mean([own_top[i] == set(row) for i, row in enumerate(existing.tolist())])
    return {m: np.array(v) for m, v in flags.items()}, float(same)


def summarize(flags: dict[str, F64Array]) -> dict[str, Any]:
    """方式ごとの Recall（95% 区間）と，RRF − 密ベクトルのみ・RRF − BM25 のみの差（対応ありの 95% 区間）．"""
    out: dict[str, Any] = {m: summarize_recall(flags[m]) for m in METHODS}
    for other in ("dense", "bm25"):
        diff = flags["rrf"] - flags[other]
        lo, hi = paired_bootstrap_ci(diff)
        out[f"rrf_minus_{other}"] = {"value": float(diff.mean()), "ci95": [lo, hi]}
    return out


def evaluate(
    cfg: AtriumConfig, paths: DatasetPaths, reference: Path, out_dir: Path
) -> dict[str, Any]:
    """dev・test の受信箱の中と中央の Recall を出し，ゲートを判定して out_dir に書く．"""
    from atrium.data_enronqa import read_gold

    k = cfg.data.require_enronqa().k_context
    depth = cfg.retrieval.fusion_depth
    inboxes = load_inboxes(cfg, paths)
    gold = read_gold(paths)
    metrics: dict[str, Any] = {
        "k": k,
        "fusion_depth": depth,
        "n_emails": sum(len(i.paths) for i in inboxes),
    }
    for split in ("dev", "test"):
        queries = load_query_set(cfg, paths, split)
        gold_paths = [gold[q]["path"] for q in queries.qids]
        start = time.perf_counter()
        inbox_part = summarize(in_inbox_flags(inboxes, queries, gold_paths, k, depth))
        central_part_flags, same = central_flags(inboxes, queries, gold_paths, k, depth)
        logger.info(
            "%s: %d questions in %.0f s", split, len(queries.qids), time.perf_counter() - start
        )
        metrics[split] = {
            "n": len(queries.qids),
            "in_inbox": inbox_part,
            "central": summarize(central_part_flags),
            "bm25_top_k_agreement_with_existing": same,
        }
    ref = json.loads((reference / "metrics.json").read_text(encoding="utf-8"))
    ref_dense = ref["central_dense"][f"recall@{k}"]["value"]
    ref_bm25 = ref["central_bm25"][f"recall@{k}"]["value"]
    test_central = metrics["test"]["central"]
    metrics["reference_p0006_p1"] = {
        "dense": ref_dense,
        "bm25": ref_bm25,
        "dense_abs_diff": abs(test_central["dense"]["value"] - ref_dense),
        "bm25_abs_diff": abs(test_central["bm25"]["value"] - ref_bm25),
        "tolerance": REFERENCE_TOLERANCE,
    }
    metrics["reference_p0006_p1"]["consistent"] = (
        metrics["reference_p0006_p1"]["dense_abs_diff"] <= REFERENCE_TOLERANCE
        and metrics["reference_p0006_p1"]["bm25_abs_diff"] <= REFERENCE_TOLERANCE
    )
    dev_diff = metrics["dev"]["in_inbox"]["rrf_minus_dense"]["value"]
    verdict = {
        "hypothesis": f"dev の受信箱の中の Recall@{k} で，RRF が密ベクトルのみより {GATE_MARGIN} 以上高い（回答まで流すゲート）",
        "threshold": GATE_MARGIN,
        "dev_in_inbox_dense": metrics["dev"]["in_inbox"]["dense"]["value"],
        "dev_in_inbox_rrf": metrics["dev"]["in_inbox"]["rrf"]["value"],
        "dev_diff": dev_diff,
        "test_diff_confirmation_only": metrics["test"]["in_inbox"]["rrf_minus_dense"]["value"],
        "gate_passed": dev_diff >= GATE_MARGIN,
    }
    metrics["verdict"] = make_verdict(verdict)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    write_verdict(out_dir, verdict)
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
    return metrics


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文（集計値だけ）．"""
    k = metrics["k"]

    def cell(r: dict[str, Any]) -> str:
        lo, hi = r["ci95"]
        return f"{r['value']:.4f} [{lo:.4f}, {hi:.4f}]"

    lines = [
        "# A2 のオフラインの評価：受信箱の中と中央の Recall（p0007 I3）",
        "",
        f"メール {metrics['n_emails']:,} 通．K = {k}．RRF の候補は，密ベクトル・BM25 それぞれの上位 {metrics['fusion_depth']} 通．",
        "",
    ]
    for scope, label in (
        ("in_inbox", "受信箱の中（持ち主の受信箱の中の上位 K 通）"),
        ("central", "中央（全メールの上位 K 通）"),
    ):
        lines += [
            f"## {label}",
            "",
            "| 分割 | 問 | 密ベクトル | BM25 | RRF | RRF − 密ベクトル | RRF − BM25 |",
            "|---|---|---|---|---|---|---|",
        ]
        for split in ("dev", "test"):
            part = metrics[split][scope]
            lines.append(
                f"| {split} | {metrics[split]['n']} | {cell(part['dense'])} | {cell(part['bm25'])} | {cell(part['rrf'])} | "
                f"{cell(part['rrf_minus_dense'])} | {cell(part['rrf_minus_bm25'])} |"
            )
        lines.append("")
    ref = metrics["reference_p0006_p1"]
    verdict = metrics["verdict"]["verdict_original"]
    lines += [
        "## 整合の確認",
        "",
        f"- p0006 P1 の中央（test）：密ベクトル {ref['dense']:.4f}・BM25 {ref['bm25']:.4f}．今回との差 {ref['dense_abs_diff']:.5f}・{ref['bm25_abs_diff']:.5f}"
        f"（許容 {ref['tolerance']}）→ {'一致' if ref['consistent'] else '不一致'}",
        f"- Bm25Index の上位 K 通が既存の bm25_top_k と一致する問の割合：dev {metrics['dev']['bm25_top_k_agreement_with_existing']:.4f}・"
        f"test {metrics['test']['bm25_top_k_agreement_with_existing']:.4f}",
        "",
        "## ゲート",
        "",
        f"dev の受信箱の中の Recall@{k}：密ベクトル {verdict['dev_in_inbox_dense']:.4f}，RRF {verdict['dev_in_inbox_rrf']:.4f}，"
        f"差 {verdict['dev_diff']:+.4f}（閾値 {verdict['threshold']}）→ **{'通過' if verdict['gate_passed'] else '不通過'}**"
        f"（test の差 {verdict['test_diff_confirmation_only']:+.4f}．確認のみ）",
        "",
    ]
    return "\n".join(lines)
