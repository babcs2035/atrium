"""専門家ノードの HTTP サーバー．

1 台のノードは 1 個以上のシャードを持ち，次の方法で問い合わせに応じる．

- 断片返却型（/v1/retrieve）: 検索した断片をそのまま返す．
- 宿る型（/v1/answer）: 断片を自分の LLM（同じホストの Ollama）に渡し，回答文だけを返す．
- flood_score の 1 段目（/v1/probe）: 各シャードの最高の検索スコアだけを返す（本文は返さない．p0004）．

コンテナ内では `atrium node` で起動する．ノード名・シャード・Ollama の URL・宿る型のモデルは，
deploy が config.yaml から作る compose.yml のコマンドの引数で渡す（環境変数は使わない）．
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from atrium import llm, prompts
from atrium.config import AtriumConfig, DatasetName, load_config
from atrium.protocol import (
    AnswerRequest,
    AnswerResponse,
    DocOut,
    GenerateRequest,
    GenerateResponse,
    LlmUsage,
    ProbeRequest,
    ProbeResponse,
    Problem,
    ProfileResponse,
    RetrieveRequest,
    RetrieveResponse,
    ShardProfile,
)
from atrium.store import RetrievedDoc, ShardStore, load_shard

logger = logging.getLogger(__name__)

AGENT_CARD_PATH = "/.well-known/agent-card.json"
A2A_PROTOCOL_VERSION = "0.3.0"


class NodeError(Exception):
    """外部へ problem details として返す例外．"""

    def __init__(self, status: int, title: str, detail: str | None = None) -> None:
        """HTTP status と，外部へ見せてよい説明を持つ．"""
        super().__init__(title)
        self.problem = Problem(status=status, title=title, detail=detail)


def build_agent_card(
    node_id: str, base_url: str, stores: Mapping[str, ShardStore]
) -> dict[str, object]:
    """A2A の Agent Card を作る．シャード 1 個を 1 個のスキルとして名乗る．"""
    skills = [
        {
            "id": store.spec.shard_id,
            "name": store.spec.source,
            "description": store.spec.description,
            "tags": [store.spec.source, store.spec.kind],
        }
        for store in stores.values()
    ]
    sources = sorted({store.spec.source for store in stores.values()})
    return {
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "name": f"atrium-expert-{node_id}",
        "description": "Expert node holding: " + ", ".join(sources),
        "url": base_url,
        "preferredTransport": "HTTP+JSON",
        "version": "0.1.0",
        "capabilities": {"streaming": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": skills,
    }


def _merge_top(docs: list[RetrievedDoc], k: int) -> list[RetrievedDoc]:
    return sorted(docs, key=lambda d: d.score, reverse=True)[:k]


def create_app(
    node_id: str,
    stores: Mapping[str, ShardStore],
    cfg: AtriumConfig,
    ollama_url: str,
    llm_client: httpx.AsyncClient | None = None,
    expert_model: str | None = None,
) -> FastAPI:
    """ノードの FastAPI アプリを作る（テストでは llm_client に MockTransport を渡す）．

    expert_model は宿る型で使う LLM（GPU を持つ専門家は llm.expert_model_gpu）．省略すると llm.expert_model．
    """
    client = llm_client or httpx.AsyncClient()
    model = expert_model or cfg.llm.expert_model

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if llm_client is None:
            await client.aclose()

    app = FastAPI(title=f"atrium expert {node_id}", lifespan=lifespan)

    @app.exception_handler(NodeError)
    async def _node_error(_: Request, exc: NodeError) -> JSONResponse:
        return JSONResponse(
            exc.problem.model_dump(),
            status_code=exc.problem.status,
            media_type="application/problem+json",
        )

    def store_of(shard_id: str) -> ShardStore:
        if shard_id not in stores:
            raise NodeError(404, "Unknown shard", f"this node does not hold shard {shard_id!r}")
        return stores[shard_id]

    def search(
        store: ShardStore, k: int, embedding: list[float] | None, query_id: str | None
    ) -> list[RetrievedDoc]:
        try:
            return store.search(k=k, embedding=embedding, query_id=query_id)
        except (ValueError, KeyError) as exc:
            # 入力の不整合（次元違い・未知の要求 ID）は呼び出し元の誤りとして 400 で返す
            raise NodeError(400, "Invalid retrieval request", str(exc)) from exc

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"status": "ok", "node_id": node_id, "shards": sorted(stores)}

    @app.get(AGENT_CARD_PATH)
    def agent_card(request: Request) -> dict[str, object]:
        return build_agent_card(node_id, str(request.base_url).rstrip("/"), stores)

    @app.get("/v1/profile")
    def profile() -> ProfileResponse:
        return ProfileResponse(
            node_id=node_id,
            shards=[
                ShardProfile(
                    shard_id=s.spec.shard_id,
                    source=s.spec.source,
                    kind=s.spec.kind,
                    n_docs=s.spec.n_docs,
                    dim=s.spec.dim,
                    encoder=s.spec.encoder,
                    centroid=s.spec.centroid,
                    description=s.spec.description,
                    advert=s.spec.advert,
                )
                for s in stores.values()
            ],
        )

    # FAISS の検索は CPU を使う同期処理なので，def のまま FastAPI のスレッドプールで動かす
    @app.post("/v1/retrieve")
    def retrieve(req: RetrieveRequest) -> RetrieveResponse:
        start = time.perf_counter()
        docs = search(store_of(req.shard_id), req.k, req.embedding, req.query_id)
        return RetrieveResponse(
            shard_id=req.shard_id,
            docs=[
                DocOut(doc_id=d.doc_id, title=d.title, content=d.content, score=d.score)
                for d in docs
            ],
            duration_s=time.perf_counter() - start,
        )

    @app.post("/v1/probe")
    def probe(req: ProbeRequest) -> ProbeResponse:
        start = time.perf_counter()
        scores: dict[str, float] = {}
        for shard_id in req.shard_ids:
            docs = search(store_of(shard_id), 1, req.embedding, None)
            scores[shard_id] = docs[0].score if docs else float("-inf")
        return ProbeResponse(node_id=node_id, scores=scores, duration_s=time.perf_counter() - start)

    @app.post("/v1/answer")
    async def answer(req: AnswerRequest) -> AnswerResponse:
        start = time.perf_counter()
        docs: list[RetrievedDoc] = []
        for shard_id in req.shard_ids:
            # FAISS の検索とディスクの読み出しは同期処理なので，イベントループを塞がないようスレッドで動かす
            docs.extend(
                await asyncio.to_thread(
                    search, store_of(shard_id), req.k, req.embedding, req.query_id
                )
            )
        context = _merge_top(docs, req.k)
        retrieve_s = time.perf_counter() - start
        if req.delegate_url is not None:
            generated = await delegate(req, context)
            content, usage, delegated_to = generated.answer, generated.llm, generated.node_id
        else:
            content, usage = await generate(req.dataset, req.question, context, req.options)
            delegated_to = None
        return AnswerResponse(
            node_id=node_id,
            answer=content,
            choice=prompts.extract_choice(content),
            n_context_docs=len(context),
            top_score=context[0].score if context else None,
            retrieve_s=retrieve_s,
            llm=usage,
            duration_s=time.perf_counter() - start,
            delegated_to=delegated_to,
            docs_sent=len(context) if delegated_to is not None else 0,
        )

    async def generate(
        dataset: DatasetName,
        question: str,
        context: list[RetrievedDoc],
        options: dict[str, str],
    ) -> tuple[str, LlmUsage]:
        messages = prompts.build_messages(dataset, question, context, options)
        try:
            result = await llm.chat(client, ollama_url, model, messages, cfg.llm)
        except httpx.HTTPError as exc:
            logger.error("local LLM call failed: %s", exc)
            raise NodeError(502, "Local LLM unavailable") from exc
        usage = LlmUsage(
            prompt_tokens=result.prompt_tokens,
            output_tokens=result.output_tokens,
            prefill_s=result.prefill_s,
            decode_s=result.decode_s,
            total_s=result.total_s,
        )
        return result.content, usage

    async def delegate(req: AnswerRequest, context: list[RetrievedDoc]) -> GenerateResponse:
        body = GenerateRequest(
            dataset=req.dataset,
            question=req.question,
            options=req.options,
            docs=[
                DocOut(doc_id=d.doc_id, title=d.title, content=d.content, score=d.score)
                for d in context
            ],
        )
        try:
            response = await client.post(
                f"{req.delegate_url}/v1/generate",
                json=body.model_dump(),
                timeout=cfg.local_answer.timeout_s,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("delegated generation failed: %s", exc)
            raise NodeError(502, "Delegate unavailable") from exc
        return GenerateResponse.model_validate_json(response.content)

    @app.post("/v1/generate")
    async def generate_for_peer(req: GenerateRequest) -> GenerateResponse:
        context = [RetrievedDoc(d.doc_id, d.title, d.content, d.score) for d in req.docs]
        content, usage = await generate(req.dataset, req.question, context, req.options)
        return GenerateResponse(node_id=node_id, answer=content, llm=usage)

    return app


def main(
    config_path: Path,
    host: str,
    port: int,
    node_id: str,
    shards_dir: Path,
    shard_ids: list[str],
    ollama_url: str,
    expert_model: str | None = None,
) -> None:
    """指定されたシャードを読み込み，ノードを起動する（値は compose の command の引数で渡される）．"""
    import uvicorn

    cfg = load_config(config_path)
    stores = {sid: load_shard(shards_dir / sid) for sid in shard_ids}
    app = create_app(node_id, stores, cfg, ollama_url, expert_model=expert_model)
    uvicorn.run(app, host=host, port=port)
