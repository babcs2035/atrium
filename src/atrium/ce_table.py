"""中央の再ランク（C1-RRF-CE）の cross-encoder の得点を事前に計算して表にする（`atrium ce-table`．d0009 A3）．

質問者に GPU が無いとき（制御点 wafl-ctrl5 の GPU の廃止．運用ルール 8）に，実機の質問者が得点の表を引けるようにする
（config.yaml の `retrieval.ce_scores: table`．`atrium.requester.TableReranker`）．表は GPU のある端末（gpu4）で作る．

- 質問ごとに，質問者が実機でやるのと同じ手順で上位 `retrieval.k_ret` 通を取る（中央の索引・全体の BM25 と密ベクトルの RRF・
  質問の埋め込みは事前に計算したもの）．この上位 50 通を `CrossEncoderReranker.scores`（bge-reranker-v2-m3・同じ revision・batch 4）で採点する．
- 表は `<data_dir>/enronqa/ce_table/<split>.jsonl`（1 行 1 組：qid・query・doc_id・score）．
- `--compare-run` を付けると，表から求めた上位 `k_context` 通が，その実行（例：`results/p0007_a5_c1_rrf_ce`）の `top_doc_ids` と
  順序も含めて一致する割合を出す（d0009 A3 のゲート G1）．

使い方：
    uv run atrium ce-table --data-dir <enronqa/ を含む置き場所> --split holdout
    uv run atrium ce-table --data-dir <同> --split test --out-dir results/d0009_a3_rerun_variance/g1 \
        --compare-run results/p0007_a5_c1_rrf_ce
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from atrium.benchmarks import load_questions
from atrium.config import AtriumConfig
from atrium.data_enronqa import QUESTION_FILES
from atrium.paths import DatasetPaths
from atrium.requester import CachedQueryEmbedder, CrossEncoderReranker
from atrium.routing.central import CentralIndex

logger = logging.getLogger(__name__)

# 進み具合のログを出す質問の間隔
LOG_EVERY = 100


def split_query_embedder(cfg: AtriumConfig, paths: DatasetPaths, split: str) -> CachedQueryEmbedder:
    """分割のクエリ埋め込み（test は queries/，holdout は queries/holdout/）を引く埋め込み器．"""
    encoder = cfg.data.require_enronqa().encoder
    if split == "holdout":
        return CachedQueryEmbedder(
            paths.holdout_query_ids, {encoder: paths.holdout_query_embeddings(encoder)}
        )
    return CachedQueryEmbedder(paths.query_ids, {encoder: paths.query_embeddings(encoder)})


def build_ce_table(
    cfg: AtriumConfig, paths: DatasetPaths, split: str, limit: int | None = None
) -> dict[str, list[str]]:
    """分割の全質問の中央の上位 k_ret 通を採点して表を書く．質問 ID → 表から求めた上位 k_context 通の文書 ID を返す．"""
    if cfg.retrieval.fusion != "rrf" or cfg.retrieval.merge != "cross_encoder":
        raise ValueError("ce-table requires retrieval.fusion=rrf and retrieval.merge=cross_encoder")
    e = cfg.data.require_enronqa()
    questions = load_questions(paths.questions.parent / QUESTION_FILES[split], limit)
    embedder = split_query_embedder(cfg, paths, split)
    index = CentralIndex.from_corpus(cfg, paths)
    reranker = CrossEncoderReranker()
    out = paths.ce_table(split)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    top: dict[str, list[str]] = {}
    start = time.perf_counter()
    with tmp.open("w", encoding="utf-8") as f:
        for n, q in enumerate(questions, 1):
            found = index.search(embedder.embed(q)[e.encoder], cfg.retrieval.k_ret, q.question)
            scores = reranker.scores(q.question, [f"{d.title}\n{d.content}".strip() for d in found])
            for d, score in zip(found, scores, strict=True):
                f.write(
                    json.dumps(
                        {"qid": q.qid, "query": q.question, "doc_id": d.doc_id, "score": score},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            # CrossEncoderReranker.rerank と同じ並べ方（得点の降順の np.argsort）
            order = np.argsort(-np.asarray(scores))[: e.k_context]
            top[q.qid] = [found[int(i)].doc_id for i in order]
            if n % LOG_EVERY == 0:
                logger.info(
                    "scored %d / %d questions (%.0f s)",
                    n,
                    len(questions),
                    time.perf_counter() - start,
                )
    tmp.replace(out)
    return top


def compare_top_doc_ids(top: dict[str, list[str]], run_dir: Path) -> dict[str, Any]:
    """表から求めた上位の文書 ID と，実行の top_doc_ids の一致（順序も含む・集合）の割合（ゲート G1）．"""
    from atrium.analysis import read_results

    rows = [r for r in read_results(run_dir / "results.jsonl") if r.get("error") is None]
    shared = [r for r in rows if r["qid"] in top]
    same_order = sum(top[r["qid"]] == r["top_doc_ids"] for r in shared)
    same_set = sum(set(top[r["qid"]]) == set(r["top_doc_ids"]) for r in shared)
    mismatched = [r["qid"] for r in shared if top[r["qid"]] != r["top_doc_ids"]]
    n = len(shared)
    return {
        "run": run_dir.name,
        "n_compared": n,
        "n_missing_in_table": len(rows) - n,
        "order_match": same_order / n if n else None,
        "set_match": same_set / n if n else None,
        "mismatched_qids": mismatched,
    }
