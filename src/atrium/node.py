"""専門家ノードの HTTP サーバー．

1 台のノードは 1 個以上のシャードを持ち，次の方法で問い合わせに応じる．

- 断片返却型（/v1/retrieve）: 検索した断片をそのまま返す．
- 宿る型（/v1/answer）: 断片を自分の LLM（同じホストの Ollama）に渡し，回答文だけを返す．
- flood_score の 1 段目（/v1/probe）: 各シャードの最高の検索スコアだけを返す（本文は返さない．p0004）．routing=bm25_probe（p0007 A4）では，
  要求の `bm25`（質問の語ごとの IDF・長さの平均）で採点した，受信箱の中の本文の BM25 の最大も返す．
- 参加・離脱の再生（/v1/admin/availability．EQ7b）: experiment.churn を指定した実験でだけ有効になり，離脱中は
  /healthz 以外の要求を 503 で拒否する．
- 管理 API（p0007 I5．cluster.node_admin の実験でだけ有効）: メールの非表示（/v1/admin/docs/hide）・追加（/v1/admin/docs/add．
  ノードがその場で埋め込みを作り索引に足す）・取り出し（/v1/admin/docs/export）・埋め込みモデルの読み込み（/v1/admin/embedder/load）・
  公開情報（hybrid_probe の要約）の作成（/v1/admin/summary．p0007 I7．デバイスの CPU で自分のメールだけから作る）．
  参加・離脱の切り替え（/v1/admin/availability）も node_admin で有効になる．
- 生存確認（/healthz）: プロセスの CPU 時間の累計（Iteration 8）とデバイス全体の CPU 時間の累計（Iteration 9）も返す
  （質問者・負荷試験が専門家の側の CPU 使用率を測る）．

コンテナ内では `atrium node` で起動する．ノード名・シャード・Ollama の URL・宿る型のモデルは，
deploy が config.yaml から作る compose.yml のコマンドの引数で渡す（環境変数は使わない）．
experiment.node_workers が 2 以上なら，同じポートを SO_REUSEPORT で開く W 個のプロセスで動かす（Iteration 10）．
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import multiprocessing.connection as mp_connection
import os
import signal
import socket
import threading
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from fastapi import Depends, FastAPI, Request, params
from fastapi.responses import JSONResponse

from atrium import llm, prompts
from atrium.arrays import F32Array
from atrium.config import AtriumConfig, DatasetName, load_config
from atrium.cpu_stat import read_cgroup_cpu_s, read_device_cpu
from atrium.protocol import (
    AddDocsRequest,
    AddDocsResponse,
    AnswerRequest,
    AnswerResponse,
    AvailabilityRequest,
    DocOut,
    EmbedderLoadResponse,
    ExportDocsRequest,
    ExportDocsResponse,
    GenerateRequest,
    GenerateResponse,
    HideDocsRequest,
    HideDocsResponse,
    InboxCenters,
    LlmUsage,
    NewMail,
    ProbeRequest,
    ProbeResponse,
    Problem,
    ProfileResponse,
    RetrieveRequest,
    RetrieveResponse,
    ShardProfile,
    SummaryRequest,
    SummaryResponse,
)
from atrium.store import FaissShardStore, MailDoc, RetrievedDoc, ShardStore, load_shard

logger = logging.getLogger(__name__)

AGENT_CARD_PATH = "/.well-known/agent-card.json"
A2A_PROTOCOL_VERSION = "0.3.0"
# メールの追加（p0007 I5）の埋め込みは，デバイスの種類によらず CPU で行う（GPU の無い専門家と同じ条件で，既存機材の費用を測るため）
EMBED_DEVICE = "cpu"
# 埋め込みの初期化を測定の前に済ませるための，ならし運転の文
WARM_UP_TEXT = "warm up"


class DocEmbedder(Protocol):
    """メールの本文を埋め込む（EnronQA は atrium.encoders.ArcticEncoder．コーパスの埋め込みと同じ入力・同じモデル）．"""

    name: str

    def encode_texts(self, texts: Sequence[str]) -> F32Array:
        """本文ごとの埋め込み（正規化済み．行が本文）を返す．"""
        ...


def load_cpu_arctic_embedder(cfg: AtriumConfig) -> DocEmbedder:
    """data.enronqa.encoder を CPU で読み込む（torch が無い atrium-node のイメージでは 501 で断る）．"""
    try:
        from atrium.encoders import ArcticEncoder
    except ImportError as exc:
        raise NodeError(
            501,
            "Embedder unavailable",
            "this node image has no torch; deploy with cluster.node_admin: true (atrium-node-embed)",
        ) from exc
    e = cfg.data.require_enronqa()
    return ArcticEncoder(e.encoder, e.embed_batch_size, device=EMBED_DEVICE)


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


def register_admin_docs_api(
    app: FastAPI,
    node_id: str,
    stores: Mapping[str, ShardStore],
    cfg: AtriumConfig,
    embedder: DocEmbedder | None,
    dependencies: list[params.Depends],
) -> None:
    """p0007 I5 の管理 API（メールの非表示・追加・取り出し・埋め込みモデルの読み込み）を app に足す．

    メールの追加は，ノードの CPU で本文を埋め込み（コーパスの埋め込みと同じく本文だけ），索引に足す．
    かかった時間（埋め込み・索引への追加・BM25 の作り直し）は応答に含める．
    """
    held: dict[str, DocEmbedder | None] = {"embedder": embedder}
    lock = threading.Lock()
    # 埋め込みモデルは同時に呼ばない（同じ受信箱へ重なって届いたメールの推論が干渉して，所要時間を歪めるため）
    encode_lock = threading.Lock()

    def faiss_store(shard_id: str) -> FaissShardStore:
        if shard_id not in stores:
            raise NodeError(404, "Unknown shard", f"this node does not hold shard {shard_id!r}")
        store = stores[shard_id]
        if not isinstance(store, FaissShardStore):
            raise NodeError(400, "Unsupported shard", "only faiss shards accept mail updates")
        return store

    def embedder_loaded() -> tuple[DocEmbedder, float]:
        """（埋め込み，この呼び出しで読み込みにかかった秒数．読み込み済みなら 0）．"""
        with lock:
            if held["embedder"] is not None:
                return held["embedder"], 0.0
            start = time.perf_counter()
            loaded = load_cpu_arctic_embedder(cfg)
            held["embedder"] = loaded
            return loaded, time.perf_counter() - start

    @app.post("/v1/admin/embedder/load", dependencies=dependencies)
    def load_embedder() -> EmbedderLoadResponse:
        loaded, load_s = embedder_loaded()
        # 最初の推論には，読み込みとは別の初期化の時間がかかるので，測定の前に 1 回流しておく
        warm_start = time.perf_counter()
        with encode_lock:
            loaded.encode_texts([WARM_UP_TEXT])
        return EmbedderLoadResponse(
            embedder=loaded.name,
            device=EMBED_DEVICE,
            load_s=load_s,
            warm_s=time.perf_counter() - warm_start,
        )

    @app.post("/v1/admin/docs/hide", dependencies=dependencies)
    def hide_docs(req: HideDocsRequest) -> HideDocsResponse:
        store = faiss_store(req.shard_id)
        try:
            n_hidden = store.hide(req.doc_ids)
        except KeyError as exc:
            raise NodeError(400, "Unknown documents", str(exc)) from exc
        return HideDocsResponse(shard_id=req.shard_id, n_hidden=n_hidden, n_visible=store.n_visible)

    @app.post("/v1/admin/docs/add", dependencies=dependencies)
    def add_docs(req: AddDocsRequest) -> AddDocsResponse:
        store = faiss_store(req.shard_id)
        total_start = time.perf_counter()
        loaded, _ = embedder_loaded()
        embed_start = time.perf_counter()
        # コーパスの埋め込み（data_enronqa.embed_corpus）と同じく本文だけを埋め込む
        with encode_lock:
            embeddings = loaded.encode_texts([d.content for d in req.docs])
        embed_s = time.perf_counter() - embed_start
        timing = store.add([MailDoc(d.doc_id, d.title, d.content) for d in req.docs], embeddings)
        return AddDocsResponse(
            shard_id=req.shard_id,
            n_added=len(req.docs),
            embed_s=embed_s,
            add_s=timing.add_s,
            bm25_s=timing.bm25_s,
            total_s=time.perf_counter() - total_start,
            embedder=loaded.name,
            device=EMBED_DEVICE,
            n_visible=store.n_visible,
        )

    @app.post("/v1/admin/docs/export", dependencies=dependencies)
    def export_docs(req: ExportDocsRequest) -> ExportDocsResponse:
        store = faiss_store(req.shard_id)
        start = time.perf_counter()
        try:
            docs = store.export(req.doc_ids)
        except KeyError as exc:
            raise NodeError(400, "Unknown documents", str(exc)) from exc
        return ExportDocsResponse(
            shard_id=req.shard_id,
            docs=[NewMail(doc_id=d["id"], title=d["title"], content=d["content"]) for d in docs],
            duration_s=time.perf_counter() - start,
        )

    @app.post("/v1/admin/summary", dependencies=dependencies)
    def build_summary(req: SummaryRequest) -> SummaryResponse:
        """デバイスの公開情報（hybrid_probe の要約）を，デバイスの CPU で自分のメールだけから作る（p0007 I7）．"""
        from atrium.device_summary import summarize_device

        total_start = time.perf_counter()
        loaded, _ = embedder_loaded()
        inboxes = {
            store.spec.source: [d["content"] for d in store.export(None)][: req.max_docs_per_inbox]
            for shard_id in stores
            for store in [faiss_store(shard_id)]
        }

        def embed(texts: Sequence[str]) -> F32Array:
            with encode_lock:
                return loaded.encode_texts(texts)

        summary = summarize_device(inboxes, embed, req.term_size, req.n_centroids, req.seed)
        return SummaryResponse(
            node_id=node_id,
            terms=summary.terms,
            dfs=summary.dfs,
            total_terms=summary.total_terms,
            n_docs=summary.n_docs,
            inboxes=[
                InboxCenters(inbox=inbox, centers=[[float(x) for x in row] for row in centers])
                for inbox, centers in summary.centers.items()
            ],
            tokenize_s=summary.tokenize_s,
            embed_s=summary.embed_s,
            kmeans_s=summary.kmeans_s,
            total_s=time.perf_counter() - total_start,
            embedder=loaded.name,
            device=EMBED_DEVICE,
        )

    logger.info("node %s: admin docs API enabled", node_id)


def create_app(
    node_id: str,
    stores: Mapping[str, ShardStore],
    cfg: AtriumConfig,
    ollama_url: str,
    llm_client: httpx.AsyncClient | None = None,
    expert_model: str | None = None,
    embedder: DocEmbedder | None = None,
) -> FastAPI:
    """ノードの FastAPI アプリを作る（テストでは llm_client に MockTransport を渡す）．

    expert_model は宿る型で使う LLM（GPU を持つ専門家は llm.expert_model_gpu）．省略すると llm.expert_model．
    embedder は管理 API のメールの追加で使う埋め込み（省略すると最初に必要になったとき CPU で読み込む．テストでは軽い偽物を渡す）．
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

    # EQ7b：離脱中は処理の入口で拒否する（プロセスは生きている．拒否の速さは接続の失敗に近い）
    availability = {"up": True}

    def require_up() -> None:
        if not availability["up"]:
            raise NodeError(503, "Node unavailable")

    up_only = [Depends(require_up)]

    if cfg.experiment.churn is not None or cfg.cluster.node_admin:

        @app.post("/v1/admin/availability")
        def set_availability(req: AvailabilityRequest) -> dict[str, bool]:
            availability["up"] = req.up
            return availability

    if cfg.cluster.node_admin:
        register_admin_docs_api(app, node_id, stores, cfg, embedder, up_only)

    def store_of(shard_id: str) -> ShardStore:
        if shard_id not in stores:
            raise NodeError(404, "Unknown shard", f"this node does not hold shard {shard_id!r}")
        return stores[shard_id]

    def search(
        store: ShardStore,
        k: int,
        embedding: list[float] | None,
        query_id: str | None,
        query_text: str | None,
    ) -> list[RetrievedDoc]:
        try:
            return store.search(k=k, embedding=embedding, query_id=query_id, query_text=query_text)
        except (ValueError, KeyError) as exc:
            # 入力の不整合（次元違い・未知の要求 ID）は呼び出し元の誤りとして 400 で返す
            raise NodeError(400, "Invalid retrieval request", str(exc)) from exc

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        # process_cpu_s：このプロセスの全スレッドの CPU 時間の累計（秒）．質問者が測定の区間の前後で読み，
        # 専門家の側の CPU 使用率を求める（Iteration 8）．host_cpu：デバイス全体の /proc/stat の累計（jiffies）と論理 CPU の数．
        # プロセスの外（uvicorn の外の docker・カーネルのネットワーク処理）も含めた使用率を負荷試験で求める（Iteration 9）
        # cgroup_cpu_s・pid・workers（Iteration 10）：複数のプロセスで動かすと，この要求に答えたプロセスは毎回違いうるので，
        # ノードの全体の CPU はコンテナの cgroup の累計で測り，process_cpu_s は pid が同じ 2 つの読み取りの間でだけ差を取る
        device = read_device_cpu()
        return {
            "status": "ok",
            "node_id": node_id,
            "shards": sorted(stores),
            "process_cpu_s": time.process_time(),
            "cgroup_cpu_s": read_cgroup_cpu_s(),
            "pid": os.getpid(),
            "workers": cfg.experiment.node_workers,
            "host_cpu": None
            if device is None
            else {
                "busy": device.busy,
                "total": device.total,
                "softirq": device.softirq,
                "n_cpu": os.cpu_count(),
            },
        }

    @app.get(AGENT_CARD_PATH)
    def agent_card(request: Request) -> dict[str, object]:
        return build_agent_card(node_id, str(request.base_url).rstrip("/"), stores)

    @app.get("/v1/profile", dependencies=up_only)
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
    @app.post("/v1/retrieve", dependencies=up_only)
    def retrieve(req: RetrieveRequest) -> RetrieveResponse:
        start = time.perf_counter()
        if cfg.retrieval.fusion == "rrf" and req.query_text is None:
            # 質問者が query_text を付け忘れると，黙って密ベクトルのみの検索になるので拒否する
            raise NodeError(400, "Invalid retrieval request", "fusion=rrf requires query_text")
        docs = search(store_of(req.shard_id), req.k, req.embedding, req.query_id, req.query_text)
        return RetrieveResponse(
            shard_id=req.shard_id,
            docs=[
                DocOut(doc_id=d.doc_id, title=d.title, content=d.content, score=d.score)
                for d in docs
            ],
            duration_s=time.perf_counter() - start,
        )

    @app.post("/v1/probe", dependencies=up_only)
    def probe(req: ProbeRequest) -> ProbeResponse:
        start = time.perf_counter()
        scores: dict[str, float] = {}
        bm25: dict[str, float] | None = None if req.bm25 is None else {}
        for shard_id in req.shard_ids:
            store = store_of(shard_id)
            docs = search(store, 1, req.embedding, None, None)
            scores[shard_id] = docs[0].score if docs else float("-inf")
            if bm25 is not None and req.bm25 is not None:
                # routing=bm25_probe（p0007 A4）：受信箱の中の本文の BM25 の最大（質問者から渡された統計で採点する）
                if not isinstance(store, FaissShardStore):
                    raise NodeError(400, "Invalid probe request", "bm25 needs a faiss shard")
                try:
                    bm25[shard_id] = store.probe_bm25(req.bm25.idf, req.bm25.avg_doc_len)
                except ValueError as exc:
                    raise NodeError(400, "Invalid probe request", str(exc)) from exc
        return ProbeResponse(
            node_id=node_id, scores=scores, bm25=bm25, duration_s=time.perf_counter() - start
        )

    @app.post("/v1/answer", dependencies=up_only)
    async def answer(req: AnswerRequest) -> AnswerResponse:
        start = time.perf_counter()
        docs: list[RetrievedDoc] = []
        for shard_id in req.shard_ids:
            # FAISS の検索とディスクの読み出しは同期処理なので，イベントループを塞がないようスレッドで動かす
            docs.extend(
                await asyncio.to_thread(
                    search,
                    store_of(shard_id),
                    req.k,
                    req.embedding,
                    req.query_id,
                    req.question,
                )
            )
        context = _merge_top(docs, req.k)
        retrieve_s = time.perf_counter() - start
        if req.delegate_url is not None:
            generated = await delegate(req, context)
            content, usage, delegated_to = generated.answer, generated.llm, generated.node_id
        else:
            content, usage = await generate(
                req.dataset, req.question, context, req.options, req.allow_unknown
            )
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
        allow_unknown: bool = False,
    ) -> tuple[str, LlmUsage]:
        messages = prompts.build_messages(dataset, question, context, options, allow_unknown)
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
            allow_unknown=req.allow_unknown,
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

    @app.post("/v1/generate", dependencies=up_only)
    async def generate_for_peer(req: GenerateRequest) -> GenerateResponse:
        context = [RetrievedDoc(d.doc_id, d.title, d.content, d.score) for d in req.docs]
        content, usage = await generate(
            req.dataset, req.question, context, req.options, req.allow_unknown
        )
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
    """指定されたシャードを読み込み，ノードを起動する（値は compose の command の引数で渡される）．

    experiment.node_workers が 1 なら従来どおり 1 プロセスで動かす．2 以上なら serve_workers で W 個のプロセスに分ける．
    """
    import uvicorn

    cfg = load_config(config_path)
    stores = {
        sid: load_shard(
            shards_dir / sid,
            cfg.retrieval.fusion,
            cfg.retrieval.fusion_depth,
            cfg.experiment.routing == "bm25_probe",
        )
        for sid in shard_ids
    }
    workers = cfg.experiment.node_workers
    if workers == 1:
        app = create_app(node_id, stores, cfg, ollama_url, expert_model=expert_model)
        uvicorn.run(app, host=host, port=port)
        return
    # 親での読み込みは，シャードの検証と行の先頭位置のキャッシュ（*.offsets.npy）を 1 回だけ書くため
    # （W 個のプロセスが同時に同じキャッシュを書くと，読みかけのファイルを読みうる）．親は要求を受けないので手放す
    del stores
    args = WorkerArgs(
        config_path, host, port, node_id, shards_dir, shard_ids, ollama_url, expert_model
    )
    serve_workers(args, workers)


# 待ち受けの列の長さ（uvicorn の既定の backlog と同じ）
LISTEN_BACKLOG = 2048


@dataclass(frozen=True)
class WorkerArgs:
    """複数のプロセスのノードで，各プロセスがシャードを読み込んでアプリを作るための引数（spawn で子へ渡す）．"""

    config_path: Path
    host: str
    port: int
    node_id: str
    shards_dir: Path
    shard_ids: list[str]
    ollama_url: str
    expert_model: str | None


def open_reuseport_socket(host: str, port: int) -> socket.socket:
    """SO_REUSEPORT で同じポートを開いた待ち受けのソケット．

    W 個のプロセスがそれぞれ自分のソケットを持ち，カーネルが新しい接続を各ソケットへ振り分ける．1 つのソケットを
    共有して accept を取り合う形（uvicorn の --workers）は，起こされるプロセスが偏り接続が少数のプロセスに集まりうる
    （LWN "The SO_REUSEPORT socket option", 2013．Cloudflare blog, 2017）ので使わない．
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind((host, port))
    sock.listen(LISTEN_BACKLOG)
    return sock


def serve_one_worker(args: WorkerArgs) -> None:
    """複数のプロセスのノードの 1 個のプロセス（spawn の子の入口）．シャードを読み込み，自分のソケットで待ち受ける．"""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO, format=f"%(asctime)s %(levelname)s [{os.getpid()}] %(message)s"
    )
    cfg = load_config(args.config_path)
    stores = {
        sid: load_shard(
            args.shards_dir / sid,
            cfg.retrieval.fusion,
            cfg.retrieval.fusion_depth,
            cfg.experiment.routing == "bm25_probe",
        )
        for sid in args.shard_ids
    }
    app = create_app(args.node_id, stores, cfg, args.ollama_url, expert_model=args.expert_model)
    sock = open_reuseport_socket(args.host, args.port)
    uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port)).run(sockets=[sock])


def serve_workers(args: WorkerArgs, workers: int) -> None:
    """W 個のプロセスでノードを動かし，どれか 1 個が終われば全てを止めて異常終了する（compose の restart に任せる）．

    SIGTERM・SIGINT（docker compose stop）を受けたら全ての子を止めて正常に終わる．
    """
    ctx = mp.get_context("spawn")
    procs = [
        ctx.Process(target=serve_one_worker, args=(args,), name=f"atrium-node-{i}")
        for i in range(workers)
    ]
    stopping = {"requested": False}

    def stop_children(signum: int, _frame: object) -> None:
        stopping["requested"] = True
        logger.info("received signal %d; stopping %d workers", signum, workers)
        for p in procs:
            if p.is_alive():
                p.terminate()

    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)
    for p in procs:
        p.start()
    logger.info("started %d node workers: %s", workers, [p.pid for p in procs])
    mp_connection.wait([p.sentinel for p in procs])
    for p in procs:
        if p.is_alive():
            p.terminate()
    for p in procs:
        p.join()
    if not stopping["requested"]:
        codes = [p.exitcode for p in procs]
        logger.error("a node worker exited unexpectedly (exit codes %s)", codes)
        raise SystemExit(1)
