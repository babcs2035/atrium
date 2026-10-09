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
from collections import Counter, defaultdict
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np

from atrium import llm, prompts
from atrium.arrays import F32Array
from atrium.benchmarks import Question
from atrium.config import AtriumConfig, Precision
from atrium.manifest import NodeAssignment, Placement
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
    ProbeRequest,
    ProbeResponse,
    ProfileResponse,
    RetrieveRequest,
    RetrieveResponse,
    ShardProfile,
)
from atrium.routing import Router, RoutingQuery, SourceProfile
from atrium.routing.advert import tokenize
from atrium.store import RetrievedDoc

logger = logging.getLogger(__name__)

# ノードへの 1 回の問い合わせの打ち切り時間（秒）．宿る型は CPU での生成を含むので長めにする
RETRIEVE_TIMEOUT_S = 120.0
# 検索要求の接続エラー（ノードが待機中の接続を閉じた直後に，クライアントがその接続を再利用すると起きる）の再試行回数．
# 検索は読み取りだけなので，同じ要求を送り直しても結果は変わらない
RETRIEVE_RETRIES = 2


async def post_with_retry(
    client: httpx.AsyncClient, url: str, body: dict[str, Any], timeout: float
) -> httpx.Response:
    """読み取りだけの要求（検索・probe）を送る．ノードが待機中の接続を閉じた直後に再利用して起きる
    接続エラーは，同じ要求を RETRIEVE_RETRIES 回まで送り直す．"""
    for attempt in range(RETRIEVE_RETRIES + 1):
        try:
            return await client.post(url, json=body, timeout=timeout)
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError):
            if attempt == RETRIEVE_RETRIES:
                raise
            logger.warning("retrying %s (%d/%d)", url, attempt + 1, RETRIEVE_RETRIES)
    raise AssertionError("unreachable")


async def gather_or_cancel[T](coros: Sequence[Coroutine[Any, Any, T]]) -> list[T]:
    """全て成功すれば結果を順に返す．1 つでも失敗すれば残りを取り消し，例外（ExceptionGroup）を投げる．

    asyncio.gather は失敗しても他の要求を走らせ続け，ノードに負荷が残って後の質問の計測をゆがめるため．
    """
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(c) for c in coros]
    return [t.result() for t in tasks]


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

    def __init__(self, query_encoder: str, article_encoder: str, precision: Precision) -> None:
        """クエリ側のモデルだけを読み込む（精度は中継点で事前計算したクエリ埋め込みとそろえる）．"""
        from atrium.encoders import MedcptEncoder

        self._encoder = MedcptEncoder(
            query_encoder, article_encoder, load_article=False, precision=precision
        )
        self._name = query_encoder

    def embed(self, question: Question) -> dict[str, F32Array]:
        """質問文を埋め込む．"""
        # 同時に呼ばれないことは Requester._embed のロックが保証する
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

    def _k_ret(self) -> int:
        """データ源から取る件数（EnronQA は原論文と同じ k_context．retrieval.k_ret は MedRAG・FeB4RAG のまま）．"""
        if self.cfg.experiment.dataset == "enronqa":
            return self.cfg.data.require_enronqa().k_context
        return self.cfg.retrieval.k_ret

    def _k_rerank(self) -> int:
        """統合後に LLM へ渡す件数．"""
        if self.cfg.experiment.dataset == "enronqa":
            return self.cfg.data.require_enronqa().k_context
        return self.cfg.retrieval.k_rerank

    def _k_local(self) -> int:
        """宿る型・委託で専門家が使う件数．"""
        if self.cfg.experiment.dataset == "enronqa":
            return self.cfg.data.require_enronqa().k_context
        return self.cfg.local_answer.k_context

    async def _retrieve(
        self, source: SourceProfile, shard_id: str, embedding: F32Array, question: Question
    ) -> tuple[list[SourcedDoc], dict[str, Any]]:
        # 配布済みの検索結果を返すシャードは埋め込みを使わないので送らない（FeB4RAG では最大 4,096 次元になる）
        req = RetrieveRequest(
            shard_id=shard_id,
            k=self._k_ret(),
            embedding=embedding.tolist() if source.kind == "faiss" else None,
            query_id=question.source_qid,
        )
        start = time.perf_counter()
        url = f"{self._url_of_shard[shard_id]}/v1/retrieve"
        response = await post_with_retry(self.client, url, req.model_dump(), RETRIEVE_TIMEOUT_S)
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
        k = self._k_rerank()
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
        results = await gather_or_cancel(tasks)
        record["timings"]["retrieve_s"] = time.perf_counter() - start
        record["shard_stats"] = [stats for _, stats in results]
        received = [d for docs, _ in results for d in docs]
        record["bytes_received"] = sum(s["bytes"] for _, s in results)
        record["snippets_exposed"] = len(received)
        # p0004 §8.4：持ち主のデバイスの外に出た本文の数（データ源ごと）と，クエリを受け取ったデバイスの数
        record["docs_exposed_by_source"] = dict(Counter(d.source for d in received))
        record["query_recipients"] = len({self._url_of_shard[s["shard_id"]] for _, s in results})
        start = time.perf_counter()
        merged = await asyncio.to_thread(
            self._merge, question, top_per_source(received, self._k_ret())
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
        sources_of_url: dict[str, list[str]] = defaultdict(list)
        encoder_of_url: dict[str, str] = {}
        for source_name in selected:
            source = self._profile(source_name)
            for shard_id in source.shard_ids:
                url = self._url_of_shard[shard_id]
                shards_of_url[url].append(shard_id)
                sources_of_url[url].append(source_name)
                encoder_of_url.setdefault(url, source.encoder)
        # B4（delegate_gpu）：GPU の無い専門家は，対応づけた GPU の専門家に回答を委託する
        delegate_url_of: dict[str, str] = {}
        if self.cfg.experiment.answer_mode == "delegate_gpu":
            url_of_host = {n.host: n.url for n in self.placement.nodes}
            host_of_url = {n.url: n.host for n in self.placement.nodes}
            for url in shards_of_url:
                gpu_host = self.placement.delegate_of.get(host_of_url[url])
                if gpu_host is not None:
                    delegate_url_of[url] = url_of_host[gpu_host]

        async def ask(url: str) -> tuple[AnswerResponse, int]:
            req = AnswerRequest(
                dataset=self.cfg.experiment.dataset,
                question=question.question,
                options=question.options,
                shard_ids=shards_of_url[url],
                k=self._k_local(),
                embedding=embeddings[encoder_of_url[url]].tolist(),
                query_id=question.source_qid,
                delegate_url=delegate_url_of.get(url),
            )
            response = await self.client.post(
                f"{url}/v1/answer", json=req.model_dump(), timeout=self.cfg.local_answer.timeout_s
            )
            response.raise_for_status()
            return AnswerResponse.model_validate_json(response.content), len(response.content)

        start = time.perf_counter()
        answers = await gather_or_cancel([ask(url) for url in shards_of_url])
        record["timings"]["generate_s"] = time.perf_counter() - start
        record["bytes_received"] = sum(size for _, size in answers)
        record["snippets_exposed"] = 0
        urls = list(shards_of_url)
        record["docs_exposed_by_source"] = {
            "+".join(sorted(set(sources_of_url[url]))): a.docs_sent
            for url, (a, _) in zip(urls, answers, strict=True)
            if a.docs_sent
        }
        record["query_recipients"] = len(urls) + len(set(delegate_url_of.values()) - set(urls))
        record["node_answers"] = [
            {
                "node_id": a.node_id,
                "choice": a.choice,
                "top_score": a.top_score,
                "n_context_docs": a.n_context_docs,
                "retrieve_s": a.retrieve_s,
                "duration_s": a.duration_s,
                "delegated_to": a.delegated_to,
                **a.llm.model_dump(),
            }
            for a, _ in answers
        ]
        record["answer"] = "\n\n".join(f"[{a.node_id}] {a.answer}" for a, _ in answers)
        if self.cfg.experiment.dataset == "enronqa":
            # 自由記述の回答は多数決できないので，最高の検索スコアのノードの回答を採る
            best, _ = max(answers, key=lambda x: x[0].top_score or float("-inf"))
            record["final_answer"] = prompts.extract_free_answer(best.answer)
            return None
        return vote([NodeVote(a.node_id, a.choice, a.top_score) for a, _ in answers])

    async def _generate(
        self, question: Question, docs: list[SourcedDoc], record: dict[str, Any], model: str
    ) -> str | None:
        context = [
            RetrievedDoc(d.doc.doc_id, d.doc.title, d.doc.content, d.doc.score) for d in docs
        ]
        messages = prompts.build_messages(
            self.cfg.experiment.dataset, question.question, context, question.options
        )
        start = time.perf_counter()
        result = await llm.chat(self.client, self.ollama_url, model, messages, self.cfg.llm)
        record["timings"]["generate_s"] = time.perf_counter() - start
        record["answer"] = result.content
        record["prompt_tokens"] = result.prompt_tokens
        record["output_tokens"] = result.output_tokens
        record["llm"] = {"prefill_s": result.prefill_s, "decode_s": result.decode_s}
        if self.cfg.experiment.dataset == "enronqa":
            record["final_answer"] = prompts.extract_free_answer(result.content)
            return None
        return prompts.extract_choice(result.content)

    def _embed(self, question: Question, record: dict[str, Any]) -> dict[str, F32Array]:
        start = time.perf_counter()
        with self._embed_lock:
            embeddings = self.embedder.embed(question)
        record["timings"]["embed_s"] = time.perf_counter() - start
        return embeddings

    async def _probe(
        self, embeddings: dict[str, F32Array], record: dict[str, Any]
    ) -> dict[str, float]:
        """flood_score の 1 段目：全ノードへクエリ埋め込みを送り，データ源ごとの最高の検索スコアを集める．"""
        source_of_shard = {sid: s for s in self.sources for sid in s.shard_ids}

        async def ask(node: NodeAssignment) -> tuple[ProbeResponse, int]:
            shard_ids = [sid for sid in node.shard_ids if sid in source_of_shard]
            encoder = source_of_shard[shard_ids[0]].encoder
            req = ProbeRequest(shard_ids=shard_ids, embedding=embeddings[encoder].tolist())
            response = await post_with_retry(
                self.client, f"{node.url}/v1/probe", req.model_dump(), RETRIEVE_TIMEOUT_S
            )
            response.raise_for_status()
            return ProbeResponse.model_validate_json(response.content), len(response.content)

        nodes = [n for n in self.placement.nodes if any(s in source_of_shard for s in n.shard_ids)]
        start = time.perf_counter()
        results = await gather_or_cancel([ask(n) for n in nodes])
        record["timings"]["probe_s"] = time.perf_counter() - start
        record["probe_recipients"] = len(nodes)
        record["probe_bytes"] = sum(size for _, size in results)
        best: dict[str, float] = {}
        for resp, _ in results:
            for sid, score in resp.scores.items():
                name = source_of_shard[sid].source
                best[name] = max(best.get(name, float("-inf")), score)
        return best

    def _route(
        self,
        question: Question,
        embeddings: dict[str, F32Array],
        probe_scores: dict[str, float] | None,
        record: dict[str, Any],
    ) -> list[str]:
        start = time.perf_counter()
        query = RoutingQuery(
            question.qid,
            question.question,
            embeddings,
            tokens=tuple(tokenize(question.question)),
            probe_scores=probe_scores,
        )
        selected = self.router.select(query, self.sources)
        record["timings"]["route_s"] = time.perf_counter() - start
        return selected

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
            embeddings = await asyncio.to_thread(self._embed, question, record)
            probe_scores = (
                await self._probe(embeddings, record)
                if getattr(self.router, "needs_probe", False)
                else None
            )
            selected = self._route(question, embeddings, probe_scores, record)
            record["selected_sources"] = selected
            record["n_shards_queried"] = sum(len(self._profile(s).shard_ids) for s in selected)
            choice: str | None = None
            if mode in ("local_answer", "delegate_gpu"):
                choice = (
                    await self._local_answers(question, selected, embeddings, record)
                    if selected
                    else None
                )
            else:
                docs = await self._snippets(question, selected, embeddings, record)
                if mode == "snippet_return":
                    choice = await self._generate(
                        question, docs, record, self.cfg.llm.requester_model
                    )
                elif mode == "snippet_return_small":
                    # B3（p0004）：質問者が，専門家（CPU）と同じ小型のモデルで答える
                    choice = await self._generate(question, docs, record, self.cfg.llm.expert_model)
            if "probe_recipients" in record:
                # flood_score の 1 段目は全ノードにクエリを送るので，受け取ったデバイスはその数になる
                record["query_recipients"] = max(
                    record.get("query_recipients", 0), record["probe_recipients"]
                )
            record["choice"] = choice
            # EnronQA の自由記述の回答は，実験の後に判定モデル（atrium judge）で採点する
            graded_here = self.cfg.experiment.dataset != "enronqa"
            if question.answer is not None and mode != "retrieval_only" and graded_here:
                record["correct"] = choice == question.answer
        except Exception as exc:  # noqa: BLE001 (1 問の失敗で実験全体を止めず，記録して次へ進む)
            # gather_or_cancel の ExceptionGroup は，原因となった最初の例外を記録する
            cause: BaseException = exc
            while isinstance(cause, BaseExceptionGroup) and cause.exceptions:
                cause = cause.exceptions[0]
            logger.exception("question %s failed", question.qid)
            record["error"] = f"{type(cause).__name__}: {cause}"
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
