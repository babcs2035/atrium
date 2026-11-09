"""p0007 I7（plans/p0007 §6.5 E2）：途中で加わるデバイス（受信箱）が答えに加わるまでの時間を，P2P と中央で実機の壁時計で比べる（`atrium join-run`）．

質問者のコンテナ（deploy で `cluster.node_admin: true` にしたもの）で動く．150 受信箱のうち 15 受信箱（3 台分．ホストの無作為抽出，種 12）を最初は外し，途中で加える．

**既存の実装との両立と代替**（plans/p0007 §13 の未確認事項に対する記録）
- 既存の参加・離脱の再生（`churn_live`・EQ7b）は `experiment.churn` を必要とし，retrieval_only・parallel 1・CHURN_ROUTINGS（hybrid_probe を含まない）に限られ，
  メンバー表は `/v1/profile` の返事の有無で作る（要約の配布を持たない）．hybrid_probe の要約は `atrium build-summary` が全受信箱から一括して作った成果物で，
  デバイスが加わっても変わらない．そのため両立しない部分を次の代替にした．
  (1) 「外しておく」：ノードのプロセスは起動したまま，`/v1/admin/availability`（`cluster.node_admin` でも有効）で最初は離脱させ，参加の時刻に有効にする
  （ノードを新しく起動する代わり．起動・索引の読み込みの時間は含まない）．
  (2) 要約：質問者は最初は 27 台の公開情報だけで要約の成果物を作り（`device_summary.assemble_artifact`），加わったデバイスの公開情報
  （`/v1/admin/summary`．デバイスの CPU が自分のメールだけから作る）を受け取るたびに，CORI の重み（デバイス数・cf・長さの平均が変わる）を作り直して
  ルーターを差し替える（`Requester.admit`）．
  (3) 参加の通知の遅れ L_join（問）：質問の流れの中で，加わったデバイスの公開情報を質問者が使い始めるのを L 問遅らせる（L = 0 が主）．

**測るもの**
- P2P：参加の時刻 t0 から，(a) デバイスが有効になる，(b) 公開情報の作成（`/v1/admin/summary`．デバイスの CPU），(c) 質問者のメンバー表に載る（プロファイルの取得と要約の組み立て・ルーターの差し替えを含む），
  までの時間，(d) その受信箱の質問に答えられるまでの時間（その受信箱の test の質問を順に流し，正解のメールが上位 5 通に入った最初の質問）．公開情報の大きさ（バイト）．
- 中央：t0 から，その受信箱の全メールを持ち主のデバイスから取り出し（転送の大きさ・時間），中央が埋め込んで索引に足し，その受信箱の質問に答えられるまでの時間と，転送量．
- 判定（事前登録）：P2P が答えられるまでの時間（受信箱ごとの中央値）で中央より短い，または同等（中央の 1.25 倍以内）で転送量が小さい．

使い方（実機の本番の手順は docs/d0003_experiment_workflow.md の §7．制御点で `scripts/remote/join_run.sh`）:
    atrium --config config.yaml join-run --data-dir /data --placement placement.json --out-dir results/<run_id> \\
        --ollama-url http://ollama:11434 [--n-devices 3] [--delays 0,10,100]

出力：`<out>/join.json`・`verdict.json`（`verdict_original`）・`sim_vs_live.json`・`run_meta.json`．

**主構成での測り直し（d0009 A6 参加．Iteration 31．docs/d0003 §7.3）**：config が `routing: bm25_probe` なら P2P のルーターは `Bm25ProbeRouter`
（`Bm25Stats` はノードの公開情報 `bm25_len_total` などから質問者が組み立てる．`device_summary.assemble_bm25_stats`），
`answer_mode: owner_answer` なら「答えられた」は `is_owner_answered`（選んだ候補の文脈に正解のメール．R3′ の表は `--owner-table`），
`question_split` が test 以外なら質問はその分割の各受信箱の先頭 `--max-tries-per-inbox` 問（ファイルの順）．
`--central-mode c1_rrf_ce` なら中央は C1-RRF-CE（RRF の上位 `retrieval.k_ret` 通を cross-encoder の表 `--central-table` で並べ替えた上位 5 通 → 生成．
生成は `central_generation: gpu_nodes` の経路）で，索引への追加は別のスレッドで行う．`--delays ""` は通知の遅れの感度を流さない．
最後に，質問者が組み立てた `Bm25Stats` とオフラインの `summary/bm25_stats.npz` の差（G-A）を `sim_vs_live.json` に書く．
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.device_summary import (
    BODY_CENTROIDS,
    DeviceSummary,
    assemble_artifact,
    assemble_bm25_stats,
    published_bytes,
)
from atrium.dynamics import (
    DEFAULT_SEED,
    choose_names,
    durations_or_censored,
    judge_join,
    summarize_times,
)
from atrium.failure_run import FAILURE_OTHER, classify_failure
from atrium.live_common import (
    is_hit,
    is_owner_answered,
    load_split_questions,
    load_test_questions,
    write_json,
)
from atrium.manifest import NodeAssignment, Placement
from atrium.node import DocEmbedder
from atrium.protocol import (
    ExportDocsRequest,
    ExportDocsResponse,
    ShardProfile,
    SummaryRequest,
    SummaryResponse,
)
from atrium.requester import (
    RETRIEVE_TIMEOUT_S,
    CachedQueryEmbedder,
    Requester,
    TableCandidateScorer,
    TableReranker,
    discover_shards,
)
from atrium.routing import RoutingQuery, SourceProfile, build_source_profiles
from atrium.routing.advert import tokenize
from atrium.routing.central import CentralIndex
from atrium.routing.summary import Bm25ProbeRouter, SummaryProbeRouter
from atrium.verdict import write_verdict

logger = logging.getLogger(__name__)

RUN_META = "run_meta.json"
JOIN_FILE = "join.json"
SIM_VS_LIVE_FILE = "sim_vs_live.json"
DEFAULT_DELAYS = (0, 10, 100)
# 公開する語の数（`atrium build-summary` の TERM_SIZE と同じ）
TERM_SIZE = 64000
# 質問者が公開情報を作るデバイスの並行数の上限（最初のメンバー全員の要約を集める準備で使う）
SUMMARY_CONCURRENCY = 30
# 公開情報の作成の要求の打ち切り（秒）．デバイスの CPU で全メールの本文を埋め込むので数分かかる（実機の煙試験で 120 秒を超えた）
SUMMARY_TIMEOUT_S = 3600.0
CENTRAL_TOP_K = 5
# 試行の記録に残す質問者の時間の内訳（質問者の `timings` のキー）
TRY_TIMING_KEYS = ("route_s", "generate_s", "ce_s", "retrieve_s", "rerank_s")
# 中央の測定の方式：索引の上位 5 通に正解（p0007 E2．既定）／C1-RRF-CE（再ランクと生成まで通す．d0009 A6 参加）
CENTRAL_MODE_RETRIEVAL = "retrieval_top"
CENTRAL_MODE_C1_RRF_CE = "c1_rrf_ce"
# 参加の前の確かめで同時に流す質問の数の上限（owner_answer は 1 問が 27 回の要求になるので絞る．従来は全問を同時）
PRE_JOIN_PARALLEL_OWNER = 8
# 表に無い組の KeyError は，メッセージが（質問，文書 ID）の組の表記で始まる（他の KeyError と分けるため）
TABLE_MISS_PREFIX = "KeyError: ("
FAILURE_TABLE_MISS = "table_miss"
# G-A（公開情報の組み立て）の許容：長さの平均の相対差
STATS_REL_TOLERANCE = 1e-6
# 候補の一致を調べる質問の数：test は先頭 300 問（従来），それ以外は受信箱ごとの先頭 AGREEMENT_PER_INBOX 問
AGREEMENT_TEST_QUESTIONS = 300
AGREEMENT_PER_INBOX = 2


@dataclass(frozen=True)
class JoinParams:
    """測定の条件（コマンドの引数）．"""

    n_devices: int = 3
    delays: tuple[int, ...] = DEFAULT_DELAYS
    # 感度の質問の流れの長さの上限（None なら加わる受信箱の全質問と，同じ数の他の受信箱の質問）
    stream_questions: int | None = None
    # 1 つの受信箱で，答えられるまで質問を試す最大の数
    max_tries_per_inbox: int = 50
    seed: int = DEFAULT_SEED
    # 動作確認用（本番は None）．最初のメンバーを先頭から数台だけにする／公開情報に使うメールを受信箱ごとに先頭から数通だけにする
    initial_devices: int | None = None
    summary_max_docs: int | None = None
    # 中央の測定の方式（CENTRAL_MODE_*）と，cross-encoder の表（None なら data ディレクトリの既定の表）
    central_mode: str = CENTRAL_MODE_RETRIEVAL
    owner_table: Path | None = None
    central_table: Path | None = None


@dataclass(frozen=True)
class JoinPlan:
    """最初は外す（途中で加える）デバイスと，最初のメンバーの配置．"""

    joiner_hosts: list[str]
    joiner_inboxes: list[str]
    initial_placement: Placement
    initial_inboxes: list[str]


def plan_join(
    placement: Placement,
    source_order: Sequence[str],
    n_devices: int,
    seed: int,
    initial_devices: int | None = None,
) -> JoinPlan:
    """加えるデバイスをホストの無作為抽出（種つき）で決め，最初のメンバーの配置（残りのノード）と受信箱を求める．

    受信箱の並びは source_order（config の data.enronqa.sources）の順．全てのデバイスが 1 つ以上のシャードを持つ前提．
    """
    joiners = choose_names([n.host for n in placement.nodes], n_devices, seed)
    shard_of = {sid for n in placement.nodes if n.host in joiners for sid in n.shard_ids}
    initial_nodes = [n for n in placement.nodes if n.host not in joiners]
    if initial_devices is not None:
        initial_nodes = initial_nodes[:initial_devices]
    initial_shards = {sid for n in initial_nodes for sid in n.shard_ids}
    return JoinPlan(
        joiner_hosts=joiners,
        joiner_inboxes=[s for s in source_order if s in shard_of],
        initial_placement=Placement(
            dataset=placement.dataset, nodes=initial_nodes, delegate_of=placement.delegate_of
        ),
        initial_inboxes=[s for s in source_order if s in initial_shards],
    )


def build_stream(
    questions: Sequence[Question],
    joiner_inboxes: Sequence[str],
    limit: int | None,
    seed: int,
) -> list[Question]:
    """参加の通知の遅れの感度で使う質問の流れ：加わる受信箱の全質問と，同じ数の他の受信箱の質問を，種つきで混ぜた並び．limit があれば先頭から limit 問．"""
    joiner_set = set(joiner_inboxes)
    joiner = [q for q in questions if q.bank in joiner_set]
    others = [q for q in questions if q.bank not in joiner_set]
    rng = random.Random(seed)
    chosen = rng.sample(others, min(len(joiner), len(others)))
    stream = joiner + chosen
    rng.shuffle(stream)
    return stream if limit is None else stream[:limit]


@dataclass
class MemberState:
    """質問者のメンバー表と，集めた公開情報（デバイスの URL → 公開情報）．"""

    shards: list[ShardProfile] = field(default_factory=list)
    summaries: dict[str, DeviceSummary] = field(default_factory=dict)
    nodes: list[NodeAssignment] = field(default_factory=list)


@dataclass
class JoinContext:
    """測定に使うもの．"""

    cfg: AtriumConfig
    requester: Requester
    client: httpx.AsyncClient
    full_placement: Placement
    plan: JoinPlan
    questions: list[Question]
    gold_doc_of: dict[str, str]
    source_order: list[str]
    encoder_name: str
    state: MemberState = field(default_factory=MemberState)
    admit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # 「答えられた」の判定（既定は top_doc_ids．owner_answer は選んだ候補の文脈）
    answered: Callable[[Mapping[str, Any], str], bool] = is_hit
    # 受信箱の質問の並び：既定は質問 ID の順（test）．False ならファイルの順（H など．先頭の問から試す）
    sort_questions_by_qid: bool = True
    # 中央の測定が c1_rrf_ce のときの，中央の質問者（再ランクと生成まで通す）
    central_requester: Requester | None = None

    @property
    def url_of_host(self) -> dict[str, str]:
        """ホスト → ノードの URL．"""
        return {n.host: n.url for n in self.full_placement.nodes}

    def node(self, host: str) -> NodeAssignment:
        """ホストのノード．"""
        return next(n for n in self.full_placement.nodes if n.host == host)

    def questions_of(self, inbox: str) -> list[Question]:
        """受信箱の質問（既定は質問 ID の順．sort_questions_by_qid が False ならファイルの順）．"""
        inbox_questions = [q for q in self.questions if q.bank == inbox]
        return (
            sorted(inbox_questions, key=lambda q: q.qid)
            if self.sort_questions_by_qid
            else inbox_questions
        )


# ── 公開情報の取得と，メンバー表の更新 ────────────────────────────────────────


def summary_from_response(response: SummaryResponse) -> DeviceSummary:
    """デバイスの応答を `DeviceSummary` にする．"""
    return DeviceSummary(
        terms=response.terms,
        dfs=response.dfs,
        total_terms=response.total_terms,
        n_docs=response.n_docs,
        centers={
            i.inbox: np.asarray(i.centers, dtype=np.float32).reshape(len(i.centers), -1)
            for i in response.inboxes
        },
        tokenize_s=response.tokenize_s,
        embed_s=response.embed_s,
        kmeans_s=response.kmeans_s,
        bm25_len_total=response.bm25_len_total,
    )


async def fetch_summary(
    ctx: JoinContext, host: str, max_docs: int | None = None
) -> tuple[DeviceSummary, dict[str, Any]]:
    """デバイスに公開情報を作らせて受け取る．（公開情報，所要時間・大きさの記録）を返す．max_docs は動作確認用．"""
    request = SummaryRequest(
        term_size=TERM_SIZE,
        n_centroids=BODY_CENTROIDS,
        seed=ctx.cfg.experiment.seed,
        max_docs_per_inbox=max_docs,
    )
    begin = time.perf_counter()
    response = await ctx.client.post(
        f"{ctx.url_of_host[host]}/v1/admin/summary",
        json=request.model_dump(),
        timeout=SUMMARY_TIMEOUT_S,
    )
    response.raise_for_status()
    rtt_s = time.perf_counter() - begin
    parsed = SummaryResponse.model_validate_json(response.content)
    summary = summary_from_response(parsed)
    return summary, {
        "host": host,
        "rtt_s": rtt_s,
        "device_total_s": parsed.total_s,
        "device_tokenize_s": parsed.tokenize_s,
        "device_embed_s": parsed.embed_s,
        "device_kmeans_s": parsed.kmeans_s,
        "device": parsed.device,
        "n_docs": parsed.n_docs,
        "payload_bytes": len(response.content),
        "published_bytes": published_bytes(summary),
    }


def make_router_and_sources(
    ctx: JoinContext, state: MemberState
) -> tuple[Placement, list[SourceProfile], SummaryProbeRouter]:
    """今のメンバー（state）から，配置・データ源・ルーター（公開情報から組み立てた要約）を作る．

    ルーターは既定が hybrid_probe．config が routing=bm25_probe なら Bm25ProbeRouter（`Bm25Stats` も公開情報から組み立てる．m は answer.owner_m）．
    """
    cfg = ctx.cfg
    member_inboxes = {sid for n in state.nodes for sid in n.shard_ids}
    sources = build_source_profiles(
        state.shards, [s for s in ctx.source_order if s in member_inboxes]
    )
    names = [s.source for s in sources]
    url_of_shard = ctx.full_placement.url_of_shard()
    device_of_source = {s.source: url_of_shard[s.shard_ids[0]] for s in sources}
    artifact = assemble_artifact(names, device_of_source, state.summaries, TERM_SIZE)
    router: SummaryProbeRouter
    if cfg.experiment.routing == "bm25_probe":
        if cfg.routing.owner_score != "fused_max":
            raise ValueError("join-run supports only routing.owner_score=fused_max")
        stats = assemble_bm25_stats(artifact, device_of_source, state.summaries)
        router = Bm25ProbeRouter(
            cfg.answer.owner_m
            if cfg.experiment.answer_mode == "owner_answer"
            else cfg.routing.top_m["enronqa"],
            cfg.routing.bm25_probe_d["enronqa"],
            dataclasses.replace(artifact, bm25=stats),
            ctx.encoder_name,
            cfg.routing.summary_weight,
            cfg.routing.bm25_weight,
            cfg.routing.owner_score,
            cfg.routing.owner_score_agg,
            cfg.routing.owner_score_k,
        )
    else:
        router = SummaryProbeRouter(
            "hybrid",
            cfg.routing.top_m["enronqa"],
            cfg.routing.hybrid_probe_d["enronqa"],
            artifact,
            ctx.encoder_name,
            cfg.routing.summary_weight,
        )
    placement = Placement(
        dataset=ctx.full_placement.dataset,
        nodes=list(state.nodes),
        delegate_of=ctx.full_placement.delegate_of,
    )
    return placement, sources, router


async def admit_device(ctx: JoinContext, host: str, summary: DeviceSummary) -> tuple[float, float]:
    """加わったデバイスの公開情報とプロファイルを受け取り，メンバー表に載せる（ルーターを作り直して質問者に差し替える）．

    （プロファイルの取得の秒数，要約の組み立てとルーターの差し替えの秒数）を返す．並行する参加の更新は 1 つずつ行う．
    """
    node = ctx.node(host)
    begin = time.perf_counter()
    shards = await discover_shards(
        ctx.client,
        Placement(dataset=ctx.full_placement.dataset, nodes=[node]),
    )
    profile_s = time.perf_counter() - begin
    async with ctx.admit_lock:
        begin = time.perf_counter()
        ctx.state.shards.extend(shards)
        ctx.state.nodes.append(node)
        ctx.state.summaries[node.url] = summary
        placement, sources, router = await asyncio.to_thread(
            make_router_and_sources, ctx, ctx.state
        )
        ctx.requester.admit(placement, sources, router)
    return profile_s, time.perf_counter() - begin


async def collect_initial_members(ctx: JoinContext, params: JoinParams) -> dict[str, Any]:
    """最初のメンバー（加わらないデバイス）の公開情報とプロファイルを集め，質問者のメンバー表を作る（準備．時間は主の測定に含めない）．"""
    semaphore = asyncio.Semaphore(SUMMARY_CONCURRENCY)
    hosts = [n.host for n in ctx.plan.initial_placement.nodes]

    async def one(host: str) -> tuple[DeviceSummary, dict[str, Any]]:
        async with semaphore:
            return await fetch_summary(ctx, host, params.summary_max_docs)

    fetched = await asyncio.gather(*(one(h) for h in hosts))
    shards = await discover_shards(ctx.client, ctx.plan.initial_placement)
    ctx.state = MemberState(
        shards=list(shards),
        summaries={ctx.node(h).url: s for h, (s, _) in zip(hosts, fetched, strict=True)},
        nodes=list(ctx.plan.initial_placement.nodes),
    )
    placement, sources, router = await asyncio.to_thread(make_router_and_sources, ctx, ctx.state)
    ctx.requester.admit(placement, sources, router)
    return {
        "n_devices": len(hosts),
        "device_total_s_p50": float(np.median([m["device_total_s"] for _, m in fetched])),
        "published_bytes_total": int(sum(m["published_bytes"] for _, m in fetched)),
    }


# ── 参加の測定 ────────────────────────────────────────────────────────────────


async def set_available(ctx: JoinContext, hosts: Sequence[str], up: bool) -> None:
    """デバイスを有効・離脱にする（/v1/admin/availability）．"""

    async def one(host: str) -> None:
        response = await ctx.client.post(
            f"{ctx.url_of_host[host]}/v1/admin/availability",
            json={"up": up},
            timeout=RETRIEVE_TIMEOUT_S,
        )
        response.raise_for_status()

    await asyncio.gather(*(one(h) for h in hosts))


def failure_kind_of(error: str | None) -> str | None:
    """質問者の結果の `error` を失敗の形（table_miss・http・other．失敗していなければ None）に分ける．

    表に無い組の KeyError は `KeyError: ('<質問>', '<文書 ID>')` の形で，委託先が見つからない KeyError など他の KeyError と区別する
    （`classify_failure` は KeyError を全て table_miss とするので，ここで先に分ける）．
    """
    if error is None:
        return None
    if error.startswith("KeyError"):
        return FAILURE_TABLE_MISS if error.startswith(TABLE_MISS_PREFIX) else FAILURE_OTHER
    return classify_failure(error)[0]


def try_row(
    record: Mapping[str, Any], answered: bool, qid: str, finished_s: float
) -> dict[str, Any]:
    """1 回の試行の記録（質問の本文は残さない）．finished_s は参加の時刻からその試行が終わった時刻までの秒数．"""
    timings = record.get("timings", {})
    return {
        "qid": qid,
        "answered": answered,
        "finished_s": finished_s,
        "e2e_s": timings.get("e2e_s"),
        "failure_kind": failure_kind_of(record.get("error")),
        "timings": {k: timings[k] for k in TRY_TIMING_KEYS if k in timings},
    }


async def first_answer_p2p(
    ctx: JoinContext, inbox: str, origin: float, max_tries: int
) -> dict[str, Any]:
    """受信箱の質問を順に流し，「答えられた」最初の質問が終わるまでの時間（origin = perf_counter の参加の時刻から）．

    「答えられた」は ctx.answered（既定は正解のメールが上位 5 通．owner_answer は選んだ候補の文脈に正解）．
    """
    rows: list[dict[str, Any]] = []
    for question in ctx.questions_of(inbox)[:max_tries]:
        record = await ctx.requester.process(question)
        ok = ctx.answered(record, ctx.gold_doc_of[question.qid])
        finished_s = time.perf_counter() - origin
        rows.append(try_row(record, ok, question.qid, finished_s))
        if ok:
            return {"inbox": inbox, "answer_s": finished_s, "tries": len(rows), "rows": rows}
    return {"inbox": inbox, "answer_s": None, "tries": len(rows), "rows": rows}


async def measure_p2p_join(ctx: JoinContext, params: JoinParams) -> dict[str, Any]:
    """加わるデバイスを一斉に有効にし，公開情報の作成・メンバー表への掲載・その受信箱の質問に答えられるまでを測る（L_join = 0）．"""
    origin = time.perf_counter()

    async def join_one(host: str) -> dict[str, Any]:
        await set_available(ctx, [host], True)
        up_s = time.perf_counter() - origin
        summary, cost = await fetch_summary(ctx, host, params.summary_max_docs)
        published_s = time.perf_counter() - origin
        profile_s, assemble_s = await admit_device(ctx, host, summary)
        member_s = time.perf_counter() - origin
        answers = await asyncio.gather(
            *(
                first_answer_p2p(ctx, inbox, origin, params.max_tries_per_inbox)
                for inbox in ctx.node(host).shard_ids
            )
        )
        return {
            **cost,
            "up_s": up_s,
            "published_s": published_s,
            "profile_s": profile_s,
            "assemble_s": assemble_s,
            "member_s": member_s,
            "answers": answers,
        }

    devices = await asyncio.gather(*(join_one(h) for h in ctx.plan.joiner_hosts))
    inbox_times = [a["answer_s"] for d in devices for a in d["answers"]]
    return {
        "devices": devices,
        "answer_s": inbox_times,
        "answer_summary": summarize_times(durations_or_censored(inbox_times)),
        "member_s_summary": summarize_times([d["member_s"] for d in devices]),
        "payload_bytes": int(sum(d["payload_bytes"] for d in devices)),
        "published_bytes": int(sum(d["published_bytes"] for d in devices)),
        "exposed_mails": 0,
    }


async def measure_central_join(
    ctx: JoinContext,
    central: CentralIndex,
    embedder: DocEmbedder,
    params: JoinParams,
) -> dict[str, Any]:
    """加わる受信箱の全メールを持ち主のデバイスから取り出し，中央が埋め込んで索引に足し，その受信箱の質問に答えられるまでを測る．

    デバイスごとに（取り出し → 埋め込み → 追加 → 質問）を並行して流す．中央の埋め込みと索引への追加は同時に 1 つだけ行う．
    central_mode が c1_rrf_ce なら，質問は中央の質問者（再ランクと生成まで通す）で流し，「答えられた」は `is_hit`（上位 5 通に正解・失敗なし），
    索引への追加（BM25 の作り直し）は別のスレッドで行う（他のデバイスの質問を止めないため．検索は追加前の索引で答える）．
    """
    full_path = params.central_mode == CENTRAL_MODE_C1_RRF_CE
    origin = time.perf_counter()
    central_lock = asyncio.Lock()
    k = ctx.cfg.data.require_enronqa().k_context

    async def export(host: str, inbox: str) -> tuple[ExportDocsResponse, int]:
        response = await ctx.client.post(
            f"{ctx.url_of_host[host]}/v1/admin/docs/export",
            json=ExportDocsRequest(shard_id=inbox, doc_ids=None).model_dump(),
            timeout=RETRIEVE_TIMEOUT_S,
        )
        response.raise_for_status()
        return ExportDocsResponse.model_validate_json(response.content), len(response.content)

    def hit(question: Question) -> bool:
        vector = np.asarray(ctx.requester.embedder.embed(question)[ctx.encoder_name], np.float32)
        text = question.question if ctx.cfg.retrieval.fusion == "rrf" else None
        found = {central.doc_ids[i] for i in central.search_ids(vector, k, text)}
        return ctx.gold_doc_of[question.qid] in found

    async def join_one(host: str) -> dict[str, Any]:
        inboxes = ctx.node(host).shard_ids
        begin = time.perf_counter()
        exported = await asyncio.gather(*(export(host, i) for i in inboxes))
        transfer_s = time.perf_counter() - begin
        docs = [d for resp, _ in exported for d in resp.docs]
        async with central_lock:
            begin = time.perf_counter()
            emb = await asyncio.to_thread(embedder.encode_texts, [d.content for d in docs])
            embed_s = time.perf_counter() - begin
            ids, titles, contents = (
                [d.doc_id for d in docs],
                [d.title for d in docs],
                [d.content for d in docs],
            )
            if full_path:
                add_s = await asyncio.to_thread(central.append, ids, titles, contents, emb)
            else:
                add_s = central.append(ids, titles, contents, emb)
            bm25_s = central.bm25_s
        indexed_s = time.perf_counter() - origin
        answers: list[dict[str, Any]] = []
        for inbox in inboxes:
            tries = 0
            answered: float | None = None
            rows: list[dict[str, Any]] = []
            for question in ctx.questions_of(inbox)[: params.max_tries_per_inbox]:
                tries += 1
                if full_path:
                    if ctx.central_requester is None:
                        raise RuntimeError("central_mode=c1_rrf_ce requires a central requester")
                    record = await ctx.central_requester.process(question)
                    ok = is_hit(record, ctx.gold_doc_of[question.qid])
                    rows.append(try_row(record, ok, question.qid, time.perf_counter() - origin))
                else:
                    ok = hit(question)
                if ok:
                    answered = time.perf_counter() - origin
                    break
            answers.append({"inbox": inbox, "answer_s": answered, "tries": tries, "rows": rows})
        return {
            "host": host,
            "n_mails": len(docs),
            "transfer_bytes": sum(size for _, size in exported),
            "transfer_s": transfer_s,
            "embed_s": embed_s,
            "add_s": add_s,
            "bm25_s": bm25_s,
            "indexed_s": indexed_s,
            "answers": answers,
        }

    devices = await asyncio.gather(*(join_one(h) for h in ctx.plan.joiner_hosts))
    inbox_times = [a["answer_s"] for d in devices for a in d["answers"]]
    return {
        "devices": devices,
        "answer_s": inbox_times,
        "answer_summary": summarize_times(durations_or_censored(inbox_times)),
        "transfer_bytes": int(sum(d["transfer_bytes"] for d in devices)),
        "exposed_mails": int(sum(d["n_mails"] for d in devices)),
    }


async def measure_delay(
    ctx: JoinContext,
    delay_questions: int,
    stream: Sequence[Question],
    initial: tuple[Placement, list[SourceProfile], SummaryProbeRouter],
    final: tuple[Placement, list[SourceProfile], SummaryProbeRouter],
) -> dict[str, Any]:
    """参加の通知が delay_questions 問だけ遅れるとき（質問者が加わったデバイスの公開情報を使い始めるのが遅れる）の，加わった受信箱の質問への答え方．

    質問の流れの先頭を参加の時刻とし，delay_questions 問目から最終のメンバー表（final）に切り替える．それまでは最初のメンバー表（initial）で答える．
    """
    ctx.requester.admit(*initial)
    joiner_set = set(ctx.plan.joiner_inboxes)
    first_hit: dict[str, int] = {}
    joiner_before = hits_before = joiner_after = hits_after = 0
    began = time.perf_counter()
    first_hit_s: dict[str, float] = {}
    for position, question in enumerate(stream):
        if position == delay_questions:
            ctx.requester.admit(*final)
        record = await ctx.requester.process(question)
        if question.bank not in joiner_set:
            continue
        ok = ctx.answered(record, ctx.gold_doc_of[question.qid])
        if position < delay_questions:
            joiner_before += 1
            hits_before += ok
        else:
            joiner_after += 1
            hits_after += ok
        if ok and question.bank not in first_hit:
            first_hit[question.bank] = position
            first_hit_s[question.bank] = time.perf_counter() - began
    if delay_questions >= len(stream):
        ctx.requester.admit(*final)
    return {
        "delay_questions": delay_questions,
        "n_stream": len(stream),
        "joiner_questions_before_notice": joiner_before,
        "joiner_hits_before_notice": hits_before,
        "joiner_questions_after_notice": joiner_after,
        "joiner_hits_after_notice": hits_after,
        "hit_rate_after_notice": hits_after / joiner_after if joiner_after else None,
        "first_hit_position": first_hit,
        "first_hit_s": first_hit_s,
        "n_inboxes_never_answered": len(joiner_set) - len(first_hit),
    }


async def pre_join_hit_rate(ctx: JoinContext, parallel: int | None = None) -> dict[str, Any]:
    """参加の前に，加わる受信箱の質問へ答えられるか（メンバーに入っていないので答えられない見込み）．parallel は同時に流す質問の数（None なら全問）．"""
    joiner_set = set(ctx.plan.joiner_inboxes)
    asked = [q for q in ctx.questions if q.bank in joiner_set]
    semaphore = asyncio.Semaphore(parallel or max(1, len(asked)))

    async def one(question: Question) -> dict[str, Any]:
        async with semaphore:
            return await ctx.requester.process(question)

    records = await asyncio.gather(*(one(q) for q in asked))
    hits = [ctx.answered(r, ctx.gold_doc_of[q.qid]) for q, r in zip(asked, records, strict=True)]
    return {"n_questions": len(asked), "hit_rate": float(np.mean(hits)) if hits else None}


# ── 全体 ──────────────────────────────────────────────────────────────────────


def candidate_agreement(
    ctx: JoinContext,
    live: SummaryProbeRouter,
    offline: SummaryProbeRouter,
    sources: Sequence[SourceProfile],
    questions: Sequence[Question],
) -> float | None:
    """質問ごとの probe の候補（デバイスの集合）が，実機で組み立てた要約と，オフラインの一括の要約（build-summary）で一致する割合（比べられなければ None）．"""
    url_of_shard = ctx.full_placement.url_of_shard()
    device_of_source = {s.source: url_of_shard[s.shard_ids[0]] for s in sources}
    same = 0
    for question in questions:
        query = RoutingQuery(
            question.qid,
            question.question,
            ctx.requester.embedder.embed(question),
            tokens=tuple(tokenize(question.question)),
        )
        try:
            a = live.candidates(query, sources, device_of_source)
            b = offline.candidates(query, sources, device_of_source)
        except ValueError:
            # オフラインの要約が別の配置・データ源の並びで作られている（比べられない）
            return None
        same += set(a) == set(b)
    return same / len(questions) if questions else 0.0


def agreement_questions(ctx: JoinContext) -> list[Question]:
    """候補の一致を調べる質問：test は先頭 AGREEMENT_TEST_QUESTIONS 問（従来），それ以外は各受信箱の先頭 AGREEMENT_PER_INBOX 問．"""
    if ctx.cfg.experiment.question_split == "test":
        return ctx.questions[:AGREEMENT_TEST_QUESTIONS]
    taken: dict[str, int] = {}
    chosen: list[Question] = []
    for question in ctx.questions:
        if taken.get(question.bank, 0) < AGREEMENT_PER_INBOX:
            taken[question.bank] = taken.get(question.bank, 0) + 1
            chosen.append(question)
    return chosen


def compare_bm25_stats(
    live: SummaryProbeRouter, offline: SummaryProbeRouter
) -> dict[str, Any] | None:
    """質問者が公開情報から組み立てた `Bm25Stats` と，オフラインの `summary/bm25_stats.npz` の差（G-A．どちらかに無ければ None）．

    published_df は語で対応づける（語彙の並びは違いうる）．片方にだけある語の数と，共通の語の差の最大も残す．
    """
    live_artifact, offline_artifact = live.artifact, offline.artifact
    if live_artifact.bm25 is None or offline_artifact.bm25 is None:
        return None
    a, b = live_artifact.bm25, offline_artifact.bm25
    only_live = [t for t in live_artifact.vocab if t not in offline_artifact.vocab]
    only_offline = [t for t in offline_artifact.vocab if t not in live_artifact.vocab]
    common = [t for t in live_artifact.vocab if t in offline_artifact.vocab]
    diffs = np.asarray(
        [
            abs(
                float(a.published_df[live_artifact.vocab[t]])
                - float(b.published_df[offline_artifact.vocab[t]])
            )
            for t in common
        ]
    )
    rel = abs(a.avg_doc_len - b.avg_doc_len) / b.avg_doc_len if b.avg_doc_len else None
    return {
        "n_docs_live": a.n_docs,
        "n_docs_offline": b.n_docs,
        "avg_doc_len_live": a.avg_doc_len,
        "avg_doc_len_offline": b.avg_doc_len,
        "avg_doc_len_rel_diff": rel,
        "n_terms_only_live": len(only_live),
        "n_terms_only_offline": len(only_offline),
        "n_terms_common": len(common),
        "n_terms_df_mismatch": int((diffs > 0).sum()),
        "max_abs_df_diff": float(diffs.max()) if len(diffs) else 0.0,
        "g_a_stats_match": bool(
            a.n_docs == b.n_docs
            and rel is not None
            and rel <= STATS_REL_TOLERANCE
            and not only_live
            and not only_offline
            and not int((diffs > 0).sum())
        ),
    }


async def run_join(
    ctx: JoinContext,
    central: CentralIndex,
    embedder: DocEmbedder,
    params: JoinParams,
    out_dir: Path,
    offline_router: SummaryProbeRouter | None,
) -> dict[str, Any]:
    """準備（最初のメンバーの公開情報）→ 参加前の確認 → P2P の参加 → 中央の参加 → 通知の遅れの感度，を流し，結果を書く．"""
    await set_available(ctx, ctx.plan.joiner_hosts, False)
    try:
        setup = await collect_initial_members(ctx, params)
        initial = make_router_and_sources(ctx, ctx.state)
        pre = await pre_join_hit_rate(
            ctx,
            PRE_JOIN_PARALLEL_OWNER if ctx.cfg.experiment.answer_mode == "owner_answer" else None,
        )
        p2p = await measure_p2p_join(ctx, params)
        final = make_router_and_sources(ctx, ctx.state)
        central_result = await measure_central_join(ctx, central, embedder, params)
        stream = build_stream(
            ctx.questions, ctx.plan.joiner_inboxes, params.stream_questions, params.seed
        )
        delays = [await measure_delay(ctx, d, stream, initial, final) for d in params.delays]
        agreement = (
            candidate_agreement(ctx, final[2], offline_router, final[1], agreement_questions(ctx))
            if offline_router is not None
            else None
        )
        stats_check = (
            compare_bm25_stats(final[2], offline_router) if offline_router is not None else None
        )
    finally:
        # 測定の後は全てのノードを有効にして終える
        await set_available(ctx, ctx.plan.joiner_hosts, True)
    verdict = write_verdict(
        out_dir,
        judge_join(
            durations_or_censored(p2p["answer_s"]),
            durations_or_censored(central_result["answer_s"]),
            p2p["payload_bytes"],
            central_result["transfer_bytes"],
        ),
    )
    write_json(
        out_dir / JOIN_FILE,
        {
            "plan": {
                "joiner_hosts": ctx.plan.joiner_hosts,
                "joiner_inboxes": ctx.plan.joiner_inboxes,
                "n_initial_devices": len(ctx.plan.initial_placement.nodes),
                # 動作確認の設定（None 以外なら本番の測定ではない）
                "smoke_initial_devices": params.initial_devices,
                "smoke_summary_max_docs": params.summary_max_docs,
            },
            "setup": setup,
            "pre_join": pre,
            "p2p": p2p,
            "central": central_result,
            "delays": delays,
        },
    )
    write_json(
        out_dir / SIM_VS_LIVE_FILE,
        {
            "pre_join": {"predicted_hit_rate": 0.0, "live_hit_rate": pre["hit_rate"]},
            "candidate_agreement_with_offline_summary": agreement,
            "bm25_stats_vs_offline": stats_check,
            "delay_zero_vs_main": _delay_zero_vs_main(delays, p2p),
            "causes": (
                "候補の不一致は，デバイスの CPU と GPU の埋め込みの丸め（重心が僅かに違う）と，"
                "公開情報の作り直しで CORI の cf・長さの平均が変わる分"
            ),
        },
    )
    return verdict


def _delay_zero_vs_main(
    delays: Sequence[Mapping[str, Any]], p2p: Mapping[str, Any]
) -> dict[str, Any] | None:
    """L = 0 の質問の流れの中の最初の正解までの順位と，主の測定で答えられた受信箱の数の比べ（同じ受信箱が両方で答えられるはず）．"""
    zero = next((d for d in delays if d["delay_questions"] == 0), None)
    if zero is None:
        return None
    answered_main = sum(a is not None for a in p2p["answer_s"])
    return {
        "inboxes_answered_main": answered_main,
        "inboxes_answered_stream_l0": len(zero["first_hit_position"]),
    }


async def run_join_command(
    cfg: AtriumConfig,
    data_dir: Path,
    placement_path: Path,
    out_dir: Path,
    ollama_url: str,
    params: JoinParams,
) -> dict[str, Any]:
    """質問者・中央の索引・メンバー表を組み立て，run_join を流す（コンテナの中で `atrium join-run` から呼ばれる）．"""
    from atrium.encoders import ArcticEncoder
    from atrium.experiment import (
        build_http_client,
        read_image_git_head,
    )
    from atrium.manifest import read_placement
    from atrium.paths import dataset_paths
    from atrium.routing import AllRouter
    from atrium.routing.summary import SUMMARY_DIRNAME, load_summary

    paths = dataset_paths(data_dir, "enronqa")
    placement = read_placement(placement_path)
    e = cfg.data.require_enronqa()
    split = cfg.experiment.question_split
    owner = cfg.experiment.answer_mode == "owner_answer"
    if split == "test":
        questions, gold = load_test_questions(paths)
    else:
        # test 以外（H など）は各受信箱の先頭 max_tries_per_inbox 問をファイルの順で使う（質問 ID の順に並べ替えない）
        questions, gold = load_split_questions(paths, split, params.max_tries_per_inbox)
    plan = plan_join(placement, e.sources, params.n_devices, params.seed, params.initial_devices)
    out_dir.mkdir(parents=True, exist_ok=True)
    async with build_http_client(cfg.experiment.http_pool, placement) as client:
        embedder = (
            CachedQueryEmbedder(paths.query_ids, {e.encoder: paths.query_embeddings(e.encoder)})
            if split == "test"
            else make_split_query_embedder(cfg, data_dir)
        )
        candidate_scorer: TableCandidateScorer | None = None
        if owner and cfg.answer.aggregate == "cross_encoder":
            if cfg.retrieval.ce_scores != "table":
                raise ValueError(
                    "join-run needs retrieval.ce_scores=table (no GPU on the requester)"
                )
            candidate_scorer = TableCandidateScorer(
                params.owner_table or paths.ce_table_owner(split)
            )
        requester = Requester(
            cfg=cfg,
            placement=plan.initial_placement,
            sources=[],
            router=AllRouter(),
            embedder=embedder,
            client=client,
            ollama_url=ollama_url,
            candidate_scorer=candidate_scorer,
        )
        ctx = JoinContext(
            cfg=cfg,
            requester=requester,
            client=client,
            full_placement=placement,
            plan=plan,
            questions=questions,
            gold_doc_of={q.qid: gold[q.qid]["path"] for q in questions},
            source_order=list(e.sources),
            encoder_name=e.encoder,
            answered=is_owner_answered if owner else is_hit,
            sort_questions_by_qid=split == "test",
        )
        central = CentralIndex.from_corpus(cfg, paths, sources=plan.initial_inboxes)
        if params.central_mode == CENTRAL_MODE_C1_RRF_CE:
            ctx.central_requester = Requester(
                cfg=central_config(cfg),
                placement=placement,
                sources=[],
                router=AllRouter(),
                embedder=embedder,
                client=client,
                ollama_url=ollama_url,
                central_index=central,
                reranker=TableReranker(params.central_table or paths.ce_table(split)),
            )
        text_encoder = ArcticEncoder(e.encoder, e.embed_batch_size)
        summary_dir = paths.root / SUMMARY_DIRNAME
        offline: SummaryProbeRouter | None = None
        if summary_dir.exists():
            offline = (
                Bm25ProbeRouter(
                    cfg.answer.owner_m if owner else cfg.routing.top_m["enronqa"],
                    cfg.routing.bm25_probe_d["enronqa"],
                    load_summary(summary_dir),
                    e.encoder,
                    cfg.routing.summary_weight,
                    cfg.routing.bm25_weight,
                    cfg.routing.owner_score,
                    cfg.routing.owner_score_agg,
                    cfg.routing.owner_score_k,
                )
                if cfg.experiment.routing == "bm25_probe"
                else SummaryProbeRouter(
                    "hybrid",
                    cfg.routing.top_m["enronqa"],
                    cfg.routing.hybrid_probe_d["enronqa"],
                    load_summary(summary_dir),
                    e.encoder,
                    cfg.routing.summary_weight,
                )
            )
        write_json(
            out_dir / RUN_META,
            {
                "run_id": out_dir.name,
                "kind": "join_run",
                "git_head": read_image_git_head(),
                "routing": cfg.experiment.routing,
                "question_split": split,
                "central_mode": params.central_mode,
                "owner_table": str(params.owner_table) if params.owner_table else None,
                "central_table": str(params.central_table) if params.central_table else None,
                "joiner_hosts": plan.joiner_hosts,
                "joiner_inboxes": plan.joiner_inboxes,
                "central_index": {"n_docs": central.n_docs, "load_s": central.load.total_s},
                "config": cfg.model_dump(mode="json"),
            },
        )
        return await run_join(ctx, central, text_encoder, params, out_dir, offline)


def parse_delays(text: str) -> tuple[int, ...]:
    """ "0,10,100" を通知の遅れ（問）の組にする（空の文字列は感度を流さない）．"""
    return tuple(int(t) for t in text.split(",") if t.strip())


def make_split_query_embedder(cfg: AtriumConfig, data_dir: Path) -> CachedQueryEmbedder:
    """test 以外の分割（holdout など）のクエリ埋め込み（`atrium start` と同じ読み方）を引く埋め込み器．"""
    from atrium.experiment import _make_embedder

    embedder = _make_embedder(cfg, data_dir, [])
    if not isinstance(embedder, CachedQueryEmbedder):
        raise TypeError("join-run needs cached query embeddings")
    return embedder


def central_config(cfg: AtriumConfig) -> AtriumConfig:
    """主構成の config から，中央 C1-RRF-CE の質問者の config を作る（索引は質問者が持ち，RRF の上位を cross-encoder の表で並べ替え，生成は GPU の PC へ送る）．"""
    return cfg.model_copy(
        update={
            "experiment": cfg.experiment.model_copy(
                update={
                    "routing": "central_local",
                    "answer_mode": "snippet_return",
                    "central_generation": "gpu_nodes",
                }
            ),
            "retrieval": cfg.retrieval.model_copy(
                update={"merge": "cross_encoder", "ce_scores": "table"}
            ),
        }
    )
