"""質問者：ベンチマークの質問を専門家ノードへ問い合わせ，1 問 1 行の結果を記録する．

1 問の処理は次の順に進む．

1. クエリ埋め込み（live: その場で計算／cached: 中継点で事前計算したもの）
2. ルーティング（atrium.routing）で問い合わせるデータ源を決める
3. answer_mode に応じて，
   - retrieval_only / snippet_return: 選んだデータ源の全シャードへ /v1/retrieve を送り，統合する．
     snippet_return のみ，統合した断片で質問者の LLM が回答する．
   - local_answer: 選んだシャードを持つノードへ /v1/answer を送り，回答を多数決で統合する．
4. 計測値（各段の所要時間・受信バイト数・デバイス外へ出た断片数）とともに results.jsonl へ追記する．
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np

from atrium import llm, prompts
from atrium.arrays import F32Array
from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.manifest import Placement
from atrium.merge import (
    NodeVote,
    SourcedDoc,
    contributing_sources,
    merge_by_qrels,
    merge_by_score,
    top_per_source,
    vote,
)
from atrium.protocol import (
    AnswerRequest,
    AnswerResponse,
    ProfileResponse,
    RetrieveRequest,
    RetrieveResponse,
    ShardProfile,
)
from atrium.routing import Router, RoutingQuery, SourceProfile
from atrium.store import RetrievedDoc

logger = logging.getLogger(__name__)

# ノードへの 1 回の問い合わせの打ち切り時間（秒）．宿る型は CPU での生成を含むので長めにする
RETRIEVE_TIMEOUT_S = 120.0


class QueryEmbedder(Protocol):
    """質問 → 検索器名ごとのクエリ埋め込み．"""

    def embed(self, question: Question) -> dict[str, F32Array]:
        """クエリ埋め込みを返す．"""
        ...


class Reranker(Protocol):
    """断片の再ランク（merge=cross_encoder）．"""

    def rerank(self, query: str, docs: Sequence[SourcedDoc], k: int) -> list[SourcedDoc]:
        """上位 k 件を返す．"""
        ...


class CachedQueryEmbedder:
    """中継点で事前計算したクエリ埋め込みを引く．"""

    def __init__(self, ids_path: Path, matrices: dict[str, Path]) -> None:
        """query_ids.json と，検索器名 → 埋め込み行列（.npy）を読み込む．"""
        ids: list[str] = json.loads(ids_path.read_text(encoding="utf-8"))
        self._row = {qid: i for i, qid in enumerate(ids)}
        self._matrices = {name: np.load(path, mmap_mode="r") for name, path in matrices.items()}

    def embed(self, question: Question) -> dict[str, F32Array]:
        """全検索器のクエリ埋め込みを返す．"""
        row = self._row[question.qid]
        return {name: np.asarray(m[row], dtype=np.float32) for name, m in self._matrices.items()}


class LiveMedcptEmbedder:
    """MedCPT のクエリ側モデルでその場で埋め込む（埋め込みにかかる時間も計測対象になる）．"""

    def __init__(self, query_encoder: str, article_encoder: str) -> None:
        """クエリ側のモデルだけを読み込む．"""
        from atrium.encoders import MedcptEncoder

        self._encoder = MedcptEncoder(query_encoder, article_encoder, load_article=False)
        self._name = query_encoder

    def embed(self, question: Question) -> dict[str, F32Array]:
        """質問文を埋め込む．"""
        # 同時に呼ばれないことは Requester._embed_and_route のロックが保証する
        return {self._name: self._encoder.encode_queries([question.question])[0]}


class CrossEncoderReranker:
    """bge-reranker-v2-m3 による再ランク（RAGRoute の既定の再ランク）．"""

    MODEL = "BAAI/bge-reranker-v2-m3"
    # RAGRoute と同じ 4 組ずつの推論（12 GB の GPU で長い断片を扱うため）
    BATCH = 4

    def __init__(self) -> None:
        """モデルを読み込む．"""
        import torch
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(
            self.MODEL, device="cuda" if torch.cuda.is_available() else "cpu"
        )
        self._lock = threading.Lock()

    def rerank(self, query: str, docs: Sequence[SourcedDoc], k: int) -> list[SourcedDoc]:
        """クロスエンコーダのスコアで上位 k 件を返す．"""
        if not docs:
            return []
        pairs = [(query, f"{d.doc.title}\n{d.doc.content}".strip()) for d in docs]
        with self._lock:
            scores = self._model.predict(pairs, batch_size=self.BATCH, convert_to_numpy=True)
        order = np.argsort(-np.asarray(scores))[:k]
        return [docs[int(i)] for i in order]


def load_rm_qrels(path: Path) -> dict[str, dict[str, int]]:
    """FeB4RAG の結果統合用ラベル（qid Q0 docid rel）を読む．"""
    out: dict[str, dict[str, int]] = defaultdict(dict)
    with path.open(encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) == 4:
                out[parts[0]][parts[2]] = int(parts[3])
    return dict(out)


async def discover_shards(client: httpx.AsyncClient, placement: Placement) -> list[ShardProfile]:
    """配置にある全ノードの /v1/profile を集める．"""

    async def fetch(url: str) -> ProfileResponse:
        response = await client.get(f"{url}/v1/profile", timeout=RETRIEVE_TIMEOUT_S)
        response.raise_for_status()
        return ProfileResponse.model_validate_json(response.content)

    results = await asyncio.gather(*(fetch(node.url) for node in placement.nodes))
    return [shard for r in results for shard in r.shards]


@dataclass
class Requester:
    """1 回の実験の質問者．"""

    cfg: AtriumConfig
    placement: Placement
    sources: list[SourceProfile]
    router: Router
    embedder: QueryEmbedder
    client: httpx.AsyncClient
    ollama_url: str
    reranker: Reranker | None = None
    rm_qrels: dict[str, dict[str, int]] = field(default_factory=dict)
    _embed_lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        """シャード → ノード URL の表を作る．"""
        self._url_of_shard = self.placement.url_of_shard()

    def _profile(self, source: str) -> SourceProfile:
        return next(s for s in self.sources if s.source == source)

    async def _retrieve(
        self, source: SourceProfile, shard_id: str, embedding: F32Array, question: Question
    ) -> tuple[list[SourcedDoc], dict[str, Any]]:
        req = RetrieveRequest(
            shard_id=shard_id,
            k=self.cfg.retrieval.k_ret,
            embedding=embedding.tolist(),
            query_id=question.source_qid,
        )
        start = time.perf_counter()
        response = await self.client.post(
            f"{self._url_of_shard[shard_id]}/v1/retrieve",
            json=req.model_dump(),
            timeout=RETRIEVE_TIMEOUT_S,
        )
        response.raise_for_status()
        body = RetrieveResponse.model_validate_json(response.content)
        stats = {
            "shard_id": shard_id,
            "source": source.source,
            "bytes": len(response.content),
            "n_docs": len(body.docs),
            "node_s": body.duration_s,
            "rtt_s": time.perf_counter() - start,
        }
        return [SourcedDoc(source.source, shard_id, d) for d in body.docs], stats

    def _merge(self, question: Question, docs: list[SourcedDoc]) -> list[SourcedDoc]:
        k = self.cfg.retrieval.k_rerank
        merge = self.cfg.retrieval.merge
        if merge == "score":
            return merge_by_score(docs, k)
        if merge == "qrels_oracle":
            return merge_by_qrels(docs, self.rm_qrels.get(question.source_qid, {}), k)
        if self.reranker is None:
            raise RuntimeError("merge=cross_encoder requires a reranker")
        return self.reranker.rerank(question.question, docs, k)

    async def _snippets(
        self,
        question: Question,
        selected: list[str],
        embeddings: dict[str, F32Array],
        record: dict[str, Any],
    ) -> list[SourcedDoc]:
        tasks = []
        for source_name in selected:
            source = self._profile(source_name)
            for shard_id in source.shard_ids:
                tasks.append(self._retrieve(source, shard_id, embeddings[source.encoder], question))
        start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        record["timings"]["retrieve_s"] = time.perf_counter() - start
        record["shard_stats"] = [stats for _, stats in results]
        received = [d for docs, _ in results for d in docs]
        record["bytes_received"] = sum(s["bytes"] for _, s in results)
        record["snippets_exposed"] = len(received)
        start = time.perf_counter()
        merged = await asyncio.to_thread(
            self._merge, question, top_per_source(received, self.cfg.retrieval.k_ret)
        )
        record["timings"]["merge_s"] = time.perf_counter() - start
        record["contributing_sources"] = contributing_sources(merged)
        record["top_doc_ids"] = [d.doc.doc_id for d in merged]
        return merged

    async def _local_answers(
        self,
        question: Question,
        selected: list[str],
        embeddings: dict[str, F32Array],
        record: dict[str, Any],
    ) -> str | None:
        shards_of_url: dict[str, list[str]] = defaultdict(list)
        encoder_of_url: dict[str, str] = {}
        for source_name in selected:
            source = self._profile(source_name)
            for shard_id in source.shard_ids:
                url = self._url_of_shard[shard_id]
                shards_of_url[url].append(shard_id)
                encoder_of_url.setdefault(url, source.encoder)

        async def ask(url: str) -> tuple[AnswerResponse, int]:
            req = AnswerRequest(
                dataset=self.cfg.experiment.dataset,
                question=question.question,
                options=question.options,
                shard_ids=shards_of_url[url],
                k=self.cfg.local_answer.k_context,
                embedding=embeddings[encoder_of_url[url]].tolist(),
                query_id=question.source_qid,
            )
            response = await self.client.post(
                f"{url}/v1/answer", json=req.model_dump(), timeout=self.cfg.llm.timeout_s
            )
            response.raise_for_status()
            return AnswerResponse.model_validate_json(response.content), len(response.content)

        start = time.perf_counter()
        answers = await asyncio.gather(*(ask(url) for url in shards_of_url))
        record["timings"]["generate_s"] = time.perf_counter() - start
        record["bytes_received"] = sum(size for _, size in answers)
        record["snippets_exposed"] = 0
        record["node_answers"] = [
            {
                "node_id": a.node_id,
                "choice": a.choice,
                "top_score": a.top_score,
                "n_context_docs": a.n_context_docs,
                "retrieve_s": a.retrieve_s,
                "duration_s": a.duration_s,
                **a.llm.model_dump(),
            }
            for a, _ in answers
        ]
        record["answer"] = "\n\n".join(f"[{a.node_id}] {a.answer}" for a, _ in answers)
        return vote([NodeVote(a.node_id, a.choice, a.top_score) for a, _ in answers])

    async def _generate(
        self, question: Question, docs: list[SourcedDoc], record: dict[str, Any]
    ) -> str | None:
        context = [
            RetrievedDoc(d.doc.doc_id, d.doc.title, d.doc.content, d.doc.score) for d in docs
        ]
        messages = prompts.build_messages(
            self.cfg.experiment.dataset, question.question, context, question.options
        )
        start = time.perf_counter()
        result = await llm.chat(
            self.client, self.ollama_url, self.cfg.llm.requester_model, messages, self.cfg.llm
        )
        record["timings"]["generate_s"] = time.perf_counter() - start
        record["answer"] = result.content
        record["prompt_tokens"] = result.prompt_tokens
        record["output_tokens"] = result.output_tokens
        record["llm"] = {"prefill_s": result.prefill_s, "decode_s": result.decode_s}
        return prompts.extract_choice(result.content)

    def _embed_and_route(
        self, question: Question, record: dict[str, Any]
    ) -> tuple[list[str], dict[str, F32Array]]:
        start = time.perf_counter()
        with self._embed_lock:
            embeddings = self.embedder.embed(question)
        record["timings"]["embed_s"] = time.perf_counter() - start
        start = time.perf_counter()
        selected = self.router.select(
            RoutingQuery(question.qid, question.question, embeddings), self.sources
        )
        record["timings"]["route_s"] = time.perf_counter() - start
        return selected, embeddings

    async def process(self, question: Question) -> dict[str, Any]:
        """1 問を処理して結果の 1 行を返す（失敗は error に記録し，例外は投げない）．"""
        mode = self.cfg.experiment.answer_mode
        record: dict[str, Any] = {
            "qid": question.qid,
            "bank": question.bank,
            "gold": question.answer,
            "routing": self.router.name,
            "answer_mode": mode,
            "timings": {},
            "bytes_received": 0,
            "snippets_exposed": 0,
            "error": None,
        }
        start = time.perf_counter()
        try:
            selected, embeddings = await asyncio.to_thread(self._embed_and_route, question, record)
            record["selected_sources"] = selected
            record["n_shards_queried"] = sum(len(self._profile(s).shard_ids) for s in selected)
            choice: str | None = None
            if mode == "local_answer":
                choice = (
                    await self._local_answers(question, selected, embeddings, record)
                    if selected
                    else None
                )
            else:
                docs = await self._snippets(question, selected, embeddings, record)
                if mode == "snippet_return":
                    choice = await self._generate(question, docs, record)
            record["choice"] = choice
            if question.answer is not None and mode != "retrieval_only":
                record["correct"] = choice == question.answer
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.error("question %s failed: %s", question.qid, exc)
            record["error"] = f"{type(exc).__name__}: {exc}"
        record["timings"]["e2e_s"] = time.perf_counter() - start
        return record


async def run_questions(
    requester: Requester, questions: Sequence[Question], out_path: Path, parallel: int
) -> int:
    """質問を並列に処理し，終わった順に out_path へ追記する．失敗した問数を返す．"""
    semaphore = asyncio.Semaphore(parallel)
    lock = asyncio.Lock()
    failures = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as out:

        async def one(q: Question) -> None:
            nonlocal failures
            async with semaphore:
                record = await requester.process(q)
            async with lock:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                failures += record["error"] is not None

        await asyncio.gather(*(one(q) for q in questions))
    return failures
