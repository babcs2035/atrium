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


class Bm25Query(_Model):
    """bm25_probe（p0007 A4）の probe に載せる，質問の語の統計．ノードはこれだけで受信箱の中の BM25 を採点できる．"""

    # 質問の語（重複を除く）→ 全体で共有した IDF（質問者が，各デバイスの公開済みの文書頻度の合計から作る）
    idf: dict[str, float]
    # 全体で共有した文書の長さ（本文の語数）の平均
    avg_doc_len: float = Field(gt=0)


class ProbeRequest(_Model):
    """flood_score の 1 段目の要求．ノードは各シャードの最高の検索スコアだけを返す（本文は返さない）．"""

    shard_ids: list[str]
    embedding: list[float]
    # bm25_probe だけが付ける（省略なら従来と同じ要求）
    bm25: Bm25Query | None = None


class ProbeResponse(_Model):
    """シャード ID → 最高の検索スコア．bm25_probe では，シャード ID → 受信箱の中の本文の BM25 の最大も返す．"""

    node_id: str
    scores: dict[str, float]
    duration_s: float
    bm25: dict[str, float] | None = None


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
    # answer.save_candidates（p0007 A5）：候補の選び方の特徴量（AnswerFeatures）も返させる．false なら従来と同じ応答
    with_features: bool = False
    # answer.aggregate=cross_encoder（p0007 F-6）：文脈にした上位 k 通（題・本文）も返させる（質問者が cross-encoder で採点する）
    with_context_docs: bool = False


class GenerateRequest(_Model):
    """B4 の委託先への要求：受け取った断片と質問から回答を作る（断片を持ち主の外へ 1 台だけ出す）．"""

    dataset: DatasetName
    question: str
    options: dict[str, str]
    docs: list[DocOut]
    allow_unknown: bool = False
    # p0007 A5：生成したトークンの対数確率の平均も返させる
    logprobs: bool = False


class LlmUsage(_Model):
    """生成 1 回分の計測値．"""

    prompt_tokens: int
    output_tokens: int
    prefill_s: float
    decode_s: float
    total_s: float


class AnswerFeatures(_Model):
    """p0007 A5：owner_answer の候補を選ぶための特徴量（with_features の要求にだけ付ける．本文は含めない）．"""

    # 文脈にした文書の ID（内積の降順．後から cross-encoder の得点を求めるのに使う）
    context_doc_ids: list[str]
    # 受信箱の中の BM25（その受信箱だけの統計）の最大と，RRF の得点の最大（retrieval.fusion=rrf のときだけ．それ以外は None）
    bm25_max: float | None = None
    rrf_max: float | None = None
    # 生成したトークンの対数確率の平均と，その数（Ollama が返さなければ None と 0）
    avg_logprob: float | None = None
    n_logprob_tokens: int = 0


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
    # with_features の要求にだけ付ける（p0007 A5）
    features: AnswerFeatures | None = None
    # with_context_docs の要求にだけ付ける（p0007 F-6．メールの本文を含むので，質問者は採点に使うだけで保存しない）
    context_docs: list[DocOut] | None = None


class GenerateResponse(_Model):
    """B4 の委託先の回答．"""

    node_id: str
    answer: str
    llm: LlmUsage
    # logprobs の要求にだけ付ける（p0007 A5）
    avg_logprob: float | None = None
    n_logprob_tokens: int = 0


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
    # 受信箱ごとに使うメールの数の上限（動作確認用．None は全て．上限を付けた要約は，オフラインの要約と等価ではない）
    max_docs_per_inbox: int | None = Field(default=None, ge=1)


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
