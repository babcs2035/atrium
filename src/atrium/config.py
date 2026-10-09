"""config.yaml の型定義と読み込み．

全ての処理は `load_config()` が返す `AtriumConfig` だけを参照し，設定値を個別に環境変数などで
上書きしない（実験条件を config.yaml の差分だけで追跡できるようにするため）．
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DatasetName = Literal["medrag", "feb4rag", "enronqa"]
# oracle〜multi_centroid は p0004 で追加（RQ-A．atrium.routing.advert）
RoutingName = Literal[
    "ragroute",
    "all",
    "random",
    "none",
    "oracle",
    "flood_score",
    "card_sim",
    "term_sketch",
    "centroid_sim",
    "multi_centroid",
]
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
    # 公開情報を使う方式と flood_score が問い合わせるデータ源の数（p0004．dev で選ぶ）
    top_m: dict[DatasetName, int] = Field(default_factory=dict)


class LlmConfig(_Strict):
    """Ollama で動かす LLM の設定．"""

    ollama_version: str
    requester_model: str
    expert_model: str
    # GPU を持つ専門家（p0004 の wafl500〜509）が宿る型で使う LLM．省略すると expert_model と同じ
    expert_model_gpu: str | None = None
    num_predict: int = 2048
    num_ctx: int = 16384
    think: bool = False
    # 0 で貪欲な復号にする（同じ入力に同じ回答を返し，方式間を対応ありの検定で比べられるようにする）
    temperature: float = 0.0
    # 質問者の Ollama が同時に処理する要求の数（OLLAMA_NUM_PARALLEL）．8B と文脈長 16384 では，
    # 2 件分の KV キャッシュまでが 12 GB の GPU に収まる
    requester_num_parallel: int = Field(default=2, ge=1)
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


class DeviceConfig(_Strict):
    """実験で使うデバイス 1 台．役割は，このデバイスを置いたリスト（requester / experts / gpu_workers）で決まる．"""

    host: str
    # 省略時は cluster.ssh_user
    ssh_user: str | None = None


class ClusterConfig(_Strict):
    """実機の構成．各ノードは制御点（control）から SSH で操作する．1 台のデバイスが同時に持つ役割は 1 つだけである．"""

    control: str
    ssh_user: str
    remote_dir: str
    data_dir: str
    registry_port: int
    node_port: int
    requester: DeviceConfig
    # 質問者の LLM（Ollama）を動かす GPU PC．省略すると質問者と同じホストで動かす．
    # 質問者の GPU で再ランク（bge-reranker-v2-m3）を動かすと，8B の Ollama と 12 GB の VRAM を取り合い，
    # Ollama の一部の層が CPU で動いて生成が約 4 倍遅くなった（2026-10-08 の s8）．別の GPU に分けると，
    # 同じモデル・同じ設定のまま全層を GPU に載せられる
    requester_llm: DeviceConfig | None = None
    experts: list[DeviceConfig] = Field(min_length=1)
    gpu_workers: list[DeviceConfig] = Field(default_factory=list)
    shard_budget_gb: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_directories_are_absolute(self) -> ClusterConfig:
        """remote_dir・data_dir は絶対パスに限る（rsync・compose・docker の bind mount が `~` や相対パスを解決しないため）．"""
        for name in ("remote_dir", "data_dir"):
            if not getattr(self, name).startswith("/"):
                raise ValueError(
                    f"cluster.{name} must be an absolute path: {getattr(self, name)!r}"
                )
        return self

    @model_validator(mode="after")
    def _check_one_role_per_device(self) -> ClusterConfig:
        """同じホストが同時に複数の役割（または同じ役割に重複）に現れたら拒否する．

        experts と gpu_workers の両方に置くことだけは許す．GPU PC は，データ準備（gpu_workers）と
        実験（experts）で時間を分けて使い，同時には持たないため（p0004 §8.1．ユーザーの承認 U2）．
        """
        experts = set(self.expert_hosts())
        # experts にもある GPU PC は，時間を分けて使うので gpu_workers の側では数えない
        workers_only = [h for h in self.gpu_worker_hosts() if h not in experts]
        llm = [self.requester_llm.host] if self.requester_llm is not None else []
        hosts = [self.requester.host, *llm, *self.expert_hosts(), *workers_only]
        repeated_workers = {
            h for h in self.gpu_worker_hosts() if self.gpu_worker_hosts().count(h) > 1
        }
        duplicated = sorted({h for h in hosts if hosts.count(h) > 1} | repeated_workers)
        if duplicated:
            raise ValueError(f"each device may have only one role; duplicated: {duplicated}")
        return self

    def devices(self) -> list[DeviceConfig]:
        """全デバイスを，質問者・質問者の LLM・専門家・埋め込みの分担の順に返す．"""
        llm = [self.requester_llm] if self.requester_llm is not None else []
        return [self.requester, *llm, *self.experts, *self.gpu_workers]

    def requester_llm_host(self) -> str:
        """質問者の LLM（Ollama）が動くホスト（requester_llm を省略したら質問者自身）．"""
        return (self.requester_llm or self.requester).host

    def expert_hosts(self) -> list[str]:
        """専門家のホストの一覧（config.yaml の順）．"""
        return [d.host for d in self.experts]

    def gpu_worker_hosts(self) -> list[str]:
        """データ準備で埋め込みを分担する GPU PC のホストの一覧．"""
        return [d.host for d in self.gpu_workers]

    def ssh_user_of(self, device: DeviceConfig) -> str:
        """デバイスへ SSH するユーザー（個別の指定が無ければ cluster.ssh_user）．"""
        return device.ssh_user or self.ssh_user


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


class EnronqaDataConfig(_Strict):
    """EnronQA（150 人の受信箱．p0004）の取得・前処理の設定．データ源は受信箱 1 個である．"""

    sources: list[str] = Field(min_length=1)
    hf_repo: str
    # 取得するリビジョンを固定して再現性を保つ
    hf_revision: str
    # メールとクエリの埋め込み（EnronQA の作成で使われた符号化器．クエリには検索用の接頭辞を付ける）
    encoder: str = "Snowflake/snowflake-arctic-embed-m-v1.5"
    embed_batch_size: int = Field(default=64, ge=1)
    # 評価に使うテスト質問の受信箱あたりの最大数（p0004 §8.7．20 問に満たない受信箱は全問）
    test_per_inbox: int = Field(default=20, ge=1)
    # 公開情報の大きさ（T・C・m）を選ぶための dev の質問と，中央の分類器（ragroute）の学習用の train の質問
    dev_per_inbox: int = Field(default=20, ge=1)
    train_per_inbox: int = Field(default=40, ge=1)
    # 他の受信箱にほぼ同じメールがある質問を主評価から除く Jaccard 類似度の閾値（p0004 §7.2 の V3）
    duplicate_jaccard: float = Field(default=0.9, gt=0, le=1)
    # 1 問の回答に使うメールの数（EnronQA の原論文と同じ 5．受信箱からの取得数と統合後の数の両方）
    k_context: int = Field(default=5, ge=1)
    # 所有者特定の評価（p0004 §8.5）で受信箱ごとに抜き出すメールの数
    owner_probe_per_inbox: int = Field(default=10, ge=1)
    # 公開情報の大きさ（dev で選ぶ．値の選び方は .claude/research/journal.md の事前登録に従う）
    card_subjects: int = Field(default=20, ge=1)
    term_sketch_size: int = Field(default=50, ge=1)
    n_centroids: int = Field(default=8, ge=1)
    # 中央の分類器（ragroute）の学習で，1 問あたりに使う関連の無い受信箱の数（150 個の全組は大きすぎるため）
    ragroute_negatives: int = Field(default=10, ge=1)


class DataConfig(_Strict):
    """データセットごとの設定．"""

    medrag: MedragDataConfig
    feb4rag: Feb4ragDataConfig
    # p0004 で追加．既存の config.yaml がそのまま読めるよう省略できる
    enronqa: EnronqaDataConfig | None = None

    def sources_of(self, dataset: DatasetName) -> list[str]:
        """データセットのデータ源の一覧を，ルーターの one-hot と同じ順序で返す．"""
        if dataset == "medrag":
            return list(self.medrag.sources)
        if dataset == "feb4rag":
            return list(self.feb4rag.sources)
        return list(self.require_enronqa().sources)

    def require_enronqa(self) -> EnronqaDataConfig:
        """data.enronqa を返す（無ければ設定の誤りとして止める）．"""
        if self.enronqa is None:
            raise ValueError("data.enronqa is required for dataset=enronqa")
        return self.enronqa


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

    @model_validator(mode="after")
    def _check_e0_hosts_are_experts(self) -> AtriumConfig:
        """E0 で測るホストと iperf の相手は，cluster.experts のデバイスでなければならない．"""
        experts = set(self.cluster.expert_hosts())
        named = [*self.e0.hosts, *(h for pair in self.e0.iperf_pairs for h in pair)]
        unknown = sorted({h for h in named if h not in experts})
        if unknown:
            raise ValueError(f"e0 hosts are not in cluster.experts: {unknown}")
        return self


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AtriumConfig:
    """config.yaml を読み込んで検証する．"""
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AtriumConfig.model_validate(raw)
