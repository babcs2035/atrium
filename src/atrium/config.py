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
    "centroid_probe",
    "device_probe",
    "term_probe",
    "hybrid_probe",
    # p0007 I4：質問者が全メールの 1 つの索引を持ち，ノードに問い合わせずに上位 k 通を取る（中央集約．atrium.routing.central）
    "central_local",
]
# snippet_return_small（B3）と delegate_gpu（B4）は p0004 で追加（RQ-B）．
# gold_context は p0006 の C0：検索せずに正解のメールを文脈として質問者が答える（評価専用．EnronQA のみ）
AnswerMode = Literal[
    "snippet_return",
    "local_answer",
    "retrieval_only",
    "snippet_return_small",
    "delegate_gpu",
    "gold_context",
    # p0007 I2：Router が選んだ上位 m 受信箱が，それぞれ自分の上位 k 通だけで答え，answer.aggregate で 1 つに統合する
    "owner_answer",
]
# owner_answer の回答の統合の規則（p0007 §6.2 A1）
AggregateName = Literal["max_score", "abstain", "agree", "llm_select"]
MergeName = Literal["score", "cross_encoder", "qrels_oracle"]
QueryEmbeddingMode = Literal["live", "cached"]
FusionName = Literal["none", "rrf"]
Precision = Literal["fp32", "fp16_autocast"]

DEFAULT_CONFIG_PATH = Path("config.yaml")


class _Strict(BaseModel):
    """未知のキーを誤記として拒否する基底クラス．"""

    model_config = ConfigDict(extra="forbid", frozen=True)


# 実機の churn（EQ7b）で使える方式．見えているデータ源の中から選ぶ公開情報の方式と flood_score
CHURN_ROUTINGS = frozenset(
    {"card_sim", "term_sketch", "centroid_sim", "multi_centroid", "flood_score", "oracle"}
)


class ChurnConfig(_Strict):
    """実機の専門家の参加・離脱の再生（p0004 EQ7b）．省略すると離脱は起こさず，質問者も 1 台の失敗で問を失敗にする（従来どおり）．

    質問を 1 問ずつ順に処理し，1 問 = 1 刻みとして，専門家ごとの稼働状態を 2 状態のマルコフ連鎖（atrium.churn_eval と同じ）で
    動かす．離脱中のノードは `/v1/admin/availability` で応答を拒否する（プロセスは生きている）．
    質問者のメンバー表は refresh_steps 刻みごとにだけ更新する．
    """

    availability: float = Field(gt=0, le=1)
    refresh_steps: int = Field(default=1, ge=1)
    # 時刻表の種の番号（atrium.churn_eval の rng と同じ [experiment.seed, schedule_seed, availability×100] で引く）
    schedule_seed: int = Field(default=0, ge=0)
    # 稼働中の平均の長さ（刻み）．atrium.churn_eval.MEAN_UP_STEPS と同じ
    mean_up_steps: int = Field(default=200, ge=2)


class ExperimentConfig(_Strict):
    """実験の種類と条件．"""

    kind: Literal["e0_measure", "e1_routing"]
    dataset: DatasetName
    routing: RoutingName
    answer_mode: AnswerMode
    question_limit: int | None = None
    # 流す質問の分割（p0007 A1）．test：questions.jsonl（従来）／dev：dev_questions.jsonl（設定の選択用．EnronQA のみ）．
    # question_limit は選んだ分割の中で各問題集（EnronQA は受信箱）の先頭からの数
    question_split: Literal["test", "dev"] = "test"
    parallel: int = Field(default=4, ge=1)
    seed: int = 12
    # 質問者の HTTP 接続の持ち方（Iteration 5）．shared：全ノードで 1 つの池（上限 512．従来の挙動）／
    # shared_unlimited：1 つの池のまま上限なし／per_node：ノードの origin ごとに別の池
    http_pool: Literal["shared", "shared_unlimited", "per_node"] = "shared"
    # 同じ質問者のデバイスの上で問を分けて同時に流すプロセスの数（Iteration 8．atrium.requester_procs）．
    # parallel は全プロセスの合計の同時の問の数で，各プロセスへほぼ均等に割る．1 は従来どおり 1 プロセス
    requester_procs: int = Field(default=1, ge=1)
    # 専門家ノード 1 台を動かすプロセス（uvicorn のサーバー）の数（Iteration 10．atrium.node）．2 以上では各プロセスが
    # シャードを読み込み，SO_REUSEPORT で同じポートを開く（メモリはシャードの W 倍）．1 は従来どおり 1 プロセス
    node_workers: int = Field(default=1, ge=1)
    # probe の本文の作り方（Iteration 11）．per_request：ノードごとに ProbeRequest → model_dump → httpx の json= で作る（従来の挙動）／
    # pre_encoded：問ごと（検索器ごと）に埋め込みの JSON を 1 回だけ作り，ノードごとに shard_ids だけを差し替えた bytes を送る．
    # ノードが受け取る本文はバイト単位で同じ
    probe_body: Literal["per_request", "pre_encoded"] = "per_request"
    # sniffio の import の扱い（Iteration 12）．search：今の挙動（httpcore 1.0.9 が要求のたびに import sniffio を試み，無ければ sys.path を探す）／
    # blocked：sniffio が入っていないときだけ質問者のプロセスの始めに sys.modules["sniffio"] = None を置いて探索を省く（httpcore は同じ ImportError の枝で asyncio とみなす）
    sniffio_lookup: Literal["search", "blocked"] = "search"
    # httpcore の接続の池の割り当て（Iteration 13）．stock：今の挙動／lean：質問者のプロセスの始めに httpcore 1.0.9 の
    # AsyncConnectionPool._assign_requests_to_connections の掃除を 1 回の走査にし，待機中の接続の生存の確かめを生のソケットへ直接問う（挙動は同じ．版が違えば止まる）
    pool_assign: Literal["stock", "lean"] = "stock"
    # /v1/retrieve の本文の作り方（Iteration 14）．per_request：シャードごとに RetrieveRequest → model_dump → httpx の json= で作る（従来）／
    # pre_encoded：問ごと（検索器ごと）の埋め込みの JSON を 1 回だけ作り（probe_body: pre_encoded なら probe と共有），faiss のシャードの本文を
    # bytes の連結で作って送る．ノードが受け取る本文はバイト単位で同じ
    retrieve_body: Literal["per_request", "pre_encoded"] = "per_request"
    # EQ7b．指定すると質問を 1 問ずつ順に流し，離脱したノードがあっても返事のあった分で続ける
    churn: ChurnConfig | None = None

    @model_validator(mode="after")
    def _check_requester_procs(self) -> ExperimentConfig:
        """各プロセスが 1 問以上を同時に持てるよう parallel ≥ requester_procs とし，churn（1 問ずつ順に流す）は
        質問者・ノードとも 1 プロセスのときだけ使える．"""
        if self.parallel < self.requester_procs:
            raise ValueError("experiment.parallel must be >= experiment.requester_procs")
        if self.churn is not None and self.requester_procs != 1:
            raise ValueError("experiment.churn requires experiment.requester_procs = 1")
        # 離脱の状態（/v1/admin/availability）はプロセスごとに持つので，複数のプロセスのノードでは全体を切り替えられない
        if self.churn is not None and self.node_workers != 1:
            raise ValueError("experiment.churn requires experiment.node_workers = 1")
        return self

    @model_validator(mode="after")
    def _check_question_split(self) -> ExperimentConfig:
        """dev の質問は EnronQA だけが持つ．churn は test の全問を再生するので dev とは併用できない．"""
        if self.question_split == "dev" and (self.dataset != "enronqa" or self.churn is not None):
            raise ValueError("experiment.question_split=dev requires dataset=enronqa and no churn")
        return self

    @model_validator(mode="after")
    def _check_gold_context(self) -> ExperimentConfig:
        """gold_context は正解のメールの本文を質問者が持つ評価専用の条件で，EnronQA の churn なしの実行でだけ使える．"""
        if self.answer_mode != "gold_context":
            return self
        if self.dataset != "enronqa" or self.churn is not None:
            raise ValueError("answer_mode=gold_context requires dataset=enronqa and no churn")
        return self

    @model_validator(mode="after")
    def _check_p0007_conditions(self) -> ExperimentConfig:
        """owner_answer は自由記述の回答を統合するので EnronQA だけで使える．central_local は質問者の 1 つの索引から
        取った断片を質問者の LLM で答える（B1 相当）EnronQA の条件で，索引をプロセスごとに持たないよう質問者は 1 プロセスに限る．"""
        if self.answer_mode == "owner_answer" and self.dataset != "enronqa":
            raise ValueError("answer_mode=owner_answer requires dataset=enronqa")
        if self.routing == "central_local":
            if self.dataset != "enronqa" or self.answer_mode != "snippet_return":
                raise ValueError(
                    "routing=central_local requires dataset=enronqa and answer_mode=snippet_return"
                )
            if self.requester_procs != 1 or self.churn is not None:
                raise ValueError(
                    "routing=central_local requires experiment.requester_procs = 1 and no churn"
                )
        return self

    @model_validator(mode="after")
    def _check_churn_conditions(self) -> ExperimentConfig:
        """churn は EnronQA の検索だけの実験で，質問を 1 問ずつ流すときにだけ使える（1 問 = 1 刻みを保つため）．"""
        if self.churn is None:
            return self
        if self.dataset != "enronqa" or self.answer_mode != "retrieval_only" or self.parallel != 1:
            raise ValueError(
                "experiment.churn requires dataset=enronqa, answer_mode=retrieval_only and parallel=1"
            )
        # 見えているデータ源の中から選べる方式だけ（ragroute・random は全データ源の並びを前提にする）
        if self.routing not in CHURN_ROUTINGS:
            raise ValueError(f"experiment.churn supports routing in {sorted(CHURN_ROUTINGS)}")
        return self


class RetrievalConfig(_Strict):
    """検索と統合の条件．"""

    k_ret: int = Field(default=50, ge=1)
    k_rerank: int = Field(default=15, ge=1)
    merge: MergeName = "score"
    query_embedding: dict[DatasetName, QueryEmbeddingMode]
    # p0007 A2・I3：受信箱（シャード）の中の検索．none：密ベクトルの内積の上位 k 通（従来）／rrf：BM25 と密ベクトルの RRF（k = 60）の上位 k 通．
    # routing=central_local では全体の BM25 と密ベクトルの RRF（中央にも同じ改良を入れる）．EnronQA の faiss のシャードだけで使える
    fusion: FusionName = "none"
    # rrf で順位を付ける，密ベクトル・BM25 それぞれの上位の件数
    fusion_depth: int = Field(default=50, ge=1)


class RoutingConfig(_Strict):
    """ルーティング方式ごとのパラメータ．"""

    random_k: dict[DatasetName, int]
    ragroute_threshold: float = 0.5
    # 公開情報を使う方式と flood_score が問い合わせるデータ源の数（p0004．dev で選ぶ）
    top_m: dict[DatasetName, int] = Field(default_factory=dict)
    # centroid_probe が probe を送る候補の数 K（multi_centroid の上位 K 個．dev で事前登録の規則で選ぶ）
    probe_k: dict[DatasetName, int] = Field(default_factory=dict)
    # device_probe が probe を送るデバイスの数 D（multi_centroid の得点をデバイスごとに最大値でまとめた上位 D 台．
    # Iteration 7 の事前登録の規則で選ぶ）
    probe_d: dict[DatasetName, int] = Field(default_factory=dict)
    # term_probe・hybrid_probe（p0006 P4）が probe を送るデバイスの数 D．P2 の dev の規則で選んだ値
    term_probe_d: dict[DatasetName, int] = Field(default_factory=dict)
    hybrid_probe_d: dict[DatasetName, int] = Field(default_factory=dict)
    # hybrid_probe の併用の重み（本文の重心側．P2 の格子で最良の 0.5）
    summary_weight: float = Field(default=0.5, gt=0)


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


class AnswerConfig(_Strict):
    """answer_mode=owner_answer（p0007 I2）の設定．他の answer_mode では使われない（既定のままで従来の挙動）．"""

    # 候補にする受信箱の数 m．Router がこの数だけ選ぶ（routing.top_m の代わりに使う．選ぶ数を持たない oracle・all などは従来どおり）
    owner_m: int = Field(default=2, ge=1)
    # 候補の回答の統合の規則．max_score：検索の最高得点の候補／abstain：UNKNOWN を許し，UNKNOWN でない回答のうち得点最高
    # （全て UNKNOWN なら max_score）／agree：正規化した文字列の一致の票数（同票は得点）／llm_select：質問者の LLM が 1 つを選ぶ
    aggregate: AggregateName = "max_score"


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
    # 自由記述の回答の採点（p0004 §8.6）．判定モデルは質問者の llama3.1:8b と別系統にして自己贔屓を避ける．
    # 参照モデルは判定モデルとの一致（Cohen の κ）を測る大きいモデル（12 GB の GPU に載る範囲）
    judge_model: str = "qwen3:8b"
    judge_reference_model: str = "qwen3:14b"
    judge_validate_n: int = Field(default=500, ge=1)
    judge_kappa_n: int = Field(default=300, ge=1)


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
    # p0007 で追加．既存の config.yaml がそのまま読めるよう省略できる
    answer: AnswerConfig = Field(default_factory=AnswerConfig)
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
