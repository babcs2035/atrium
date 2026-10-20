"""p0007 I5（plans/p0007 §6.4 C）：新しいメールが答えに使えるようになるまでの時間を，実機の壁時計で測る（`atrium freshness-run`）．

質問者のコンテナ（deploy で `cluster.node_admin: true` にしたもの）で動く．test の正解メールの 10%（種 12）を「後から届くメール」として，
実験の開始時に持ち主の索引から外し（ノードの `/v1/admin/docs/hide`），途中で 1 通ずつ届ける．

- P2P：メールは持ち主のデバイスへ届く（`/v1/admin/docs/add`）．デバイスがその場で CPU で本文を埋め込み，索引に足す．
  届いた直後から，持ち主のデバイスへそのメールについての質問を繰り返し投げ（`/v1/retrieve`），正解のメールが上位 k 通に入るまでの時間を測る（direct）．
  設定の routing（hybrid_probe など）を通した質問でも同じ時間を測る（routed．公開情報は作り直さないので参考）．
- 中央（間隔 T ∈ {0, 60, 600, ∞} 秒）：届いたメールを，開始時刻を 0 とする T の倍数の時刻に，持ち主のデバイスからまとめて取り出し
  （`/v1/admin/docs/export`），中央（質問者）が埋め込んで索引に足す．足した後に同じ質問で答えられるまでの時間を測る．T = ∞ は転送しない．
- 届いてから ask_delay_s 後に，設定の answer_mode で質問を 1 回流し，結果を `<out>/<条件>/results.jsonl` に残す
  （generation を伴う設定では `atrium judge` で採点して，新しいメールについての正答率を条件ごとに出す）．

使い方（実機の本番の手順は docs/d0003_experiment_workflow.md の §7．制御点で `scripts/remote/freshness_run.sh`）:
    atrium --config config.yaml freshness-run --data-dir /data --placement placement.json --out-dir results/<run_id> \\
        --ollama-url http://ollama:11434 [--intervals 0,60,600,inf] [--n-arrivals N]

出力：`<out>/freshness.json`（条件ごとの時間・費用）・`verdict.json`（`verdict_original`）・`sim_vs_live.json`（§10）・`run_meta.json`．
判定（事前登録）：P2P（direct）の答えられるまでの時間の p50 が 1 秒未満，かつ中央の T ≥ 60 秒の全ての場合より有意に短い（対にした片側の符号検定）．
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.data_enronqa import read_inbox
from atrium.dynamics import (
    DEFAULT_SEED,
    HELD_BACK_FRACTION,
    central_flush_offsets,
    choose_held_back,
    durations_or_censored,
    eligible_indices,
    judge_freshness,
    summarize_times,
    times_or_censored,
)
from atrium.live_common import is_hit, load_test_questions, sleep_until, write_json
from atrium.node import DocEmbedder
from atrium.paths import DatasetPaths
from atrium.protocol import (
    AddDocsRequest,
    AddDocsResponse,
    EmbedderLoadResponse,
    ExportDocsRequest,
    ExportDocsResponse,
    HideDocsRequest,
    NewMail,
    RetrieveRequest,
    RetrieveResponse,
)
from atrium.requester import RETRIEVE_TIMEOUT_S, Requester, post_with_retry
from atrium.routing.central import CentralIndex
from atrium.verdict import write_verdict

logger = logging.getLogger(__name__)

RUN_META = "run_meta.json"
FRESHNESS_FILE = "freshness.json"
SIM_VS_LIVE_FILE = "sim_vs_live.json"
RESULTS = "results.jsonl"
# 既定の中央の転送の間隔 T（秒）．plans/p0007 §6.4
DEFAULT_INTERVALS = (0.0, 60.0, 600.0, math.inf)
# 質問者の待機中の接続を捨てるまでの秒数．ノードが閉じる 5 秒より短くして，閉じた直後の再利用による ReadError を避ける
CLIENT_KEEPALIVE_EXPIRY_S = 2.0
# 同時に流す検索の数の上限（実験の開始前の確認で使う）
PRECHECK_CONCURRENCY = 8


@dataclass(frozen=True)
class FreshnessParams:
    """測定の条件（コマンドの引数）．"""

    intervals: tuple[float, ...] = DEFAULT_INTERVALS
    # 届けるメールの数の上限（None なら後から届くメールの全て）．届けなかったメールも外したままにする
    n_arrivals: int | None = None
    # メールが届く間隔（秒）
    arrival_gap_s: float = 0.5
    # 届いてから，設定の answer_mode で質問を流すまでの遅れ（秒）
    ask_delay_s: float = 1.0
    # 答えられるまで質問を繰り返す時間の上限（秒．超えたら打ち切り）と，繰り返しの間隔（0 なら続けて送る）
    poll_timeout_s: float = 30.0
    poll_gap_s: float = 0.0
    # 設定の routing を通した質問の時間（routed）も測るか
    routed: bool = True
    fraction: float = HELD_BACK_FRACTION
    seed: int = DEFAULT_SEED


@dataclass(frozen=True)
class Arrival:
    """後から届くメール 1 通．qid はそのメールを正解とする質問（持ち主の受信箱の test の質問）．"""

    doc_id: str
    qid: str
    inbox: str
    offset_s: float


def plan_arrivals(
    questions: Sequence[Question],
    gold: Mapping[str, Mapping[str, Any]],
    params: FreshnessParams,
) -> tuple[list[str], list[Arrival]]:
    """後から届くメール全て（外しておく文書 ID）と，実際に届ける最初の n_arrivals 通（届く順・時刻つき）を決める．

    後から届くメールは，test の質問の正解のメールの重複を除いた集合から種つきで 10% 選ぶ．
    各メールの質問は，そのメールを正解とする質問のうち質問 ID が最小のもの．
    """
    qid_of_doc: dict[str, str] = {}
    for q in sorted(questions, key=lambda q: q.qid):
        qid_of_doc.setdefault(gold[q.qid]["path"], q.qid)
    held = choose_held_back(list(qid_of_doc), params.fraction, params.seed)
    delivered = held if params.n_arrivals is None else held[: params.n_arrivals]
    arrivals = [
        Arrival(
            doc_id=doc_id,
            qid=qid_of_doc[doc_id],
            inbox=gold[qid_of_doc[doc_id]]["inbox"],
            offset_s=i * params.arrival_gap_s,
        )
        for i, doc_id in enumerate(delivered)
    ]
    return held, arrivals


@dataclass
class FreshnessContext:
    """測定に使うもの（質問者・質問・メールの本文・持ち主のノードの URL）．"""

    cfg: AtriumConfig
    requester: Requester
    client: httpx.AsyncClient
    questions: dict[str, Question]
    mails: dict[str, dict[str, str]]
    url_of_inbox: dict[str, str]
    encoder_name: str

    def question_embedding(self, qid: str) -> list[float]:
        """質問の埋め込み（質問者が持つ，事前計算したもの）．"""
        vector = self.requester.embedder.embed(self.questions[qid])[self.encoder_name]
        return [float(x) for x in vector]


async def owner_retrieval_hit(ctx: FreshnessContext, arrival: Arrival) -> bool:
    """持ち主のデバイスへ質問を直接送り（ルーティングを通さない），正解のメールが上位 k 通に入るか．"""
    question = ctx.questions[arrival.qid]
    k = ctx.cfg.data.require_enronqa().k_context
    body = RetrieveRequest(
        shard_id=arrival.inbox,
        k=k,
        embedding=ctx.question_embedding(arrival.qid),
        query_id=question.source_qid,
        query_text=question.question if ctx.cfg.retrieval.fusion == "rrf" else None,
    )
    # 待機中の接続をノードが閉じた直後の再利用で起きる ReadError（本番の 1 回目で 1 件発生し，測定全体が止まった）は送り直す
    response = await post_with_retry(
        ctx.client,
        f"{ctx.url_of_inbox[arrival.inbox]}/v1/retrieve",
        body.model_dump(),
        RETRIEVE_TIMEOUT_S,
    )
    response.raise_for_status()
    docs = RetrieveResponse.model_validate_json(response.content).docs
    return arrival.doc_id in {d.doc_id for d in docs}


async def poll_until(
    attempt: Any, started: float, timeout_s: float, gap_s: float
) -> tuple[float | None, int]:
    """attempt()（真偽を返す coroutine 関数）が真になるまで繰り返し，started（perf_counter）からの経過秒と試行の回数を返す．

    timeout_s を超えたら（答えられなければ）経過秒は None．
    """
    polls = 0
    while True:
        polls += 1
        if await attempt():
            return time.perf_counter() - started, polls
        if time.perf_counter() - started > timeout_s:
            return None, polls
        await asyncio.sleep(gap_s)


# ── 事前の準備 ────────────────────────────────────────────────────────────────


async def prepare_nodes(
    ctx: FreshnessContext,
    held: Sequence[str],
    arrivals: Sequence[Arrival],
    doc_inbox: Mapping[str, str],
) -> dict[str, Any]:
    """持ち主のデバイスで，後から届くメールを索引から外し，届ける先の埋め込みモデルを読み込んでならしておく．

    外す前に再実行の状態（前の実行が足したメール）を残さないよう，同じ文書 ID の行は全て外れる（store.hide）．
    """
    held_by_inbox: dict[str, list[str]] = {}
    for doc_id in held:
        held_by_inbox.setdefault(doc_inbox[doc_id], []).append(doc_id)

    async def hide(inbox: str, doc_ids: list[str]) -> int:
        response = await ctx.client.post(
            f"{ctx.url_of_inbox[inbox]}/v1/admin/docs/hide",
            json=HideDocsRequest(shard_id=inbox, doc_ids=doc_ids).model_dump(),
            timeout=RETRIEVE_TIMEOUT_S,
        )
        response.raise_for_status()
        return int(response.json()["n_hidden"])

    hidden = await asyncio.gather(*(hide(i, d) for i, d in held_by_inbox.items()))
    owners = sorted({ctx.url_of_inbox[a.inbox] for a in arrivals})

    async def load(url: str) -> dict[str, Any]:
        response = await ctx.client.post(
            f"{url}/v1/admin/embedder/load", timeout=RETRIEVE_TIMEOUT_S
        )
        response.raise_for_status()
        loaded = EmbedderLoadResponse.model_validate_json(response.content)
        return {"url": url, **loaded.model_dump()}

    embedders = await asyncio.gather(*(load(u) for u in owners))
    semaphore = asyncio.Semaphore(PRECHECK_CONCURRENCY)

    async def before(arrival: Arrival) -> bool:
        async with semaphore:
            return await owner_retrieval_hit(ctx, arrival)

    pre_hits = await asyncio.gather(*(before(a) for a in arrivals))
    return {
        "n_held_back": len(held),
        "n_rows_hidden": int(sum(hidden)),
        # 外した直後は，届く前のメールに答えられてはならない（0 でなければ外し方か質問の誤り）
        "pre_arrival_hit_rate": float(np.mean(pre_hits)) if pre_hits else None,
        "embedders": embedders,
    }


# ── P2P ───────────────────────────────────────────────────────────────────────


async def deliver_to_owner(
    ctx: FreshnessContext, arrival: Arrival
) -> tuple[AddDocsResponse, float]:
    """メールを持ち主のデバイスへ届ける（索引に足されるまで待つ）．（デバイスが報告した所要時間，往復の秒数）を返す．"""
    mail = ctx.mails[arrival.doc_id]
    body = AddDocsRequest(
        shard_id=arrival.inbox,
        docs=[NewMail(doc_id=arrival.doc_id, title=mail["title"], content=mail["content"])],
    )
    start = time.perf_counter()
    response = await ctx.client.post(
        f"{ctx.url_of_inbox[arrival.inbox]}/v1/admin/docs/add",
        json=body.model_dump(),
        timeout=RETRIEVE_TIMEOUT_S,
    )
    response.raise_for_status()
    return AddDocsResponse.model_validate_json(response.content), time.perf_counter() - start


async def measure_p2p(
    ctx: FreshnessContext, arrivals: Sequence[Arrival], params: FreshnessParams, out_dir: Path
) -> list[dict[str, Any]]:
    """メールを予定の時刻に届け，メールごとに，答えられるまでの時間（direct・routed）と，届いてから ask_delay_s 後の質問の結果を記録する．

    メールごとの処理は並行して流れる（届く間隔が処理より短くてもよい）．時間は届ける要求を送る直前を 0 とする．
    """
    origin = time.perf_counter()
    results_path = out_dir / "p2p" / RESULTS
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text("", encoding="utf-8")

    async def one(i: int, arrival: Arrival) -> dict[str, Any]:
        await sleep_until(origin, arrival.offset_s)
        started = time.perf_counter()
        added, add_rtt_s = await deliver_to_owner(ctx, arrival)
        direct_s, direct_polls = await poll_until(
            lambda: owner_retrieval_hit(ctx, arrival),
            started,
            params.poll_timeout_s,
            params.poll_gap_s,
        )
        row: dict[str, Any] = {
            "index": i,
            "doc_id": arrival.doc_id,
            "inbox": arrival.inbox,
            "node_embed_s": added.embed_s,
            "node_add_s": added.add_s,
            "node_bm25_s": added.bm25_s,
            "node_total_s": added.total_s,
            "add_rtt_s": add_rtt_s,
            "embedder": added.embedder,
            "device": added.device,
            "direct_s": direct_s,
            "direct_polls": direct_polls,
        }
        question = ctx.questions[arrival.qid]
        if params.routed:

            async def routed_hit() -> bool:
                return is_hit(await ctx.requester.process(question), arrival.doc_id)

            row["routed_s"], row["routed_polls"] = await poll_until(
                routed_hit, started, params.poll_timeout_s, params.poll_gap_s
            )
        await asyncio.sleep(max(0.0, started + params.ask_delay_s - time.perf_counter()))
        record = await ctx.requester.process(question)
        record["fresh"] = {
            "condition": "p2p",
            "doc_id": arrival.doc_id,
            "delay_s": params.ask_delay_s,
        }
        row["hit_at_delay"] = is_hit(record, arrival.doc_id)
        with results_path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
        return row

    return list(await asyncio.gather(*(one(i, a) for i, a in enumerate(arrivals))))


# ── 中央 ──────────────────────────────────────────────────────────────────────


def central_hit(central: CentralIndex, ctx: FreshnessContext, arrival: Arrival) -> bool:
    """中央の索引に質問し，正解のメールが上位 k 通に入るか（ルーティングは無い）．"""
    question = ctx.questions[arrival.qid]
    k = ctx.cfg.data.require_enronqa().k_context
    embedding = np.asarray(ctx.question_embedding(arrival.qid), dtype=np.float32)
    text = question.question if ctx.cfg.retrieval.fusion == "rrf" else None
    return arrival.doc_id in {central.doc_ids[i] for i in central.search_ids(embedding, k, text)}


async def flush_to_central(
    ctx: FreshnessContext,
    central: CentralIndex,
    embedder: DocEmbedder,
    pending: Sequence[Arrival],
) -> dict[str, Any]:
    """届いたメールを持ち主のデバイスから取り出し（転送），中央が埋め込んで索引に足す．転送の大きさ・時間と，中央での追加の時間を返す．"""
    by_inbox: dict[str, list[str]] = {}
    for a in pending:
        by_inbox.setdefault(a.inbox, []).append(a.doc_id)

    async def export(inbox: str, doc_ids: list[str]) -> tuple[ExportDocsResponse, int]:
        response = await post_with_retry(
            ctx.client,
            f"{ctx.url_of_inbox[inbox]}/v1/admin/docs/export",
            ExportDocsRequest(shard_id=inbox, doc_ids=doc_ids).model_dump(),
            RETRIEVE_TIMEOUT_S,
        )
        response.raise_for_status()
        return ExportDocsResponse.model_validate_json(response.content), len(response.content)

    start = time.perf_counter()
    exported = await asyncio.gather(*(export(i, d) for i, d in by_inbox.items()))
    transfer_s = time.perf_counter() - start
    docs = [d for resp, _ in exported for d in resp.docs]
    start = time.perf_counter()
    emb = await asyncio.to_thread(embedder.encode_texts, [d.content for d in docs])
    embed_s = time.perf_counter() - start
    add_s = central.append(
        [d.doc_id for d in docs], [d.title for d in docs], [d.content for d in docs], emb
    )
    return {
        "n_mails": len(docs),
        "transfer_bytes": sum(size for _, size in exported),
        "transfer_s": transfer_s,
        "embed_s": embed_s,
        "add_s": add_s,
    }


async def measure_central(
    ctx: FreshnessContext,
    central: CentralIndex,
    embedder: DocEmbedder,
    arrivals: Sequence[Arrival],
    interval_s: float,
    params: FreshnessParams,
    central_requester: Requester | None,
    out_dir: Path,
) -> dict[str, Any]:
    """間隔 interval_s で中央へ転送するときの，メールごとに答えられるまでの時間と，転送・追加の費用．

    メールが届く時刻と転送の時刻は，開始を 0 とする予定の時刻（central_flush_offsets）で動かす．
    central_requester があれば，届いてから ask_delay_s 後の質問を設定の answer_mode で流し，結果を残す．
    """
    n_base = central.n_docs
    arrival_offsets = [a.offset_s for a in arrivals]
    flush_at = central_flush_offsets(arrival_offsets, interval_s)
    events: list[tuple[float, int, str, Any]] = []
    for i, a in enumerate(arrivals):
        events.append((a.offset_s + params.ask_delay_s, 1, "probe", i))
    for t in sorted({t for t in flush_at if not math.isinf(t)}):
        events.append((t, 0, "flush", t))
    events.sort(key=lambda e: (e[0], e[1]))
    results_path = out_dir / _condition_name(interval_s) / RESULTS
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text("", encoding="utf-8")
    answered: list[float | None] = [None] * len(arrivals)
    probe_hits: list[bool | None] = [None] * len(arrivals)
    flushes: list[dict[str, Any]] = []
    origin = time.perf_counter()
    for at, _, kind, payload in events:
        await sleep_until(origin, at)
        if kind == "flush":
            # 予定の時刻までに届いたメールで，この時刻に転送されるもの
            pending = [i for i, t in enumerate(flush_at) if t == payload]
            cost = await flush_to_central(ctx, central, embedder, [arrivals[i] for i in pending])
            done = time.perf_counter() - origin
            flushes.append({"scheduled_s": payload, "finished_s": done, **cost})
            for i in pending:
                if central_hit(central, ctx, arrivals[i]):
                    answered[i] = time.perf_counter() - origin
            continue
        arrival = arrivals[payload]
        question = ctx.questions[arrival.qid]
        if central_requester is None:
            probe_hits[payload] = central_hit(central, ctx, arrival)
            continue
        record = await central_requester.process(question)
        record["fresh"] = {
            "condition": _condition_name(interval_s),
            "doc_id": arrival.doc_id,
            "delay_s": params.ask_delay_s,
        }
        probe_hits[payload] = is_hit(record, arrival.doc_id)
        with results_path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    central.truncate(n_base)
    times = times_or_censored(arrival_offsets, answered)
    return {
        "interval_s": None if math.isinf(interval_s) else interval_s,
        "times_s": [None if math.isinf(t) else t for t in times],
        "summary": summarize_times(times),
        "hit_at_delay": probe_hits,
        "hit_at_delay_rate": _rate(probe_hits),
        "flushes": flushes,
        "scheduled_wait_s": [
            None if math.isinf(f) else f - a for f, a in zip(flush_at, arrival_offsets, strict=True)
        ],
    }


def _condition_name(interval_s: float) -> str:
    """出力のディレクトリ名（central_T0・central_T60・central_Tinf）．"""
    return "central_Tinf" if math.isinf(interval_s) else f"central_T{interval_s:g}"


def _rate(flags: Sequence[bool | None]) -> float | None:
    """None を除いた真の割合（全て None なら None）．"""
    known = [f for f in flags if f is not None]
    return sum(known) / len(known) if known else None


# ── 全体 ──────────────────────────────────────────────────────────────────────


def sim_vs_live(
    p2p_rows: Sequence[Mapping[str, Any]], central: Mapping[float, Mapping[str, Any]]
) -> dict[str, Any]:
    """オフラインの予測（plans/p0007 §10）と実機の値．

    中央：予定の待ち時間（T の倍数の時刻まで待つだけ．転送・埋め込みの費用を 0 とする予測）の中央値と，実測の中央値の差・比．
    P2P：デバイスが報告した埋め込み＋索引への追加の時間（予測）と，実測の答えられるまでの時間（通信と検索を含む）の中央値の差・比．
    """
    out: dict[str, Any] = {"central": {}, "p2p": None}
    for interval, row in sorted(central.items()):
        waits = [w for w in row["scheduled_wait_s"] if w is not None]
        live = [t for t in row["times_s"] if t is not None]
        if waits and live:
            predicted = float(np.median(waits))
            measured = float(np.median(live))
            out["central"][f"{interval:g}"] = {
                "predicted_p50_s": predicted,
                "live_p50_s": measured,
                "difference_s": measured - predicted,
                "ratio": measured / predicted if predicted > 0 else None,
            }
    server = [r["node_embed_s"] + r["node_add_s"] + r["node_bm25_s"] for r in p2p_rows]
    live = [r["direct_s"] for r in p2p_rows if r["direct_s"] is not None]
    if server and live:
        predicted = float(np.median(server))
        measured = float(np.median(live))
        out["p2p"] = {
            "predicted_p50_s": predicted,
            "live_p50_s": measured,
            "difference_s": measured - predicted,
            "ratio": measured / predicted if predicted > 0 else None,
            "note": "予測はデバイスが報告した埋め込み＋索引への追加．差は通信・検索の往復と，試行の間隔",
        }
    out["causes"] = (
        "中央は予定の待ち時間に転送・埋め込み・追加の費用が加わる分だけ長くなる．P2P は報告の時間に HTTP の往復と検索が加わる"
    )
    return out


async def run_freshness(
    ctx: FreshnessContext,
    central: CentralIndex,
    embedder: DocEmbedder,
    questions: Sequence[Question],
    gold: Mapping[str, Mapping[str, Any]],
    params: FreshnessParams,
    out_dir: Path,
    central_requester: Requester | None,
) -> dict[str, Any]:
    """P2P と中央の各 T を順に測り，freshness.json・verdict.json・sim_vs_live.json を書く．"""
    held, arrivals = plan_arrivals(questions, gold, params)
    doc_inbox = {gold[q.qid]["path"]: gold[q.qid]["inbox"] for q in questions}
    preparation = await prepare_nodes(ctx, held, arrivals, doc_inbox)
    p2p_rows = await measure_p2p(ctx, arrivals, params, out_dir)
    p2p_times = durations_or_censored([r["direct_s"] for r in p2p_rows])
    routed_times = (
        durations_or_censored([r.get("routed_s") for r in p2p_rows]) if params.routed else None
    )
    central_rows: dict[float, dict[str, Any]] = {}
    for interval in params.intervals:
        central_rows[interval] = await measure_central(
            ctx, central, embedder, arrivals, interval, params, central_requester, out_dir
        )
    # どの構成でも最後まで答えられないメール（新しさとは別の理由．中央は全受信箱の上位 5 通に入らない場合など）は，時間の比較から除く
    eligible = eligible_indices(
        [r["direct_s"] for r in p2p_rows], {k: row["times_s"] for k, row in central_rows.items()}
    )
    central_times = {
        interval: [math.inf if row["times_s"][i] is None else row["times_s"][i] for i in eligible]
        for interval, row in central_rows.items()
    }
    verdict_body = {
        **judge_freshness([p2p_times[i] for i in eligible], central_times),
        "eligibility": {
            "n_arrivals": len(arrivals),
            "n_compared": len(eligible),
            "rule": "P2P（direct）で答えられ，かつ中央の有限の T のどれかで答えられたメールだけを比べる",
        },
    }
    verdict = write_verdict(out_dir, verdict_body)
    summary = {
        "params": {
            "intervals_s": [None if math.isinf(t) else t for t in params.intervals],
            "n_arrivals": len(arrivals),
            "arrival_gap_s": params.arrival_gap_s,
            "ask_delay_s": params.ask_delay_s,
            "poll_timeout_s": params.poll_timeout_s,
            "fraction": params.fraction,
            "seed": params.seed,
        },
        "preparation": preparation,
        "p2p": {
            "rows": p2p_rows,
            "direct": summarize_times(p2p_times),
            "routed": None if routed_times is None else summarize_times(routed_times),
            "hit_at_delay_rate": _rate([r["hit_at_delay"] for r in p2p_rows]),
            "node_embed_s": summarize_times([r["node_embed_s"] for r in p2p_rows]),
            "node_add_s": summarize_times([r["node_add_s"] + r["node_bm25_s"] for r in p2p_rows]),
        },
        "central": {f"{'inf' if math.isinf(k) else f'{k:g}'}": v for k, v in central_rows.items()},
    }
    write_json(out_dir / FRESHNESS_FILE, summary)
    write_json(out_dir / SIM_VS_LIVE_FILE, sim_vs_live(p2p_rows, central_rows))
    return verdict


JUDGEMENTS = "judgements.jsonl"
ACCURACY_FILE = "accuracy.json"


def freshness_accuracy(run_dir: Path) -> dict[str, Any]:
    """新しいメールについての質問の正答率を条件（p2p・central_T*）ごとに出し，`<run_dir>/accuracy.json` に書く．

    先に `atrium judge`（`scripts/tasks/judge.sh run <run_id>/<条件>`）で各条件の results.jsonl を採点しておく．
    分母は流した質問の全て（失敗・判定できなかった回答は不正解として数える）．採点がまだの条件は載せない．
    """
    from atrium.analysis import merge_judgements, read_results

    out: dict[str, Any] = {}
    for condition_dir in sorted(p for p in run_dir.iterdir() if (p / RESULTS).exists()):
        if not (condition_dir / JUDGEMENTS).exists():
            continue
        rows = read_results(condition_dir / RESULTS)
        merged = merge_judgements(rows, condition_dir / JUDGEMENTS)
        correct = sum(bool(r.get("correct")) for r in merged)
        out[condition_dir.name] = {
            "n": len(rows),
            "n_failed": sum(r.get("error") is not None for r in rows),
            "n_correct": correct,
            "accuracy": correct / len(rows) if rows else None,
        }
    write_json(run_dir / ACCURACY_FILE, out)
    return out


async def run_freshness_command(
    cfg: AtriumConfig,
    data_dir: Path,
    placement_path: Path,
    out_dir: Path,
    ollama_url: str,
    params: FreshnessParams,
) -> dict[str, Any]:
    """質問者・中央の索引・持ち主のノードの対応を組み立て，run_freshness を流す（コンテナの中で `atrium freshness-run` から呼ばれる）．"""
    from atrium.encoders import ArcticEncoder
    from atrium.experiment import build_http_client, build_requester, read_image_git_head
    from atrium.manifest import read_placement
    from atrium.paths import dataset_paths
    from atrium.routing.central import CentralLocalRouter

    paths: DatasetPaths = dataset_paths(data_dir, "enronqa")
    placement = read_placement(placement_path)
    questions, gold = load_test_questions(paths)
    held, arrivals = plan_arrivals(questions, gold, params)
    out_dir.mkdir(parents=True, exist_ok=True)
    async with build_http_client(
        cfg.experiment.http_pool, placement, keepalive_expiry_s=CLIENT_KEEPALIVE_EXPIRY_S
    ) as client:
        requester, sources, _ = await build_requester(cfg, data_dir, placement, client, ollama_url)
        e = cfg.data.require_enronqa()
        inboxes = sorted({a.inbox for a in arrivals})
        mails = {d["id"]: d for inbox in inboxes for d in read_inbox(paths, inbox)}
        shard_url = placement.url_of_shard()
        ctx = FreshnessContext(
            cfg=cfg,
            requester=requester,
            client=client,
            questions={q.qid: q for q in questions},
            mails=mails,
            url_of_inbox={s.source: shard_url[s.shard_ids[0]] for s in sources},
            encoder_name=e.encoder,
        )
        central = CentralIndex.from_corpus(cfg, paths, exclude_doc_ids=held)
        embedder = ArcticEncoder(e.encoder, e.embed_batch_size)
        central_requester = (
            None
            if cfg.experiment.answer_mode == "retrieval_only"
            else Requester(
                cfg=cfg,
                placement=placement,
                sources=[],
                router=CentralLocalRouter(),
                embedder=requester.embedder,
                client=client,
                ollama_url=ollama_url,
                central_index=central,
            )
        )
        write_json(
            out_dir / RUN_META,
            {
                "run_id": out_dir.name,
                "kind": "freshness_run",
                "git_head": read_image_git_head(),
                "routing": cfg.experiment.routing,
                "answer_mode": cfg.experiment.answer_mode,
                "retrieval_fusion": cfg.retrieval.fusion,
                "central_index": {"n_docs": central.n_docs, **{"load_s": central.load.total_s}},
                "config": cfg.model_dump(mode="json"),
            },
        )
        return await run_freshness(
            ctx, central, embedder, questions, gold, params, out_dir, central_requester
        )
