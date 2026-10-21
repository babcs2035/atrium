"""質問者：ベンチマークの質問を専門家ノードへ問い合わせ，1 問 1 行の結果を記録する．

1 問の処理は次の順に進む．

1. クエリ埋め込み（live: その場で計算／cached: 中継点で事前計算したもの）
2. ルーティング（atrium.routing）で問い合わせるデータ源を決める
3. answer_mode に応じて，
   - retrieval_only / snippet_return: 選んだデータ源の全シャードへ /v1/retrieve を送り，統合する．
     snippet_return のみ，統合した断片で質問者の LLM が回答する．
   - local_answer: 選んだシャードを持つノードへ /v1/answer を送り，回答を多数決で統合する．
   - owner_answer（p0007）: 選んだ各データ源（受信箱）へ /v1/answer を 1 つずつ送り，その受信箱の上位 k 通だけで答えさせ，
     answer.aggregate の規則で 1 つの回答に統合する．
   - routing=central_local（p0007）: ノードに問い合わせず，質問者が持つ全メールの 1 つの索引から上位 k 通を取って質問者の LLM が答える．
     experiment.central_generation=gpu_nodes（準集権）では，答えるのを GPU のノードの /v1/generate に分ける（索引は質問者が持ったまま）．
4. 計測値（各段の所要時間・受信バイト数・デバイス外へ出た断片数）とともに results.jsonl へ追記する．
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import logging
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpcore
import httpx
import numpy as np
from anyio._backends._asyncio import SocketStream as AnyioSocketStream
from httpcore._backends.anyio import AnyIOStream
from httpcore._utils import is_socket_readable

from atrium import llm, prompts
from atrium.arrays import F32Array
from atrium.benchmarks import Question
from atrium.config import AtriumConfig, Precision
from atrium.hf_revisions import pinned_revision
from atrium.manifest import NodeAssignment, Placement
from atrium.merge import (
    NodeVote,
    OwnerCandidate,
    OwnerChoice,
    SourcedDoc,
    choose_abstain,
    choose_agree,
    choose_max_score,
    contributing_sources,
    merge_by_qrels,
    merge_by_score,
    top_per_source,
    vote,
)
from atrium.protocol import (
    AnswerRequest,
    AnswerResponse,
    Bm25Query,
    DocOut,
    GenerateRequest,
    GenerateResponse,
    ProbeRequest,
    ProbeResponse,
    ProfileResponse,
    RetrieveRequest,
    RetrieveResponse,
    ShardProfile,
)
from atrium.routing import Router, RoutingQuery, SourceProfile
from atrium.routing.advert import CentroidProbeRouter, DeviceProbeRouter, tokenize
from atrium.routing.central import CentralIndex, inbox_of_doc
from atrium.routing.summary import Bm25ProbeRouter, SummaryProbeRouter
from atrium.store import RetrievedDoc

logger = logging.getLogger(__name__)

# ノードへの 1 回の問い合わせの打ち切り時間（秒）．宿る型は CPU での生成を含むので長めにする
RETRIEVE_TIMEOUT_S = 120.0
# 検索要求の接続エラー（ノードが待機中の接続を閉じた直後に，クライアントがその接続を再利用すると起きる）の再試行回数．
# 検索は読み取りだけなので，同じ要求を送り直しても結果は変わらない
RETRIEVE_RETRIES = 2
JSON_CONTENT_TYPE_HEADER = {"Content-Type": "application/json"}
# 回答の生成を GPU の専門家へ委託する answer_mode（GPU の無い専門家が，対応づけた GPU の専門家へ検索した断片を渡す）
DELEGATING_MODES = ("delegate_gpu", "owner_answer")
# routing=central_local の断片に付けるシャード ID（ノードのシャードではない．SourcedDoc の必須の項目を埋める）
CENTRAL_SHARD_ID = "central"


def apply_sniffio_lookup(mode: str) -> bool:
    """experiment.sniffio_lookup を質問者のプロセスに反映し，sniffio が実際に入っているかを返す．

    httpcore 1.0.9 の current_async_library() は呼ばれるたびに `import sniffio` を試みる．sniffio が無い環境では
    失敗した import が sys.modules に残らず，毎回 sys.path の探索が走る（1 要求あたり約 9.5 回）．
    blocked かつ sniffio が入っていないときだけ sys.modules["sniffio"] = None を置くと，import は探索せずに
    ImportError を送出し，httpcore は同じ except ImportError の枝で asyncio とみなす（返す値・例外の型は同じ）．
    入っている環境では本物のモジュールを隠さないので何もしない．spawn の子は親の sys.modules を引き継がないので，各プロセスの始めに呼ぶ．
    """
    if "sniffio" in sys.modules:
        installed = sys.modules["sniffio"] is not None
    else:
        installed = importlib.util.find_spec("sniffio") is not None
    if mode == "blocked" and not installed:
        sys.modules["sniffio"] = None  # type: ignore[assignment]  # None は「見つからない」の標準の印
    return installed


# lean が内部の実装に依存する版（これ以外では差し替えずに止める．黙って stock に戻すと比較が成り立たなくなる）
LEAN_HTTPCORE_VERSION = "1.0.9"
LEAN_ANYIO_VERSION = "4.15.1"
_pool_assign_applied = False


def _assign_requests_to_connections_lean(self: httpcore.AsyncConnectionPool) -> list[Any]:
    """httpcore 1.0.9 の AsyncConnectionPool._assign_requests_to_connections と同じ結果を返す（接続の列・閉じる接続・割り当て先）．

    元の掃除の段は，待機中の接続ごとに `len([c.is_idle() for c in self._connections])`（実際には全接続の数）を作り，
    接続の数の 2 乗の費用がかかる．ここでは各時点のリストの長さを数で持ち，1 回の走査にする．
    上流の PR #1035 の Bug 1（待機中の数ではなく全接続の数で max_keepalive と比べる）も元と同じに残す．
    割り当ての段（使える接続 → 新しい接続 → 待機中の接続を閉じて作り直す）の順と選び方も元と同じ．
    """
    connections = self._connections
    closing: list[Any] = []
    kept: list[Any] = []
    remaining = len(connections)  # 元の `len(self._connections)`（走査中の今の長さ）に当たる
    for connection in connections:
        if connection.is_closed():
            remaining -= 1
        elif connection.has_expired() or (
            connection.is_idle() and remaining > self._max_keepalive_connections
        ):
            remaining -= 1
            closing.append(connection)
        else:
            kept.append(connection)
    connections[:] = kept

    for pool_request in [r for r in self._requests if r.is_queued()]:
        origin = pool_request.request.url.origin
        available = next(
            (c for c in connections if c.can_handle_request(origin) and c.is_available()),
            None,
        )
        if available is not None:
            pool_request.assign_to_connection(available)
        elif len(connections) < self._max_connections:
            connection = self.create_connection(origin)
            connections.append(connection)
            pool_request.assign_to_connection(connection)
        else:
            idle = next((c for c in connections if c.is_idle()), None)
            if idle is not None:
                connections.remove(idle)
                closing.append(idle)
                connection = self.create_connection(origin)
                connections.append(connection)
                pool_request.assign_to_connection(connection)
    return closing


def apply_pool_assign(mode: str) -> str:
    """experiment.pool_assign を質問者のプロセスに反映し，httpcore の版を返す．

    httpcore 1.0.9 の割り当ては要求の開始と終了のたびに池の全接続を走査し，待機中の接続ごとに生存の確かめ
    （anyio の extra_attributes の辞書の組み立てと getpeername）と，全接続のリストの組み立てを行う（実機に近い構成で
    質問者の主スレッドの標本の 15〜25%．上流の PR #1035 は未マージで，修正を含む版も無い）．
    lean は掃除を 1 回の走査にし，`get_extra_info("is_readable")` だけを生のソケットへ直接問う（判定は同じ is_socket_readable）．
    版が違えば RuntimeError で止める．1 プロセスにつき 1 回だけ当て，spawn の子は親の差し替えを引き継がないので各プロセスの始めに呼ぶ．
    """
    version = httpcore.__version__
    if mode != "lean":
        return version
    anyio_version = importlib.metadata.version("anyio")
    if version != LEAN_HTTPCORE_VERSION or anyio_version != LEAN_ANYIO_VERSION:
        raise RuntimeError(
            f"pool_assign=lean supports httpcore {LEAN_HTTPCORE_VERSION} and anyio "
            f"{LEAN_ANYIO_VERSION} only (found {version}, {anyio_version})"
        )
    if not hasattr(AnyioSocketStream, "_raw_socket") or not hasattr(AnyIOStream, "get_extra_info"):
        raise RuntimeError("pool_assign=lean: the expected httpcore/anyio internals are missing")
    global _pool_assign_applied  # 1 プロセスに 1 回だけ当てるための印
    if _pool_assign_applied:
        return version
    original_get_extra_info = AnyIOStream.get_extra_info

    def get_extra_info(self: AnyIOStream, info: str) -> Any:
        stream = self._stream
        if info == "is_readable" and isinstance(stream, AnyioSocketStream):
            return is_socket_readable(stream._raw_socket)
        return original_get_extra_info(self, info)

    AnyIOStream.get_extra_info = get_extra_info  # type: ignore[method-assign]
    httpcore.AsyncConnectionPool._assign_requests_to_connections = (  # type: ignore[method-assign]
        _assign_requests_to_connections_lean
    )
    _pool_assign_applied = True
    logger.info("pool_assign=lean applied (httpcore %s, anyio %s)", version, anyio_version)
    return version


def encode_json_like_httpx(value: object) -> bytes:
    """httpx（0.28）の json= と同じ設定（区切り・ensure_ascii・allow_nan）で JSON の bytes を作る．
    pre_encoded の本文を json= の本文とバイト単位で一致させるため，この設定を変えてはならない．"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


async def post_with_retry(
    client: httpx.AsyncClient, url: str, body: dict[str, Any] | bytes, timeout: float
) -> httpx.Response:
    """読み取りだけの要求（検索・probe）を送る．ノードが待機中の接続を閉じた直後に再利用して起きる
    接続エラーは，同じ要求を RETRIEVE_RETRIES 回まで送り直す．

    body が dict なら httpx の json= で符号化し，bytes なら符号化済みの JSON としてそのまま送る．"""
    for attempt in range(RETRIEVE_RETRIES + 1):
        try:
            if isinstance(body, bytes):
                return await client.post(
                    url, content=body, headers=JSON_CONTENT_TYPE_HEADER, timeout=timeout
                )
            return await client.post(url, json=body, timeout=timeout)
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError) as exc:
            if attempt == RETRIEVE_RETRIES:
                raise
            logger.warning(
                "retrying %s (%d/%d): %s", url, attempt + 1, RETRIEVE_RETRIES, type(exc).__name__
            )
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
            self.MODEL,
            device="cuda" if torch.cuda.is_available() else "cpu",
            revision=pinned_revision(self.MODEL),
        )
        self._lock = threading.Lock()

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        """質問と各文（`<題>\\n<本文>`）の関連度（p0007 A5 の候補の特徴量も同じ得点を使う）．"""
        if not texts:
            return []
        pairs = [(query, text) for text in texts]
        with self._lock:
            out = self._model.predict(pairs, batch_size=self.BATCH, convert_to_numpy=True)
        return [float(s) for s in np.asarray(out).reshape(-1)]

    def rerank(self, query: str, docs: Sequence[SourcedDoc], k: int) -> list[SourcedDoc]:
        """クロスエンコーダのスコアで上位 k 件を返す．"""
        if not docs:
            return []
        scores = self.scores(query, [f"{d.doc.title}\n{d.doc.content}".strip() for d in docs])
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
    # EQ7b：質問者のメンバー表（見えているノードの URL）．None なら全ノードが見えている（従来どおり）
    visible_urls: frozenset[str] | None = None
    # answer_mode=gold_context（p0006 C0．評価専用）：質問 ID → 正解のメール．他の条件では空
    gold_docs: dict[str, SourcedDoc] = field(default_factory=dict)
    # routing=central_local（p0007 I4）：質問者が持つ全メールの 1 つの索引．他の方式では None
    central_index: CentralIndex | None = None
    # p0007 I6：churn を指定しなくても，止まったノードの失敗を問の失敗にせず，返事のあった分で続ける（failure-run）
    tolerate_node_failures: bool = False
    _embed_lock: threading.Lock = field(default_factory=threading.Lock)
    # experiment.central_generation=gpu_nodes：次に生成を頼む GPU のノードの番号（ラウンドロビン．イベントループの中だけで進める）
    _next_generator: int = 0

    def __post_init__(self) -> None:
        """シャード → ノード URL の表などを作る．"""
        self._rebuild_tables()

    def _rebuild_tables(self) -> None:
        """配置・データ源から，シャード → ノード URL の表などを作る（途中で加わるデバイスを載せるときも呼ぶ）．"""
        self._url_of_shard = self.placement.url_of_shard()
        # device_probe：データ源 → それを持つノードの URL（EnronQA は 1 データ源 1 シャード．複数のシャードの
        # データ源は 1 台に決まらないので，device_probe では使えない）
        self._device_of_source = {
            s.source: self._url_of_shard[s.shard_ids[0]]
            for s in self.sources
            if len(s.shard_ids) == 1
        }
        # churn を指定した実験では，離脱したノードの失敗を問の失敗にせず，返事のあった分で続ける
        self._tolerant = self.cfg.experiment.churn is not None or self.tolerate_node_failures

    def admit(self, placement: Placement, sources: list[SourceProfile], router: Router) -> None:
        """途中で加わったデバイス（受信箱）をメンバー表に載せる（p0007 I7）．配置・データ源・ルーターを差し替え，対応表を作り直す．"""
        self.placement = placement
        self.sources = sources
        self.router = router
        self._rebuild_tables()

    def _routable_sources(self) -> list[SourceProfile]:
        """メンバー表で見えているデータ源（全てのシャードのノードが見えているもの）．"""
        if self.visible_urls is None:
            return self.sources
        return [
            s
            for s in self.sources
            if all(self._url_of_shard[sid] in self.visible_urls for sid in s.shard_ids)
        ]

    async def _gather_requests[T](
        self, coros: Sequence[Coroutine[Any, Any, T]]
    ) -> tuple[list[T | None], list[int]]:
        """要求を並べて実行し，（結果．失敗した位置は None，失敗した位置の番号）を返す．

        従来は 1 つでも失敗すれば全体を失敗にする（gather_or_cancel）．churn を指定した実験では，ノードの離脱（HTTP のエラーと
        接続の失敗）だけを許し，ほかの例外（配置の誤りなど）は投げ直す．
        """
        if not self._tolerant:
            return list(await gather_or_cancel(coros)), []
        outcomes = await asyncio.gather(*coros, return_exceptions=True)
        failed: list[int] = []
        results: list[T | None] = []
        for i, outcome in enumerate(outcomes):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, httpx.HTTPError):
                    raise outcome
                failed.append(i)
                results.append(None)
            else:
                results.append(outcome)
        return results, failed

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

    def _encode_embeddings(self, embeddings: dict[str, F32Array]) -> dict[str, bytes]:
        """probe_body または retrieve_body が pre_encoded のとき，検索器ごとの埋め込みの JSON を問ごとに 1 回だけ作る．

        probe と retrieve が同じ bytes を共有する（問ごとの埋め込みの符号化は 1 回）．どちらも per_request なら空の表を返す．
        """
        exp = self.cfg.experiment
        if exp.probe_body != "pre_encoded" and exp.retrieve_body != "pre_encoded":
            return {}
        return {
            encoder: encode_json_like_httpx(embeddings[encoder].tolist())
            for encoder in {s.encoder for s in self.sources}
            if encoder in embeddings
        }

    async def _retrieve(
        self,
        source: SourceProfile,
        shard_id: str,
        embedding: F32Array,
        question: Question,
        encoded_embedding: bytes | None = None,
    ) -> tuple[list[SourcedDoc], dict[str, Any]]:
        """1 シャードの検索．encoded_embedding（埋め込みの JSON）があれば，RetrieveRequest の model_dump() を json= で
        符号化した本文とバイト単位で同じ本文を bytes の連結で作って送る（faiss のシャードだけ）．"""
        k = self._k_ret()
        # retrieval.fusion=rrf のノードだけが BM25 に使う質問の文字列．none では従来どおり null を送る
        query_text = question.question if self.cfg.retrieval.fusion == "rrf" else None
        request_body: dict[str, Any] | bytes
        if encoded_embedding is not None and source.kind == "faiss":
            request_body = (
                b'{"shard_id":'
                + encode_json_like_httpx(shard_id)
                + b',"k":'
                + encode_json_like_httpx(k)
                + b',"embedding":'
                + encoded_embedding
                + b',"query_id":'
                + encode_json_like_httpx(question.source_qid)
                + b',"query_text":'
                + encode_json_like_httpx(query_text)
                + b"}"
            )
        else:
            # 配布済みの検索結果を返すシャードは埋め込みを使わないので送らない（FeB4RAG では最大 4,096 次元になる）
            req = RetrieveRequest(
                shard_id=shard_id,
                k=k,
                embedding=embedding.tolist() if source.kind == "faiss" else None,
                query_id=question.source_qid,
                query_text=query_text,
            )
            request_body = req.model_dump()
        start = time.perf_counter()
        url = f"{self._url_of_shard[shard_id]}/v1/retrieve"
        response = await post_with_retry(self.client, url, request_body, RETRIEVE_TIMEOUT_S)
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
        encoded_embeddings: dict[str, bytes] | None = None,
    ) -> list[SourcedDoc]:
        use_encoded = self.cfg.experiment.retrieve_body == "pre_encoded"
        if use_encoded and encoded_embeddings is None:
            encoded_embeddings = self._encode_embeddings(embeddings)
        tasks = []
        asked_shards: list[str] = []
        for source_name in selected:
            source = self._profile(source_name)
            encoded = (
                encoded_embeddings.get(source.encoder)
                if use_encoded and encoded_embeddings
                else None
            )
            for shard_id in source.shard_ids:
                tasks.append(
                    self._retrieve(source, shard_id, embeddings[source.encoder], question, encoded)
                )
                asked_shards.append(shard_id)
        start = time.perf_counter()
        outcomes, failed = await self._gather_requests(tasks)
        results = [o for o in outcomes if o is not None]
        if self._tolerant:
            # 問い合わせたノードと，返事の無かったノード（EQ7b の無駄な問い合わせ）
            record["contacted_nodes"] = len({self._url_of_shard[s] for s in asked_shards})
            record["failed_nodes"] = len({self._url_of_shard[asked_shards[i]] for i in failed})
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

    def _delegate_urls(self, urls: Sequence[str]) -> dict[str, str]:
        """B4（delegate_gpu）・owner_answer：GPU の無い専門家（URL）→ 回答を委託する，対応づけた GPU の専門家の URL．
        委託しない answer_mode（local_answer など）では空にする．"""
        if self.cfg.experiment.answer_mode not in DELEGATING_MODES:
            return {}
        url_of_host = {n.host: n.url for n in self.placement.nodes}
        host_of_url = {n.url: n.host for n in self.placement.nodes}
        delegate_url_of: dict[str, str] = {}
        for url in urls:
            gpu_host = self.placement.delegate_of.get(host_of_url[url])
            if gpu_host is not None:
                delegate_url_of[url] = url_of_host[gpu_host]
        return delegate_url_of

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
        delegate_url_of = self._delegate_urls(list(shards_of_url))

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

    async def _owner_answers(
        self,
        question: Question,
        selected: list[str],
        embeddings: dict[str, F32Array],
        record: dict[str, Any],
    ) -> None:
        """owner_answer（p0007 I2）：選んだ各受信箱が，その受信箱の上位 k 通だけで答え，answer.aggregate の規則で 1 つに統合する．

        local_answer と違い，同じノードに複数の受信箱があっても受信箱ごとに別の要求にする（受信箱ごとの文脈で答えさせるため）．
        GPU の無いノードは B4 と同じ経路で，検索した断片を対応づけた GPU のノードへ渡して答えさせる．
        回答の本文は，採った 1 つ（answer・final_answer）だけを record に残す（候補の回答にはメールの引用が混ざりうるので，
        残りは UNKNOWN かどうかと得点などの数だけを残す．採った回答は commit の前に sanitize_results.py を通す）．
        answer.save_candidates（p0007 A5）のときだけ，全候補の回答（"Answer:" の後ろ）と選び方の特徴量を owner_answer.candidates
        に残す（統合の規則をオフラインで比べるため．commit の前に sanitize_results.py が候補の回答も整形する）．
        """
        rule = self.cfg.answer.aggregate
        allow_unknown = rule == "abstain"
        save_candidates = self.cfg.answer.save_candidates
        profiles = [self._profile(name) for name in selected]
        urls = []
        for profile in profiles:
            owners = {self._url_of_shard[sid] for sid in profile.shard_ids}
            if len(owners) != 1:
                raise ValueError(f"owner_answer needs single-node sources: {profile.source}")
            urls.append(owners.pop())
        delegate_url_of = self._delegate_urls(urls)

        async def ask(profile: SourceProfile, url: str) -> tuple[AnswerResponse, int]:
            req = AnswerRequest(
                dataset=self.cfg.experiment.dataset,
                question=question.question,
                options=question.options,
                shard_ids=list(profile.shard_ids),
                k=self._k_local(),
                embedding=embeddings[profile.encoder].tolist(),
                query_id=question.source_qid,
                delegate_url=delegate_url_of.get(url),
                allow_unknown=allow_unknown,
                with_features=save_candidates,
            )
            response = await self.client.post(
                f"{url}/v1/answer", json=req.model_dump(), timeout=self.cfg.local_answer.timeout_s
            )
            response.raise_for_status()
            return AnswerResponse.model_validate_json(response.content), len(response.content)

        start = time.perf_counter()
        answers = await gather_or_cancel([ask(p, u) for p, u in zip(profiles, urls, strict=True)])
        record["timings"]["generate_s"] = time.perf_counter() - start
        candidates = [
            OwnerCandidate(
                p.source,
                (free := prompts.extract_free_answer(a.answer)),
                a.top_score,
                prompts.is_unknown_answer(free),
            )
            for p, (a, _) in zip(profiles, answers, strict=True)
        ]
        choice, select_info = await self._choose_owner_answer(question, candidates, rule, record)
        chosen = answers[choice.index][0]
        record["answer"] = chosen.answer
        record["final_answer"] = candidates[choice.index].answer
        record["bytes_received"] = sum(size for _, size in answers)
        record["snippets_exposed"] = 0
        # 受信箱ごとに，外へ出た文書の数（委託したときだけ）を残す（B4 と同じ見方で露出を数えられる）
        record["docs_exposed_by_source"] = {
            p.source: a.docs_sent
            for p, (a, _) in zip(profiles, answers, strict=True)
            if a.docs_sent
        }
        record["query_recipients"] = len(set(urls) | set(delegate_url_of.values()))
        record["node_answers"] = [
            {
                "node_id": a.node_id,
                "source": c.source,
                "unknown": c.unknown,
                "top_score": a.top_score,
                "n_context_docs": a.n_context_docs,
                "retrieve_s": a.retrieve_s,
                "duration_s": a.duration_s,
                "delegated_to": a.delegated_to,
                **a.llm.model_dump(),
            }
            for c, (a, _) in zip(candidates, answers, strict=True)
        ]
        record["owner_answer"] = {
            "aggregate": rule,
            "candidate_sources": [c.source for c in candidates],
            "n_candidates": len(candidates),
            # 質問者が /v1/answer を送った回数と，生成した回数（候補ごとに 1 回．llm_select は選ぶ 1 回を足す）
            "n_requests": len(candidates),
            # probe（候補を選ぶ 1 段目）の要求の数を足した，1 問あたりの質問者からノードへの要求の総数
            "n_requests_total": len(candidates) + int(record.get("probe_recipients", 0)),
            "n_generations": len(candidates) + select_info["n_select_generations"],
            "n_unknown": sum(c.unknown for c in candidates),
            "chosen_source": candidates[choice.index].source,
            "chosen_index": choice.index,
            "chosen_unknown": candidates[choice.index].unknown,
            "fell_back_to_max_score": choice.fell_back,
            "vote_groups": [list(g) for g in choice.vote_groups],
            **select_info,
        }
        if save_candidates:
            probe = record.get("probe_selected_scores", {})
            record["owner_answer"]["candidates"] = [
                {
                    "source": c.source,
                    "answer": c.answer,
                    "unknown": c.unknown,
                    "top_score": c.top_score,
                    "probe_dense": probe.get(c.source, {}).get("dense"),
                    "probe_bm25": probe.get(c.source, {}).get("bm25"),
                    "probe_fused": probe.get(c.source, {}).get("fused"),
                    **(a.features.model_dump() if a.features is not None else {}),
                }
                for c, (a, _) in zip(candidates, answers, strict=True)
            ]

    async def _choose_owner_answer(
        self,
        question: Question,
        candidates: list[OwnerCandidate],
        rule: str,
        record: dict[str, Any],
    ) -> tuple[OwnerChoice, dict[str, Any]]:
        """統合の規則で候補から 1 つを採る．llm_select だけ質問者の LLM を呼ぶ．（採った候補，llm_select の記録）を返す．

        llm_select で候補が 1 つなら選ぶ必要が無いので呼ばない．LLM の出力から番号を取り出せなければ max_score にする．
        """
        select_info: dict[str, Any] = {"n_select_generations": 0, "select_parse_failed": False}
        if rule == "abstain":
            return choose_abstain(candidates), select_info
        if rule == "agree":
            return choose_agree(candidates), select_info
        if rule == "max_score" or len(candidates) == 1:
            return choose_max_score(candidates), select_info
        messages = prompts.build_select_messages(question.question, [c.answer for c in candidates])
        start = time.perf_counter()
        result = await llm.chat(
            self.client, self.ollama_url, self.cfg.llm.requester_model, messages, self.cfg.llm
        )
        record["timings"]["select_s"] = time.perf_counter() - start
        select_info["n_select_generations"] = 1
        select_info["select_prompt_tokens"] = result.prompt_tokens
        select_info["select_output_tokens"] = result.output_tokens
        picked = prompts.extract_selected_index(result.content, len(candidates))
        if picked is None:
            select_info["select_parse_failed"] = True
            return choose_max_score(candidates), select_info
        return OwnerChoice(picked), select_info

    async def _central_answer(
        self, question: Question, embeddings: dict[str, F32Array], record: dict[str, Any]
    ) -> str | None:
        """routing=central_local（p0007 I4）：質問者の 1 つの索引から上位 k 通を取り，質問者の LLM で答える（B1 相当）．

        ノードには問い合わせない．メールは起動の前に質問者へ集めてあるので，質問ごとに外へ出る文書は 0 とする
        （集める一度きりの量は run_meta の central_index に残す）．retrieval.merge=cross_encoder なら，上位 retrieval.k_ret 通を
        bge-reranker-v2-m3 で並べ替えた上位 k 通（再ランクの順）を文脈にする（p0007 A5 の C1-RRF-CE）．
        """
        if self.central_index is None:
            raise RuntimeError("routing=central_local requires a central index")
        encoder = self.cfg.data.require_enronqa().encoder
        # retrieval.merge=cross_encoder（p0007 A5 の C1-RRF-CE）：索引の上位 retrieval.k_ret 通を再ランクして上位 k 通にする
        rerank = self.cfg.retrieval.merge == "cross_encoder"
        start = time.perf_counter()
        found = await asyncio.to_thread(
            self.central_index.search,
            embeddings[encoder],
            self.cfg.retrieval.k_ret if rerank else self._k_ret(),
            question.question if self.cfg.retrieval.fusion == "rrf" else None,
        )
        record["timings"]["retrieve_s"] = time.perf_counter() - start
        docs = [
            SourcedDoc(
                inbox_of_doc(d.doc_id),
                CENTRAL_SHARD_ID,
                DocOut(doc_id=d.doc_id, title=d.title, content=d.content, score=d.score),
            )
            for d in found
        ]
        if rerank:
            start = time.perf_counter()
            record["rerank_pool"] = len(docs)
            docs = await asyncio.to_thread(self._merge, question, docs)
            record["timings"]["rerank_s"] = time.perf_counter() - start
        record["selected_sources"] = []
        record["n_shards_queried"] = 0
        record["query_recipients"] = 0
        record["contributing_sources"] = contributing_sources(docs)
        record["top_doc_ids"] = [d.doc.doc_id for d in docs]
        if self.cfg.experiment.central_generation == "gpu_nodes":
            return await self._generate_on_gpu_node(question, docs, record)
        return await self._generate(question, docs, record, self.cfg.llm.requester_model)

    async def _generate_on_gpu_node(
        self, question: Question, docs: list[SourcedDoc], record: dict[str, Any]
    ) -> str | None:
        """準集権（p0007 D．central_generation=gpu_nodes）：索引の上位 k 通と質問を，GPU のノードの /v1/generate へ送って答えさせる．

        送り先は placement.delegate_of の委託先（B4 と同じ GPU のノード）をホスト名順に並べたものへのラウンドロビン．
        プロンプト・モデル・温度はノードが B4 の委託先と同じ関数で作る（中央の `_generate` と同じ文面）．
        ノードへ出る文書は 1 問あたり k 通で，記録の docs_sent_to_generator に残す．
        """
        url_of_host = {n.host: n.url for n in self.placement.nodes}
        generators = sorted(set(self.placement.delegate_of.values()))
        if not generators:
            raise RuntimeError("central_generation=gpu_nodes requires GPU nodes in the placement")
        host = generators[self._next_generator % len(generators)]
        self._next_generator += 1
        body = GenerateRequest(
            dataset=self.cfg.experiment.dataset,
            question=question.question,
            options=question.options,
            docs=[d.doc for d in docs],
        )
        start = time.perf_counter()
        response = await self.client.post(
            f"{url_of_host[host]}/v1/generate",
            json=body.model_dump(),
            timeout=self.cfg.local_answer.timeout_s,
        )
        response.raise_for_status()
        generated = GenerateResponse.model_validate_json(response.content)
        record["timings"]["generate_s"] = time.perf_counter() - start
        record["query_recipients"] = 1
        record["docs_sent_to_generator"] = len(docs)
        record["generated_by"] = generated.node_id
        record["answer"] = generated.answer
        record["prompt_tokens"] = generated.llm.prompt_tokens
        record["output_tokens"] = generated.llm.output_tokens
        record["llm"] = {"prefill_s": generated.llm.prefill_s, "decode_s": generated.llm.decode_s}
        record["final_answer"] = prompts.extract_free_answer(generated.answer)
        return None

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
        self,
        embeddings: dict[str, F32Array],
        record: dict[str, Any],
        targets: Sequence[str] | None = None,
        encoded_embeddings: dict[str, bytes] | None = None,
        bm25: Bm25Query | None = None,
        bm25_out: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """probe（/v1/probe）の 1 段目：クエリ埋め込みを送り，データ源ごとの最高の検索スコアを集める．

        targets を省略すると，flood_score と同じく全ノードの全シャードへ送る．centroid_probe は，候補のデータ源の名前を
        渡し，そのシャードを持つノードにだけ，候補のシャードの shard_ids で送る．
        bm25（bm25_probe．p0007 A4）を渡すと，質問の語の統計を要求に載せ，ノードが返した受信箱の中の BM25 の最大を bm25_out
        （データ源の名前 → BM25 の最大）に集める．返す値は従来どおり密ベクトルの最高の検索スコア．
        """
        target_set = None if targets is None else set(targets)
        source_of_shard = {
            sid: s
            for s in self.sources
            if target_set is None or s.source in target_set
            for sid in s.shard_ids
        }

        # pre_encoded：埋め込みの JSON は検索器ごとに 1 回だけ作り，ノードごとに shard_ids だけ差し替える
        # 問の処理が作った表（retrieve と共有）があればそれを使う
        if self.cfg.experiment.probe_body != "pre_encoded":
            encoded_embeddings = {}
        elif encoded_embeddings is None:
            encoded_embeddings = self._encode_embeddings(embeddings)

        async def ask(node: NodeAssignment) -> tuple[ProbeResponse, int]:
            shard_ids = [sid for sid in node.shard_ids if sid in source_of_shard]
            encoder = source_of_shard[shard_ids[0]].encoder
            body: dict[str, Any] | bytes
            # bm25 は bm25_probe だけが付ける．付けないときは従来と同じ本文（キーを省く）にする
            bm25_part = (
                b"" if bm25 is None else b',"bm25":' + encode_json_like_httpx(bm25.model_dump())
            )
            if encoder in encoded_embeddings:
                body = (
                    b'{"shard_ids":'
                    + encode_json_like_httpx(shard_ids)
                    + b',"embedding":'
                    + encoded_embeddings[encoder]
                    + bm25_part
                    + b"}"
                )
            else:
                req = ProbeRequest(
                    shard_ids=shard_ids, embedding=embeddings[encoder].tolist(), bm25=bm25
                )
                body = req.model_dump(exclude_none=True)
            response = await post_with_retry(
                self.client, f"{node.url}/v1/probe", body, RETRIEVE_TIMEOUT_S
            )
            response.raise_for_status()
            return ProbeResponse.model_validate_json(response.content), len(response.content)

        nodes = [
            n
            for n in self.placement.nodes
            if any(s in source_of_shard for s in n.shard_ids)
            and (self.visible_urls is None or n.url in self.visible_urls)
        ]
        start = time.perf_counter()
        outcomes, failed = await self._gather_requests([ask(n) for n in nodes])
        results = [o for o in outcomes if o is not None]
        record["timings"]["probe_s"] = time.perf_counter() - start
        record["probe_recipients"] = len(nodes)
        # ノードの probe の仕事量はシャードの数に比例するので，デバイスごとの数も残す
        per_device = [sum(s in source_of_shard for s in n.shard_ids) for n in nodes]
        record["probe_shards"] = sum(per_device)
        record["probe_shards_per_device"] = per_device
        # ノードの計算時間の最大（probe_s との差が，待ち行列と通信の時間）
        record["probe_node_s_max"] = max((r.duration_s for r, _ in results), default=0.0)
        if self._tolerant:
            record["probe_failed"] = len(failed)
        record["probe_bytes"] = sum(size for _, size in results)
        best: dict[str, float] = {}
        for resp, _ in results:
            for sid, score in resp.scores.items():
                name = source_of_shard[sid].source
                best[name] = max(best.get(name, float("-inf")), score)
            if bm25 is not None and bm25_out is not None:
                if resp.bm25 is None:
                    raise ValueError(
                        f"node {resp.node_id} returned no BM25 scores for a bm25 probe"
                    )
                for sid, score in resp.bm25.items():
                    name = source_of_shard[sid].source
                    bm25_out[name] = max(bm25_out.get(name, float("-inf")), score)
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
        selected = self.router.select(query, self._routable_sources())
        record["timings"]["route_s"] = time.perf_counter() - start
        return selected

    async def _route_by_candidate_probe(
        self,
        router: CentroidProbeRouter | DeviceProbeRouter | SummaryProbeRouter,
        question: Question,
        embeddings: dict[str, F32Array],
        record: dict[str, Any],
        encoded_embeddings: dict[str, bytes] | None = None,
    ) -> list[str]:
        """centroid_probe・device_probe：1 段目の候補を同期に求め（multi_centroid と同じくイベントループの上），候補だけへ probe を送って上位 m 個を選ぶ．

        centroid_probe の候補は上位 K 個のデータ源，device_probe の候補は上位 D 台のデバイスに載る全てのデータ源である．
        route_s は 1 段目の採点と 2 段目の選択の合計である（probe の待ちは probe_s に入る）．
        """
        start = time.perf_counter()
        query = RoutingQuery(
            question.qid,
            question.question,
            embeddings,
            tokens=tuple(tokenize(question.question)),
        )
        sources = self._routable_sources()
        if isinstance(router, (DeviceProbeRouter, SummaryProbeRouter)):
            missing = [s.source for s in sources if s.source not in self._device_of_source]
            if missing:
                raise ValueError(f"device_probe needs single-shard sources: {missing[:5]}")
            candidates = router.candidates(query, sources, self._device_of_source)
        else:
            candidates = router.candidates(query, sources)
        first_stage_s = time.perf_counter() - start
        if isinstance(router, Bm25ProbeRouter):
            # bm25_probe：probe に質問の語の統計を載せ，密ベクトルと BM25 の 2 つの得点で上位 m 個を選ぶ
            bm25_scores: dict[str, float] = {}
            probe_scores = await self._probe(
                embeddings,
                record,
                candidates,
                encoded_embeddings,
                bm25=router.bm25_query(query),
                bm25_out=bm25_scores,
            )
            start = time.perf_counter()
            selected = router.select_among(candidates, probe_scores, bm25_scores)
            if self.cfg.answer.save_candidates:
                # p0007 A5：選んだ受信箱の probe の得点（密ベクトル・BM25・融合得点）を，候補の選び方の特徴量として残す
                # probe に答えた受信箱が m 個に満たないと，答えなかった受信箱も選ばれうる（その得点は None）
                fused = router.fused_scores(candidates, probe_scores, bm25_scores)
                record["probe_selected_scores"] = {
                    s: {
                        "dense": probe_scores.get(s),
                        "bm25": bm25_scores.get(s),
                        "fused": fused.get(s),
                    }
                    for s in selected
                }
        else:
            probe_scores = await self._probe(embeddings, record, candidates, encoded_embeddings)
            start = time.perf_counter()
            selected = router.select_among(candidates, probe_scores)
        record["timings"]["route_s"] = first_stage_s + (time.perf_counter() - start)
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
            if mode == "gold_context":
                # 検索もルーティングもせず，正解のメールだけを文脈にする（回答のプロンプトは snippet_return と同じ）
                gold = self.gold_docs[question.qid]
                record["selected_sources"] = [gold.source]
                record["n_shards_queried"] = 0
                record["top_doc_ids"] = [gold.doc.doc_id]
                record["contributing_sources"] = contributing_sources([gold])
                record["choice"] = await self._generate(
                    question, [gold], record, self.cfg.llm.requester_model
                )
                record["timings"]["e2e_s"] = time.perf_counter() - start
                return record
            embeddings = await asyncio.to_thread(self._embed, question, record)
            if self.central_index is not None:
                record["choice"] = await self._central_answer(question, embeddings, record)
                record["timings"]["e2e_s"] = time.perf_counter() - start
                return record
            encoded_embeddings = self._encode_embeddings(embeddings)
            if isinstance(
                self.router, (CentroidProbeRouter, DeviceProbeRouter, SummaryProbeRouter)
            ):
                selected = await self._route_by_candidate_probe(
                    self.router, question, embeddings, record, encoded_embeddings
                )
            else:
                probe_scores = (
                    await self._probe(embeddings, record, None, encoded_embeddings)
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
            elif mode == "owner_answer":
                if selected:
                    await self._owner_answers(question, selected, embeddings, record)
            else:
                docs = await self._snippets(
                    question, selected, embeddings, record, encoded_embeddings
                )
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
