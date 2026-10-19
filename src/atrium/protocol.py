"""専門家ノードの HTTP API の要求・応答の型（ノードと質問者が共有する）．"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from atrium.config import DatasetName
from atrium.manifest import Advert, ShardKind


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocOut(_Model):
    """断片 1 件．"""

    doc_id: str
    title: str
    content: str
    score: float


class RetrieveRequest(_Model):
    """断片返却型の検索要求．MedRAG 型は embedding，FeB4RAG 型は query_id を使う．"""

    shard_id: str
    k: int = Field(ge=1)
    embedding: list[float] | None = None
    query_id: str | None = None
    # retrieval.fusion=rrf のとき，BM25 に使う質問の文字列（それ以外は None）
    query_text: str | None = None


class RetrieveResponse(_Model):
    """断片返却型の検索結果．"""

    shard_id: str
    docs: list[DocOut]
    duration_s: float


class ProbeRequest(_Model):
    """flood_score の 1 段目の要求．ノードは各シャードの最高の検索スコアだけを返す（本文は返さない）．"""

    shard_ids: list[str]
    embedding: list[float]


class ProbeResponse(_Model):
    """シャード ID → 最高の検索スコア．"""

    node_id: str
    scores: dict[str, float]
    duration_s: float


class AnswerRequest(_Model):
    """宿る型の回答要求．ノードは shard_ids の断片から上位 k 件を使って自分の LLM で答える．"""

    dataset: DatasetName
    question: str
    options: dict[str, str]
    shard_ids: list[str]
    k: int = Field(ge=1)
    embedding: list[float] | None = None
    query_id: str | None = None
    # B4（delegate_gpu．p0004）：検索した断片を，この URL の GPU の専門家に渡して答えさせる
    delegate_url: str | None = None
    # owner_answer の abstain（p0007 I2）：答えがメールに無ければ UNKNOWN と答えさせるプロンプトを使う
    allow_unknown: bool = False


class GenerateRequest(_Model):
    """B4 の委託先への要求：受け取った断片と質問から回答を作る（断片を持ち主の外へ 1 台だけ出す）．"""

    dataset: DatasetName
    question: str
    options: dict[str, str]
    docs: list[DocOut]
    allow_unknown: bool = False


class LlmUsage(_Model):
    """生成 1 回分の計測値．"""

    prompt_tokens: int
    output_tokens: int
    prefill_s: float
    decode_s: float
    total_s: float


class AnswerResponse(_Model):
    """宿る型の回答．原文の断片は含めない．"""

    node_id: str
    answer: str
    choice: str | None
    n_context_docs: int
    top_score: float | None
    retrieve_s: float
    llm: LlmUsage
    duration_s: float
    # B4 で回答を作った委託先のノードと，そこへ渡した断片の数（委託しなければ None と 0）
    delegated_to: str | None = None
    docs_sent: int = 0


class GenerateResponse(_Model):
    """B4 の委託先の回答．"""

    node_id: str
    answer: str
    llm: LlmUsage


class ShardProfile(_Model):
    """シャードの自己紹介（重心を含む）．"""

    shard_id: str
    source: str
    kind: ShardKind
    n_docs: int
    dim: int
    encoder: str
    centroid: list[float]
    description: str
    # 事前に公開する情報（EnronQA のシャードだけ．p0004 の RQ-A）
    advert: Advert | None = None


class ProfileResponse(_Model):
    """ノードが持つシャードの一覧．"""

    node_id: str
    shards: list[ShardProfile]


class AvailabilityRequest(_Model):
    """EQ7b：ノードの参加・離脱の切り替え．false の間，ノードは検索・probe・回答・プロファイルの要求を 503 で拒否する．"""

    up: bool


class NewMail(_Model):
    """p0007 I5：メール 1 通（持ち主のデバイスが受け取って索引に足す，または中央へ転送する）．"""

    doc_id: str
    title: str
    content: str


class HideDocsRequest(_Model):
    """p0007 I5：実験の開始時に，「後から届くメール」を検索の結果から外す．"""

    shard_id: str
    doc_ids: list[str]


class HideDocsResponse(_Model):
    """外した行の数と，検索の対象に残った文書の数．"""

    shard_id: str
    n_hidden: int
    n_visible: int


class AddDocsRequest(_Model):
    """p0007 I5：新しいメールを届ける．ノードがその場で埋め込みを作り（CPU），索引に足す．"""

    shard_id: str
    docs: list[NewMail] = Field(min_length=1)


class AddDocsResponse(_Model):
    """足した通数と，ノードの中での所要時間（秒．embed_s は埋め込み，add_s は索引への追加，bm25_s は fusion=rrf の BM25 の作り直し）．"""

    shard_id: str
    n_added: int
    embed_s: float
    add_s: float
    bm25_s: float
    total_s: float
    embedder: str
    device: str
    n_visible: int


class ExportDocsRequest(_Model):
    """p0007 I5・I7：メールの本文を中央へ転送する．doc_ids が None ならシャードの検索対象の全メール．"""

    shard_id: str
    doc_ids: list[str] | None = None


class ExportDocsResponse(_Model):
    """取り出したメール．"""

    shard_id: str
    docs: list[NewMail]
    duration_s: float


class EmbedderLoadResponse(_Model):
    """埋め込みモデルの読み込み（最初の追加の時間に読み込みを含めないよう，測定の前に呼ぶ）．"""

    embedder: str
    device: str
    load_s: float
    # 読み込みの後のならし運転（1 文の埋め込み）の秒数
    warm_s: float


class SummaryRequest(_Model):
    """p0007 I7：デバイスに，自分のメールだけから公開情報（hybrid_probe の要約）を作らせる．"""

    term_size: int = Field(ge=1)
    n_centroids: int = Field(ge=1)
    seed: int


class InboxCenters(_Model):
    """受信箱 1 個の本文の重心（行が重心）．"""

    inbox: str
    centers: list[list[float]]


class SummaryResponse(_Model):
    """デバイスの公開情報（語の文書頻度の上位 term_size 語・語数の合計・受信箱ごとの重心）と，作るのにかかった時間（秒．デバイスの CPU）．"""

    node_id: str
    terms: list[str]
    dfs: list[int]
    total_terms: int
    n_docs: int
    inboxes: list[InboxCenters]
    tokenize_s: float
    embed_s: float
    kmeans_s: float
    total_s: float
    embedder: str
    device: str


class Problem(_Model):
    """RFC 9457 の problem details．"""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
