"""config.yaml の型定義と読み込み．

全ての処理は `load_config()` が返す `AtriumConfig` だけを参照し，設定値を個別に環境変数などで
上書きしない（実験条件を config.yaml の差分だけで追跡できるようにするため）．
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DatasetName = Literal["medrag", "feb4rag"]
RoutingName = Literal["ragroute", "all", "random", "none"]
AnswerMode = Literal["snippet_return", "local_answer", "retrieval_only"]
MergeName = Literal["score", "cross_encoder", "qrels_oracle"]
QueryEmbeddingMode = Literal["live", "cached"]
Precision = Literal["fp32", "fp16_autocast"]

DEFAULT_CONFIG_PATH = Path("config.yaml")


class _Strict(BaseModel):
    """未知のキーを誤記として拒否する基底クラス．"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentConfig(_Strict):
    """実験の種類と条件．"""

    kind: Literal["e0_measure", "e1_routing"]
    dataset: DatasetName
    routing: RoutingName
    answer_mode: AnswerMode
    question_limit: int | None = None
    parallel: int = Field(default=4, ge=1)
    seed: int = 12


class RetrievalConfig(_Strict):
    """検索と統合の条件．"""

    k_ret: int = Field(default=50, ge=1)
    k_rerank: int = Field(default=15, ge=1)
    merge: MergeName = "score"
    query_embedding: dict[DatasetName, QueryEmbeddingMode]


class RoutingConfig(_Strict):
    """ルーティング方式ごとのパラメータ．"""

    random_k: dict[DatasetName, int]
    ragroute_threshold: float = 0.5


class LlmConfig(_Strict):
    """Ollama で動かす LLM の設定．"""

    ollama_version: str
    requester_model: str
    expert_model: str
    num_predict: int = 2048
    num_ctx: int = 16384
    think: bool = False
    timeout_s: float = 600.0


class LocalAnswerConfig(_Strict):
    """宿る型（local_answer）の設定．"""

    k_context: int = Field(default=15, ge=1)
    aggregate: Literal["vote"] = "vote"
    # 1 台の専門家への回答の要求の打ち切り時間（秒）．専門家の Ollama は要求を順番に処理するので，
    # 質問者が並列に送ると待ち時間が積み重なる（生成 1 回の上限 llm.timeout_s とは別に持つ）
    timeout_s: float = 1800.0


class GgufModel(_Strict):
    """llama-bench で測る GGUF ファイル．"""

    name: str
    repo: str
    file: str


class E0Config(_Strict):
    """E0（実機の性能実測）の設定．"""

    hosts: list[str]
    iperf_pairs: list[tuple[str, str]]
    gguf_models: list[GgufModel]
    faiss_vectors: list[int]
    faiss_queries: int = 20


class ClusterConfig(_Strict):
    """実機の構成．各ノードは制御点（control）から SSH で操作する．"""

    control: str
    ssh_user: str
    remote_dir: str
    data_dir: str
    registry_port: int
    node_port: int
    requester: str
    expert_hosts: list[str]
    shard_budget_gb: float = Field(gt=0)
    gpu_workers: list[str] = Field(default_factory=list)
    release_hugepages: bool = True


class MedragDataConfig(_Strict):
    """MIRAGE ＋ MedRAG コーパスの取得・前処理の設定．"""

    sources: list[str]
    article_encoder: str
    query_encoder: str
    embed_batch_size: int = 128
    # MedCPT の埋め込みの計算精度（文書・クエリとも．atrium.encoders.MedcptEncoder の説明を参照）
    embed_precision: Precision = "fp16_autocast"
    medrag_commit: str
    mirage_url: str
    statpearls_url: str
    descriptions: dict[str, str]


class Feb4ragDataConfig(_Strict):
    """FeB4RAG の取得・前処理の設定．"""

    sources: list[str]
    feb4rag_commit: str
    beir_hf_repo: str
    centroid_sample: int = 2000
    centroid_sample_overrides: dict[str, int] = Field(default_factory=dict)


class DataConfig(_Strict):
    """データセットごとの設定．"""

    medrag: MedragDataConfig
    feb4rag: Feb4ragDataConfig

    def sources_of(self, dataset: DatasetName) -> list[str]:
        """データセットのデータ源の一覧を，ルーターの one-hot と同じ順序で返す．"""
        if dataset == "medrag":
            return list(self.medrag.sources)
        return list(self.feb4rag.sources)


class AtriumConfig(_Strict):
    """config.yaml 全体．"""

    experiment: ExperimentConfig
    retrieval: RetrievalConfig
    routing: RoutingConfig
    llm: LlmConfig
    local_answer: LocalAnswerConfig
    e0: E0Config
    cluster: ClusterConfig
    data: DataConfig


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AtriumConfig:
    """config.yaml を読み込んで検証する．"""
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AtriumConfig.model_validate(raw)
