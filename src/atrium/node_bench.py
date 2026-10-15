"""専門家ノード 1 台に閉じた系の /v1/probe の負荷を集め，同時の要求の数ごとの処理量・待ち時間・CPU を測る（Iteration 9．`atrium node-bench`）．

30 台に負荷を散らすと質問者（制御点 1 台）が先に律速になり，専門家の限界に届かない（Iteration 8）．ここでは 1 台の
ノードだけに，flood_score がそのノードへ送るのと同じ要求（EnronQA の全 2,940 問の cached の埋め込みと，そのノードの
全シャードの shard_ids）を，同時の要求の数 C を固定した閉じた系（前の要求が返ったら次を送る）で送り続ける．

1 つの水準（C）は次の順に進む．
1. C を高々 max_procs 個の負荷生成のプロセスへ割り（atrium.requester_procs.split_parallel），各プロセスが自分の分の
   要求の本文を前もって JSON にしておく（負荷生成の費用を小さくするため）．
2. 全プロセスの準備が済んだら，同じ時刻 t0 から一斉に送り始める．[t0 + warmup_s, t0 + warmup_s + measure_s) が測定の区間で，
   区間の終わりで新しい要求を止め，送信中の要求は返るのを待つ（区間の外の要求は集計に入れない）．
3. 親は区間の始まりと終わりに，対象のノードの /healthz（プロセス・コンテナの cgroup・デバイスの CPU 時間）と，このデバイスの /proc/stat を読む．
   ノードを複数のプロセスで動かすとき（experiment.node_workers．Iteration 10）は，ノードの全体の CPU を cgroup の値で測る．

出力（out_dir）: node_bench.json（水準ごとの集計と条件），scores.json（qid → シャードごとの probe の得点．最初に見た値）．
使い方: scripts/remote/node_bench.sh（制御点の full イメージのコンテナ）から呼ぶ．指標の定義は docs/d0004_metrics.md．
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing as mp
import random
import resource
import statistics
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from multiprocessing.queues import Queue
from multiprocessing.sharedctypes import Synchronized
from multiprocessing.synchronize import Event
from pathlib import Path

import httpx
import numpy as np

from atrium.benchmarks import load_questions
from atrium.cpu_stat import CpuSample, busy_fractions, read_device_cpu
from atrium.manifest import Placement
from atrium.paths import dataset_paths
from atrium.protocol import ProbeRequest, ProbeResponse, ProfileResponse
from atrium.requester_procs import fetch_healthz, split_parallel

logger = logging.getLogger(__name__)

DATASET = "enronqa"
# 1 つの要求の打ち切り（飽和した後の待ち行列でも数秒に収まる見込み．打ち切りは失敗として数える）
REQUEST_TIMEOUT_S = 30.0
# 全プロセスの準備が済んでから送り始めるまでの余裕（全プロセスが同じ t0 を受け取るため）
START_DELAY_S = 2.0
# 子の準備（numpy の読み込みと本文の JSON 化）を待つ上限
READY_TIMEOUT_S = 300.0
# 区間の終わりから，送信中の要求が返って子が報告するまでを待つ上限
REPORT_GRACE_S = REQUEST_TIMEOUT_S + 60.0
# 水準の間の休み（前の水準の接続が閉じ，ノードの待ち行列が空になるまで）
LEVEL_PAUSE_S = 2.0
PERCENTILES = (50, 95, 99)
JSON_HEADERS = {"content-type": "application/json"}


@dataclass(frozen=True)
class BenchTarget:
    """負荷を集めるノード（配置の host と URL，シャード，埋め込みの検索器，/v1/profile が名乗るノード名）．"""

    host: str
    url: str
    node_id: str
    shard_ids: list[str]
    encoder: str


@dataclass(frozen=True)
class RequestRecord:
    """1 つの要求（時刻は time.time()．node_s はノードの中の probe の計算時間）．"""

    qid: str
    sent_at: float
    done_at: float
    node_s: float | None
    error: str | None


@dataclass
class WorkerReport:
    """負荷生成のプロセス 1 個の記録．cpu_s は測定の区間のこのプロセスの CPU 時間．"""

    index: int
    concurrency: int
    cpu_s: float
    records: list[RequestRecord]
    scores: dict[str, dict[str, float]]
    score_mismatches: int


@dataclass(frozen=True)
class NodeSample:
    """対象のノードの /healthz を読んだ結果（読めなければ値は None）と，読み取りの往復の時間．"""

    process_cpu_s: float | None
    host_cpu: CpuSample | None
    n_cpu: int | None
    rtt_s: float
    # Iteration 10：コンテナの cgroup の CPU 時間の累計，答えたプロセスの pid，ノードのプロセスの数（古いノードは返さない）
    cgroup_cpu_s: float | None = None
    pid: int | None = None
    workers: int | None = None


@dataclass(frozen=True)
class Window:
    """測定の区間 [start, end)（time.time()）．"""

    start: float
    end: float

    @property
    def length(self) -> float:
        """区間の長さ（秒）．"""
        return self.end - self.start


@dataclass
class LevelResult:
    """1 つの水準（同時の要求の数 C）の集計．"""

    concurrency: int
    procs: int
    concurrency_per_proc: list[int]
    measure_s: float
    n_ok: int
    n_errors: int
    error_kinds: dict[str, int]
    throughput_rps: float
    latency_s: dict[str, float | None]
    latency_mean_s: float | None
    node_s: dict[str, float | None]
    little_ratio: float | None
    node_proc_util: float | None
    node_cgroup_util: float | None
    node_workers: int | None
    node_host_busy: float | None
    node_host_softirq: float | None
    node_n_cpu: int | None
    healthz_rtt_s: list[float]
    generator_cpu_util_per_proc: list[float]
    control_cpu_busy: float | None
    score_mismatches: int


def question_order(qids: Sequence[str], seed: int) -> list[str]:
    """`atrium run` の EnronQA と同じ並べ替え（種で決まる順）．"""
    order = list(qids)
    random.Random(seed).shuffle(order)
    return order


def build_probe_bodies(
    qids: Sequence[str], ids_path: Path, emb_path: Path, shard_ids: Sequence[str]
) -> list[tuple[str, bytes]]:
    """qid ごとに，flood_score がそのノードへ送るのと同じ /v1/probe の本文を JSON にする．"""
    row_of = {qid: i for i, qid in enumerate(json.loads(ids_path.read_text(encoding="utf-8")))}
    matrix = np.load(emb_path, mmap_mode="r")
    bodies: list[tuple[str, bytes]] = []
    for qid in qids:
        embedding = np.asarray(matrix[row_of[qid]], dtype=np.float32)
        req = ProbeRequest(shard_ids=list(shard_ids), embedding=embedding.tolist())
        bodies.append((qid, json.dumps(req.model_dump()).encode("utf-8")))
    return bodies


def _percentiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {f"p{p}": None for p in PERCENTILES}
    arr = np.asarray(values, dtype=np.float64)
    return {f"p{p}": float(np.percentile(arr, p)) for p in PERCENTILES}


def summarize_level(
    concurrency: int,
    reports: Sequence[WorkerReport],
    window: Window,
    node_before: NodeSample,
    node_after: NodeSample,
    control_before: CpuSample | None,
    control_after: CpuSample | None,
) -> LevelResult:
    """測定の区間に返った要求（done_at が区間の内）だけで，1 つの水準の指標を求める．"""
    in_window = [
        r for rep in reports for r in rep.records if window.start <= r.done_at < window.end
    ]
    ok = [r for r in in_window if r.error is None]
    errors = [r for r in in_window if r.error is not None]
    error_kinds: dict[str, int] = {}
    for r in errors:
        kind = (r.error or "").split(":", 1)[0]
        error_kinds[kind] = error_kinds.get(kind, 0) + 1
    latencies = [r.done_at - r.sent_at for r in ok]
    throughput = len(ok) / window.length
    latency_mean = statistics.fmean(latencies) if latencies else None
    # プロセスの CPU 時間の差は，前後の /healthz に同じプロセスが答えたときだけ意味を持つ（複数のプロセスのノード．Iteration 10）
    node_proc_util = None
    if (
        node_before.process_cpu_s is not None
        and node_after.process_cpu_s is not None
        and node_before.pid == node_after.pid
    ):
        node_proc_util = (node_after.process_cpu_s - node_before.process_cpu_s) / window.length
    node_cgroup_util = None
    if node_before.cgroup_cpu_s is not None and node_after.cgroup_cpu_s is not None:
        node_cgroup_util = (node_after.cgroup_cpu_s - node_before.cgroup_cpu_s) / window.length
    host = busy_fractions(node_before.host_cpu, node_after.host_cpu)
    control = busy_fractions(control_before, control_after)
    ordered = sorted(reports, key=lambda rep: rep.index)
    return LevelResult(
        concurrency=concurrency,
        procs=len(ordered),
        concurrency_per_proc=[rep.concurrency for rep in ordered],
        measure_s=window.length,
        n_ok=len(ok),
        n_errors=len(errors),
        error_kinds=error_kinds,
        throughput_rps=throughput,
        latency_s=_percentiles(latencies),
        latency_mean_s=latency_mean,
        node_s=_percentiles([r.node_s for r in ok if r.node_s is not None]),
        # 閉じた系の Little の法則：処理量 × 平均の待ち時間 ≈ 同時の要求の数（1 から離れれば負荷生成の側の待ちがある）
        little_ratio=None if latency_mean is None else throughput * latency_mean / concurrency,
        node_proc_util=node_proc_util,
        node_cgroup_util=node_cgroup_util,
        node_workers=node_after.workers,
        node_host_busy=None if host is None else host[0],
        node_host_softirq=None if host is None else host[1],
        node_n_cpu=node_after.n_cpu,
        healthz_rtt_s=[node_before.rtt_s, node_after.rtt_s],
        generator_cpu_util_per_proc=[rep.cpu_s / window.length for rep in ordered],
        control_cpu_busy=None if control is None else control[0],
        score_mismatches=sum(rep.score_mismatches for rep in ordered),
    )


# ── 負荷生成のプロセス ────────────────────────────────────────────────────────


def _rusage_cpu_s() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


async def _sleep_until(at: float) -> None:
    await asyncio.sleep(max(0.0, at - time.time()))


async def drive_closed_loop(
    client: httpx.AsyncClient,
    target: BenchTarget,
    bodies: Sequence[tuple[str, bytes]],
    concurrency: int,
    t0: float,
    window: Window,
    index: int = 0,
) -> WorkerReport:
    """t0 から window.end まで，concurrency 本の流れで要求を送り続ける（閉じた系）．本文は順に使い，尽きたら先頭へ戻る．"""
    records: list[RequestRecord] = []
    scores: dict[str, dict[str, float]] = {}
    mismatches = 0
    next_body = 0
    cpu_at: dict[str, float] = {}
    expected_shards = set(target.shard_ids)

    async def sample_cpu() -> None:
        await _sleep_until(window.start)
        cpu_at["start"] = _rusage_cpu_s()
        await _sleep_until(window.end)
        cpu_at["end"] = _rusage_cpu_s()

    async def stream() -> None:
        nonlocal next_body, mismatches
        await _sleep_until(t0)
        while time.time() < window.end:
            qid, body = bodies[next_body % len(bodies)]
            next_body += 1
            sent_at = time.time()
            try:
                response = await client.post(
                    f"{target.url}/v1/probe",
                    content=body,
                    headers=JSON_HEADERS,
                    timeout=REQUEST_TIMEOUT_S,
                )
                response.raise_for_status()
                parsed = ProbeResponse.model_validate_json(response.content)
            # 応答の形が壊れていたとき（pydantic の ValidationError は ValueError）も 1 要求の失敗として数え，流れは止めない
            except (httpx.HTTPError, ValueError) as exc:
                records.append(
                    RequestRecord(qid, sent_at, time.time(), None, f"{type(exc).__name__}: {exc}")
                )
                continue
            done_at = time.time()
            if parsed.node_id != target.node_id or set(parsed.scores) != expected_shards:
                error = f"UnexpectedResponse: node {parsed.node_id}, shards {sorted(parsed.scores)}"
                records.append(RequestRecord(qid, sent_at, done_at, None, error))
                continue
            records.append(RequestRecord(qid, sent_at, done_at, parsed.duration_s, None))
            seen = scores.setdefault(qid, parsed.scores)
            # 同じ問の得点は負荷によらず同じはず（索引は読み取りだけ）．違えば数える
            mismatches += seen != parsed.scores

    sampler = asyncio.create_task(sample_cpu())
    await asyncio.gather(*(stream() for _ in range(concurrency)))
    await sampler
    return WorkerReport(
        index=index,
        concurrency=concurrency,
        cpu_s=cpu_at["end"] - cpu_at["start"],
        records=records,
        scores=scores,
        score_mismatches=mismatches,
    )


def _worker_main(
    index: int,
    target: BenchTarget,
    data_dir: Path,
    qids: list[str],
    concurrency: int,
    ready: Queue[int],
    start: Event,
    t0_value: Synchronized[float],
    warmup_s: float,
    measure_s: float,
    reports: Queue[WorkerReport],
) -> None:
    """負荷生成の子のプロセスの入口（spawn で起動する）．本文を用意し，親の合図の後の t0 から送る．"""
    logging.basicConfig(
        level=logging.WARNING, format=f"%(asctime)s %(levelname)s %(name)s[b{index}]: %(message)s"
    )
    paths = dataset_paths(data_dir, DATASET)
    bodies = build_probe_bodies(
        qids, paths.query_ids, paths.query_embeddings(target.encoder), target.shard_ids
    )

    async def run() -> WorkerReport:
        limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
        async with httpx.AsyncClient(limits=limits) as client:
            ready.put(index)
            await asyncio.to_thread(start.wait)
            t0 = t0_value.value
            window = Window(t0 + warmup_s, t0 + warmup_s + measure_s)
            return await drive_closed_loop(
                client, target, bodies, concurrency, t0, window, index=index
            )

    reports.put(asyncio.run(run()))


# ── 親（水準ごとの実行） ──────────────────────────────────────────────────────


async def read_node_sample(client: httpx.AsyncClient, url: str) -> NodeSample:
    """対象のノードの /healthz から，プロセスとデバイスの CPU 時間の累計を読む．"""
    start = time.perf_counter()
    body = await fetch_healthz(client, url)
    rtt = time.perf_counter() - start
    if body is None:
        return NodeSample(None, None, None, rtt)
    cpu = body.get("process_cpu_s")
    host = body.get("host_cpu")
    host_sample = None
    n_cpu = None
    if isinstance(host, dict):
        host_sample = CpuSample(
            busy=int(host["busy"]), total=int(host["total"]), softirq=int(host["softirq"])
        )
        n_cpu = int(host["n_cpu"]) if host.get("n_cpu") is not None else None
    cgroup = body.get("cgroup_cpu_s")
    pid = body.get("pid")
    workers = body.get("workers")
    return NodeSample(
        float(cpu) if isinstance(cpu, int | float) else None,
        host_sample,
        n_cpu,
        rtt,
        cgroup_cpu_s=float(cgroup) if isinstance(cgroup, int | float) else None,
        pid=pid if isinstance(pid, int) else None,
        workers=workers if isinstance(workers, int) else None,
    )


async def resolve_target(client: httpx.AsyncClient, placement: Placement, host: str) -> BenchTarget:
    """配置から対象のノードを探し，/v1/profile でシャードが配置と一致し，検索器が 1 種類であることを確かめる．"""
    node = next((n for n in placement.nodes if n.host == host), None)
    if node is None:
        raise ValueError(f"host {host!r} is not in the placement")
    response = await client.get(f"{node.url}/v1/profile", timeout=REQUEST_TIMEOUT_S)
    response.raise_for_status()
    profile = ProfileResponse.model_validate_json(response.content)
    announced = sorted(s.shard_id for s in profile.shards)
    if announced != sorted(node.shard_ids):
        raise RuntimeError(f"{host} announces shards {announced}, placement has {node.shard_ids}")
    encoders = {s.encoder for s in profile.shards}
    if len(encoders) != 1:
        raise RuntimeError(f"{host} holds shards of several encoders: {sorted(encoders)}")
    return BenchTarget(host, node.url, profile.node_id, list(node.shard_ids), encoders.pop())


async def run_level(
    target: BenchTarget,
    data_dir: Path,
    qids: Sequence[str],
    concurrency: int,
    max_procs: int,
    warmup_s: float,
    measure_s: float,
    client: httpx.AsyncClient,
) -> tuple[LevelResult, list[WorkerReport]]:
    """1 つの水準を流す．client は親が /healthz を読むためだけに使う（負荷の接続とは別）．"""
    procs_n = min(max_procs, concurrency)
    per_proc = split_parallel(concurrency, procs_n)
    ctx = mp.get_context("spawn")
    ready: Queue[int] = ctx.Queue()
    reports_q: Queue[WorkerReport] = ctx.Queue()
    start = ctx.Event()
    t0_value: Synchronized[float] = ctx.Value("d", 0.0)
    procs = [
        ctx.Process(
            target=_worker_main,
            args=(
                i,
                target,
                data_dir,
                list(qids[i::procs_n]),
                per_proc[i],
                ready,
                start,
                t0_value,
                warmup_s,
                measure_s,
                reports_q,
            ),
            name=f"atrium-bench-{i}",
        )
        for i in range(procs_n)
    ]
    reports: list[WorkerReport] = []
    try:
        for p in procs:
            p.start()
        for _ in range(procs_n):
            await asyncio.to_thread(ready.get, True, READY_TIMEOUT_S)
        t0 = time.time() + START_DELAY_S
        t0_value.value = t0
        start.set()
        window = Window(t0 + warmup_s, t0 + warmup_s + measure_s)
        await _sleep_until(window.start)
        control_before = read_device_cpu()
        node_before = await read_node_sample(client, target.url)
        await _sleep_until(window.end)
        control_after = read_device_cpu()
        node_after = await read_node_sample(client, target.url)
        for _ in range(procs_n):
            reports.append(await asyncio.to_thread(reports_q.get, True, REPORT_GRACE_S))
        for p in procs:
            await asyncio.to_thread(p.join)
        failed = [(i, p.exitcode) for i, p in enumerate(procs) if p.exitcode != 0]
        if failed:
            raise RuntimeError(f"bench processes exited abnormally (index, exit code): {failed}")
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
                p.join()
    result = summarize_level(
        concurrency, reports, window, node_before, node_after, control_before, control_after
    )
    return result, reports


async def run_bench(
    placement: Placement,
    data_dir: Path,
    target_host: str,
    concurrency_order: Sequence[int],
    seed: int,
    max_procs: int,
    warmup_s: float,
    measure_s: float,
    git_head: str,
) -> tuple[dict[str, object], dict[str, dict[str, float]]]:
    """水準を concurrency_order の順に流し，（node_bench.json の中身，scores.json の中身）を返す．"""
    questions = load_questions(dataset_paths(data_dir, DATASET).questions, None)
    qids = question_order([q.qid for q in questions], seed)
    levels: list[dict[str, object]] = []
    scores: dict[str, dict[str, float]] = {}
    cross_level_mismatches = 0
    async with httpx.AsyncClient() as client:
        target = await resolve_target(client, placement, target_host)
        logger.info("target %s (%s): shards %s", target.host, target.node_id, target.shard_ids)
        for concurrency in concurrency_order:
            result, reports = await run_level(
                target, data_dir, qids, concurrency, max_procs, warmup_s, measure_s, client
            )
            for rep in reports:
                for qid, s in rep.scores.items():
                    cross_level_mismatches += scores.setdefault(qid, s) != s
            logger.info(
                "C=%d: %.1f req/s, p95 %.4f s, node CPU %s (cgroup %s), errors %d",
                concurrency,
                result.throughput_rps,
                result.latency_s["p95"] or float("nan"),
                result.node_proc_util,
                result.node_cgroup_util,
                result.n_errors,
            )
            levels.append(asdict(result))
            await asyncio.sleep(LEVEL_PAUSE_S)
    bench = {
        "kind": "node_bench",
        "git_head": git_head,
        "dataset": DATASET,
        "target": asdict(target),
        "concurrency_order": list(concurrency_order),
        "seed": seed,
        "n_questions": len(qids),
        "max_procs": max_procs,
        "warmup_s": warmup_s,
        "measure_s": measure_s,
        "request_timeout_s": REQUEST_TIMEOUT_S,
        "cross_level_score_mismatches": cross_level_mismatches,
        "levels": levels,
    }
    return bench, scores


def write_outputs(
    out_dir: Path, bench: dict[str, object], scores: dict[str, dict[str, float]]
) -> None:
    """node_bench.json と scores.json を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "node_bench.json").write_text(
        json.dumps(bench, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "scores.json").write_text(json.dumps(scores, sort_keys=True), encoding="utf-8")


def render_table(bench: dict[str, object]) -> str:
    """水準ごとの主な指標の表（Markdown）．"""
    lines = [
        "| C | procs | req/s | p50 s | p95 s | node p95 s | node proc CPU | node cgroup CPU | node host busy | errors | Little |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    levels = bench["levels"]
    assert isinstance(levels, list)
    for lv in levels:
        lines.append(
            "| {concurrency} | {procs} | {throughput_rps:.1f} | {p50} | {p95} | {np95} | {cpu} | {cgroup} | {host} | {n_errors} | {little} |".format(
                concurrency=lv["concurrency"],
                procs=lv["procs"],
                throughput_rps=lv["throughput_rps"],
                p50=_fmt(lv["latency_s"]["p50"]),
                p95=_fmt(lv["latency_s"]["p95"]),
                np95=_fmt(lv["node_s"]["p95"]),
                cpu=_fmt(lv["node_proc_util"]),
                cgroup=_fmt(lv["node_cgroup_util"]),
                host=_fmt(lv["node_host_busy"]),
                n_errors=lv["n_errors"],
                little=_fmt(lv["little_ratio"]),
            )
        )
    return "\n".join(lines)


def _fmt(value: object) -> str:
    return "-" if not isinstance(value, int | float) else f"{value:.4f}"
