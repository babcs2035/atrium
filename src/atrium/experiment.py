"""1 回の実験（E1 以降）の組み立て．質問者のコンテナの中で `atrium run` から呼ばれる．"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from atrium.analysis import read_results
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
# イメージの build 時にコミットを書き込むファイル（Dockerfile の GIT_HEAD．start.sh も同じファイルを読む）
IMAGE_GIT_HEAD_FILE = Path("/etc/atrium-git-head")


def read_image_git_head() -> str:
    """このイメージを build したときの git のコミット（イメージの外で動かしているときは unknown）．"""
    if not IMAGE_GIT_HEAD_FILE.exists():
        return "unknown"
    return IMAGE_GIT_HEAD_FILE.read_text(encoding="utf-8").strip()


def _make_embedder(
    cfg: AtriumConfig, data_dir: Path, sources: list[SourceProfile]
) -> QueryEmbedder:
    dataset = cfg.experiment.dataset
    paths = dataset_paths(data_dir, dataset)
    if cfg.retrieval.query_embedding[dataset] == "live":
        if dataset != "medrag":
            raise ValueError("live query embedding is only supported for medrag")
        m = cfg.data.medrag
        return LiveMedcptEmbedder(m.query_encoder, m.article_encoder, m.embed_precision)
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
    all_questions = load_questions(paths.questions, cfg.experiment.question_limit)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 同じ run_id で再開したときは，成功済みの質問を飛ばす（失敗した質問はやり直す）
    done = (
        {r["qid"] for r in read_results(out_dir / RESULTS) if r.get("error") is None}
        if (out_dir / RESULTS).exists()
        else set()
    )
    questions = [q for q in all_questions if q.qid not in done]
    previous: dict[str, Any] = (
        json.loads((out_dir / RUN_META).read_text(encoding="utf-8"))
        if (out_dir / RUN_META).exists()
        else {}
    )
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
            "git_head": read_image_git_head(),
            "dataset": dataset,
            "routing": cfg.experiment.routing,
            "answer_mode": cfg.experiment.answer_mode,
            "merge": cfg.retrieval.merge,
            "n_questions": len(all_questions),
            "n_skipped_on_resume": len(done),
            "n_nodes": len(placement.nodes),
            "discovery_s": discovery_s,
            "sources": [
                {"source": s.source, "n_docs": s.n_docs, "shards": list(s.shard_ids)}
                for s in sources
            ],
            "config": cfg.model_dump(mode="json"),
            "started_at": previous.get("started_at", time.time()),
        }
        _write_meta(out_dir, meta)
        await run_questions(requester, questions, out_dir / RESULTS, cfg.experiment.parallel)
        # 再開した場合も含め，各質問の最後の結果で失敗を数える
        failures = sum(r.get("error") is not None for r in read_results(out_dir / RESULTS))
        meta.update({"finished_at": time.time(), "failures": failures})
        _write_meta(out_dir, meta)
    logger.info("finished %d questions (%d failures)", len(questions), failures)
    return failures
