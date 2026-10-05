"""1 回の実験（E1 以降）の組み立て．質問者のコンテナの中で `atrium run` から呼ばれる．"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from atrium.benchmarks import load_questions
from atrium.config import AtriumConfig
from atrium.manifest import read_placement
from atrium.paths import dataset_paths
from atrium.requester import (
    CachedQueryEmbedder,
    CrossEncoderReranker,
    LiveMedcptEmbedder,
    QueryEmbedder,
    Requester,
    discover_shards,
    load_rm_qrels,
    run_questions,
)
from atrium.routing import SourceProfile, build_source_profiles, make_router

logger = logging.getLogger(__name__)

RUN_META = "run_meta.json"
RESULTS = "results.jsonl"
# 50 台規模へ同時に問い合わせても接続数で詰まらないようにする
MAX_CONNECTIONS = 512


def _make_embedder(
    cfg: AtriumConfig, data_dir: Path, sources: list[SourceProfile]
) -> QueryEmbedder:
    dataset = cfg.experiment.dataset
    paths = dataset_paths(data_dir, dataset)
    if cfg.retrieval.query_embedding[dataset] == "live":
        if dataset != "medrag":
            raise ValueError("live query embedding is only supported for medrag")
        return LiveMedcptEmbedder(cfg.data.medrag.query_encoder, cfg.data.medrag.article_encoder)
    encoders = sorted({s.encoder for s in sources})
    return CachedQueryEmbedder(paths.query_ids, {e: paths.query_embeddings(e) for e in encoders})


def _write_meta(out_dir: Path, meta: dict[str, Any]) -> None:
    (out_dir / RUN_META).write_text(
        json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8"
    )


async def run_experiment(
    cfg: AtriumConfig, data_dir: Path, placement_path: Path, out_dir: Path, ollama_url: str
) -> int:
    """全質問を処理し，results.jsonl と run_meta.json を書く．失敗した問数を返す．"""
    dataset = cfg.experiment.dataset
    paths = dataset_paths(data_dir, dataset)
    placement = read_placement(placement_path)
    questions = load_questions(paths.questions, cfg.experiment.question_limit)
    out_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=MAX_CONNECTIONS)) as client:
        start = time.perf_counter()
        shards = await discover_shards(client, placement)
        discovery_s = time.perf_counter() - start
        expected = cfg.data.sources_of(dataset)
        sources = build_source_profiles(shards, expected)
        missing = sorted(set(expected) - {s.source for s in sources})
        if missing:
            raise RuntimeError(f"no node announced sources {missing}; check deploy")
        requester = Requester(
            cfg=cfg,
            placement=placement,
            sources=sources,
            router=make_router(cfg, paths.router),
            embedder=_make_embedder(cfg, data_dir, sources),
            client=client,
            ollama_url=ollama_url,
            reranker=CrossEncoderReranker() if cfg.retrieval.merge == "cross_encoder" else None,
            rm_qrels=load_rm_qrels(paths.rm_qrels) if cfg.retrieval.merge == "qrels_oracle" else {},
        )
        meta: dict[str, Any] = {
            "run_id": out_dir.name,
            "git_head": os.environ.get("ATRIUM_GIT_HEAD", "unknown"),
            "dataset": dataset,
            "routing": cfg.experiment.routing,
            "answer_mode": cfg.experiment.answer_mode,
            "merge": cfg.retrieval.merge,
            "n_questions": len(questions),
            "n_nodes": len(placement.nodes),
            "discovery_s": discovery_s,
            "sources": [
                {"source": s.source, "n_docs": s.n_docs, "shards": list(s.shard_ids)}
                for s in sources
            ],
            "config": cfg.model_dump(mode="json"),
            "started_at": time.time(),
        }
        _write_meta(out_dir, meta)
        failures = await run_questions(
            requester, questions, out_dir / RESULTS, cfg.experiment.parallel
        )
        meta.update({"finished_at": time.time(), "failures": failures})
        _write_meta(out_dir, meta)
    logger.info("finished %d questions (%d failures)", len(questions), failures)
    return failures
