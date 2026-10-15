"""同じデバイスの上で，問を複数の質問者のプロセスに分けて同時に流す（Iteration 8．experiment.requester_procs）．

`atrium run`（親のプロセス）は，問を順番に配り（i 番目の問を i mod N 番目のプロセスへ），parallel（全体の同時の問の数）を
ほぼ均等に割る．各プロセスは自分で HTTP クライアント・ルーター・埋め込みを用意し，全プロセスの準備が済んでから
親の合図で一斉に流し始める（準備の時間を処理量に含めないため）．各プロセスは結果を results.part{i}.jsonl に書き，
親が最後に results.jsonl へまとめる．

あわせて，測定の区間の前後で専門家の側の CPU 時間（ノードの /healthz の process_cpu_s）と質問者のデバイス全体の
CPU 時間（/proc/stat）を読む関数を持つ．1 プロセスの実験（requester_procs = 1）でも同じ指標を記録する．
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import queue
import resource
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass
from multiprocessing.process import BaseProcess
from multiprocessing.queues import Queue
from multiprocessing.synchronize import Event
from pathlib import Path

import httpx

from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.manifest import Placement, read_placement
from atrium.requester import run_questions

logger = logging.getLogger(__name__)

# 全プロセスの準備（シャードの発見・ルーターと埋め込みの読み込み）を待つ上限．EnronQA では数秒で済む
READY_TIMEOUT_S = 600.0
# 子の状態を確かめる間隔（子が落ちたときに親が待ち続けないため）
POLL_INTERVAL_S = 1.0
HEALTH_TIMEOUT_S = 10.0
PROC_STAT = Path("/proc/stat")


@dataclass(frozen=True)
class CpuSample:
    """/proc/stat の先頭の行（全 CPU の合計．単位は jiffies）．"""

    busy: int
    total: int
    softirq: int


@dataclass(frozen=True)
class WorkerResult:
    """1 つの質問者のプロセスが問を流した区間の記録．"""

    index: int
    n_questions: int
    parallel: int
    failures: int
    cpu_user_s: float
    cpu_sys_s: float
    wall_s: float
    finished_at: float


@dataclass(frozen=True)
class ProcsOutcome:
    """複数のプロセスで流した結果．started_at は一斉に流し始めた時刻，finished_at は最後のプロセスが終えた時刻．"""

    started_at: float
    finished_at: float
    workers: list[WorkerResult]
    node_cpu_before: dict[str, float | None]
    node_cpu_after: dict[str, float | None]
    control_cpu_before: CpuSample | None
    control_cpu_after: CpuSample | None


def split_parallel(parallel: int, procs: int) -> list[int]:
    """全体の同時の問の数を，合計が parallel のまま各プロセスへほぼ均等に割る（余りは前のプロセスから 1 ずつ）．"""
    if procs < 1 or parallel < procs:
        raise ValueError(f"need parallel >= procs >= 1: parallel={parallel}, procs={procs}")
    base, extra = divmod(parallel, procs)
    return [base + (1 if i < extra else 0) for i in range(procs)]


def split_questions(questions: Sequence[Question], procs: int) -> list[list[Question]]:
    """問を順番に配る（i 番目の問を i mod procs 番目へ）．並べ替えた後の順を保ち，各プロセスの問の数の差は 1 以下．"""
    return [list(questions[i::procs]) for i in range(procs)]


def part_path(out_path: Path, index: int) -> Path:
    """index 番目のプロセスが結果を書くファイル（results.jsonl なら results.part{index}.jsonl）．"""
    return out_path.with_name(f"{out_path.stem}.part{index}{out_path.suffix}")


def merge_part_files(out_path: Path) -> int:
    """プロセスごとの結果を out_path の末尾へ移して消し，移した行の数を返す．

    親が途中で落ちた後に同じ run_id で再開したときも，先に呼べば成功済みの問を飛ばせる．
    """
    parts = sorted(out_path.parent.glob(f"{out_path.stem}.part*{out_path.suffix}"))
    if not parts:
        return 0
    moved = 0
    with out_path.open("a", encoding="utf-8") as out:
        for part in parts:
            lines = [line for line in part.read_text(encoding="utf-8").splitlines() if line]
            out.writelines(line + "\n" for line in lines)
            moved += len(lines)
            part.unlink()
    return moved


def parse_proc_stat(text: str) -> CpuSample:
    """/proc/stat の先頭の cpu 行から，忙しい時間・全体の時間・softirq の時間を読む．

    列は user nice system idle iowait irq softirq steal guest guest_nice．guest は user に含まれるので足さない．
    """
    first = text.splitlines()[0].split()
    if first[0] != "cpu":
        raise ValueError(f"unexpected first line of /proc/stat: {first[:2]}")
    fields = [int(x) for x in first[1:9]]
    idle = fields[3] + fields[4]
    total = sum(fields)
    return CpuSample(busy=total - idle, total=total, softirq=fields[6])


def read_control_cpu() -> CpuSample | None:
    """このデバイス全体の CPU 時間の累計（コンテナの中でも /proc/stat はホスト全体を示す）．Linux 以外では None．"""
    if not PROC_STAT.exists():
        return None
    return parse_proc_stat(PROC_STAT.read_text(encoding="utf-8"))


def control_cpu_fractions(
    before: CpuSample | None, after: CpuSample | None
) -> dict[str, float | None]:
    """2 つの標本の間の，全 CPU に対する忙しい時間と softirq の時間の割合（0〜1）．"""
    if before is None or after is None or after.total <= before.total:
        return {"control_cpu_busy": None, "control_cpu_softirq": None}
    total = after.total - before.total
    return {
        "control_cpu_busy": (after.busy - before.busy) / total,
        "control_cpu_softirq": (after.softirq - before.softirq) / total,
    }


async def read_node_cpu_seconds(
    client: httpx.AsyncClient, placement: Placement
) -> dict[str, float | None]:
    """各ノードのプロセスの CPU 時間の累計（秒）．読めなかったノードは None にして警告を残す（実験は止めない）．"""

    async def fetch(url: str) -> float | None:
        try:
            response = await client.get(f"{url}/healthz", timeout=HEALTH_TIMEOUT_S)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("could not read the CPU time of %s: %s", url, exc)
            return None
        value = response.json().get("process_cpu_s")
        return float(value) if value is not None else None

    values = await asyncio.gather(*(fetch(node.url) for node in placement.nodes))
    return {node.host: v for node, v in zip(placement.nodes, values, strict=True)}


def expert_cpu_meta(
    before: dict[str, float | None], after: dict[str, float | None], wall_s: float
) -> dict[str, object]:
    """ノードごとの CPU 使用率（コアの数の単位．1.0 で 1 コア分）と，その最大値・中央値．"""
    utils = {
        host: (after_s - before_s) / wall_s
        for host, before_s in before.items()
        if before_s is not None and (after_s := after.get(host)) is not None
    }
    return {
        "expert_cpu_util": utils,
        "expert_cpu_util_max": max(utils.values()) if utils else None,
        "expert_cpu_util_median": statistics.median(utils.values()) if utils else None,
    }


def _worker_main(
    index: int,
    cfg: AtriumConfig,
    data_dir: Path,
    placement_path: Path,
    ollama_url: str,
    questions: list[Question],
    parallel: int,
    out_path: Path,
    ready: Queue[int],
    start: Event,
    results: Queue[WorkerResult],
) -> None:
    """子のプロセスの入口（spawn で起動するので，親のイベントループや接続を引き継がない）．"""
    logging.basicConfig(
        level=logging.INFO, format=f"%(asctime)s %(levelname)s %(name)s[w{index}]: %(message)s"
    )
    result = asyncio.run(
        _run_worker(
            index,
            cfg,
            data_dir,
            placement_path,
            ollama_url,
            questions,
            parallel,
            out_path,
            ready,
            start,
        )
    )
    results.put(result)


async def _run_worker(
    index: int,
    cfg: AtriumConfig,
    data_dir: Path,
    placement_path: Path,
    ollama_url: str,
    questions: list[Question],
    parallel: int,
    out_path: Path,
    ready: Queue[int],
    start: Event,
) -> WorkerResult:
    """自分の質問者を用意し，親の合図を待ってから，割り当てられた問を流す．"""
    # atrium.experiment はこのモジュールを読み込むので，循環を避けて子の中で読み込む
    from atrium.experiment import build_http_client, build_requester

    placement = read_placement(placement_path)
    async with build_http_client(cfg.experiment.http_pool, placement) as client:
        requester, _, _ = await build_requester(cfg, data_dir, placement, client, ollama_url)
        ready.put(index)
        await asyncio.to_thread(start.wait)
        cpu_before = resource.getrusage(resource.RUSAGE_SELF)
        wall_before = time.perf_counter()
        failures = await run_questions(requester, questions, out_path, parallel)
        wall_s = time.perf_counter() - wall_before
        cpu_after = resource.getrusage(resource.RUSAGE_SELF)
    return WorkerResult(
        index=index,
        n_questions=len(questions),
        parallel=parallel,
        failures=failures,
        cpu_user_s=cpu_after.ru_utime - cpu_before.ru_utime,
        cpu_sys_s=cpu_after.ru_stime - cpu_before.ru_stime,
        wall_s=wall_s,
        finished_at=time.time(),
    )


def _join_all(procs: list[BaseProcess]) -> None:
    """全ての子の終了を待つ（イベントループを止めないよう別のスレッドで呼ぶ）．"""
    for p in procs:
        p.join()


def _raise_if_a_worker_failed(procs: list[BaseProcess]) -> None:
    """異常終了した子があれば，その番号と終了コードを添えて止める．"""
    failed = [(i, p.exitcode) for i, p in enumerate(procs) if p.exitcode not in (None, 0)]
    if failed:
        raise RuntimeError(f"requester processes exited abnormally (index, exit code): {failed}")


async def _collect(
    q: Queue[int] | Queue[WorkerResult], n: int, procs: list[BaseProcess], timeout_s: float | None
) -> list[object]:
    """子から n 個を受け取る．子が異常終了したか，全員が終わったのに足りないか，時間切れなら止める．"""
    items: list[object] = []
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while len(items) < n:
        try:
            items.append(await asyncio.to_thread(q.get, True, POLL_INTERVAL_S))
            continue
        except queue.Empty:
            pass
        _raise_if_a_worker_failed(procs)
        if all(p.exitcode is not None for p in procs) and q.empty():
            raise RuntimeError(f"requester processes ended with {len(items)} of {n} reports")
        if deadline is not None and time.monotonic() > deadline:
            raise RuntimeError(f"requester processes did not report within {timeout_s} s")
    return items


async def run_questions_in_processes(
    cfg: AtriumConfig,
    data_dir: Path,
    placement_path: Path,
    placement: Placement,
    ollama_url: str,
    questions: Sequence[Question],
    out_path: Path,
    client: httpx.AsyncClient,
) -> ProcsOutcome:
    """問を experiment.requester_procs 個のプロセスに分けて同時に流し，結果を out_path にまとめる．

    client は親の HTTP クライアントで，測定の区間の前後にノードの CPU 時間を読むためだけに使う．
    """
    procs_n = cfg.experiment.requester_procs
    parallels = split_parallel(cfg.experiment.parallel, procs_n)
    parts = split_questions(questions, procs_n)
    ctx = mp.get_context("spawn")
    ready: Queue[int] = ctx.Queue()
    results: Queue[WorkerResult] = ctx.Queue()
    start = ctx.Event()
    procs: list[BaseProcess] = [
        ctx.Process(
            target=_worker_main,
            args=(
                i,
                cfg,
                data_dir,
                placement_path,
                ollama_url,
                parts[i],
                parallels[i],
                part_path(out_path, i),
                ready,
                start,
                results,
            ),
            name=f"atrium-requester-{i}",
        )
        for i in range(procs_n)
    ]
    try:
        for p in procs:
            p.start()
        await _collect(ready, procs_n, procs, READY_TIMEOUT_S)
        node_before = await read_node_cpu_seconds(client, placement)
        control_before = read_control_cpu()
        started_at = time.time()
        start.set()
        logger.info("started %d requester processes (parallel %s)", procs_n, parallels)
        reports = await _collect(results, procs_n, procs, None)
        control_after = read_control_cpu()
        node_after = await read_node_cpu_seconds(client, placement)
        await asyncio.to_thread(_join_all, procs)
        _raise_if_a_worker_failed(procs)
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
                p.join()
        # 失敗しても成功済みの問を残し，同じ run_id での再開で飛ばせるようにする
        merge_part_files(out_path)
    workers = sorted((r for r in reports if isinstance(r, WorkerResult)), key=lambda r: r.index)
    return ProcsOutcome(
        started_at=started_at,
        finished_at=max(w.finished_at for w in workers),
        workers=workers,
        node_cpu_before=node_before,
        node_cpu_after=node_after,
        control_cpu_before=control_before,
        control_cpu_after=control_after,
    )
