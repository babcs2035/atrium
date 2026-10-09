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


class Problem(_Model):
    """RFC 9457 の problem details．"""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
