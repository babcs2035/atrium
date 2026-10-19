"""p0007 I6（plans/p0007 §6.5 E1）：障害のときに答えられる割合の時間変化を，P2P と中央で実機の壁時計で比べる（`atrium failure-run`）．

質問者のコンテナ（deploy で `cluster.node_admin: true` にしたもの）で動く．

- **P2P**：k 台（k ∈ {1, 3, 6, 10}．ホストの無作為抽出，種 12）を止め，質問を 1 問ずつ流し続けて，答えられた割合（正解のメールが上位 5 通に入った割合）を
  時間の関数として記録する．止め方は 2 種類．
  `availability`：`/v1/admin/availability` で止める（離脱中は 503．再現性が高い）．
  `container`：ノードのコンテナを実際に止めて再開する（`docker compose stop node` / `start node`．**止めるのは node サービスだけで，
  データ・image・Ollama は消さない**）．質問者のコンテナは ssh できないので，制御点の `scripts/remote/failure_run.sh` が
  `<out>/ctl/` のファイルで頼みを受け，ssh で実行する（ファイルの頼み方は `ContainerInjector`）．止めたノードは必ず再開する
  （この CLI の finally・SIGTERM と，failure_run.sh の EXIT の後始末の 2 段）．
  止まったデバイスの検出は，生存の確認（`/v1/profile`）を poll_interval_s ごとに繰り返したときの，止めた直後から最初に失敗するまでの時間で測り，
  実際の質問が最初にそのノードの失敗に当たるまでの時間と，失敗した質問の待ち時間も記録する．
  再開は，再開の頼みから `/v1/profile` が返事をするまでの時間（コンテナの起動と索引の読み込みを含む）．
- **中央**：中央の索引を持つプロセス（子プロセス）を止め（kill），再起動して最初の質問に答えられるまでの時間（インタプリタの起動・import・
  索引の読み込みを含む．`central_local` の `central_index` と同じ読み込み）を実測する．再起動のあとの答えられた割合は同じ質問で測る．
  冗長化（2 台）は，切り替え時間の見込み（検出の時間＋切り替えの操作．操作は測れないので仮定として明記）とデータ複製の費用を，同じ手順の実測値から見積もる（2 台目は立てない）．
- **主の指標**：障害の窓 [0, W] の中で答えられた割合の時間積分（割合 × 秒）．W = max(中央の再起動の時間, 止めておく時間) として，
  P2P も中央も同じ窓で積分する．中央は再起動が終わるまで 0，そのあと再起動後の割合．
  判定（事前登録）：測った全ての（止め方，k）で P2P の積分 > 中央の積分（`verdict_original`）．

使い方（実機の本番の手順は docs/d0003_experiment_workflow.md の §7．制御点で `scripts/remote/failure_run.sh`）:
    atrium --config config.yaml failure-run --data-dir /data --placement placement.json --out-dir results/<run_id> \\
        --ollama-url http://ollama:11434 [--ks 1,3,6,10] [--modes availability,container]

出力：`<out>/failure.json`（止め方・k ごとの時間変化・検出・再開・中央の再起動・冗長化の見積もり）・`verdict.json`・`sim_vs_live.json`・`run_meta.json`．
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import multiprocessing as mp
import random
import signal
import statistics
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Protocol

import httpx
import numpy as np

from atrium.benchmarks import Question
from atrium.config import AtriumConfig, load_config
from atrium.dynamics import (
    DEFAULT_BIN_S,
    DEFAULT_SEED,
    binned_rates,
    central_rate_integral,
    choose_names,
    fill_forward,
    integrate_rates,
    judge_failure,
)
from atrium.live_common import is_hit, load_test_questions, write_json
from atrium.paths import dataset_paths
from atrium.requester import RETRIEVE_TIMEOUT_S, Requester
from atrium.routing.central import CentralIndex
from atrium.verdict import write_verdict

logger = logging.getLogger(__name__)

RUN_META = "run_meta.json"
FAILURE_FILE = "failure.json"
SIM_VS_LIVE_FILE = "sim_vs_live.json"
DEFAULT_KS = (1, 3, 6, 10)
MODES = ("availability", "container")
# 生存の確認 1 回の打ち切り（秒）．応答の無いデバイスをこの時間で「止まった」とみなす検出の上限
LIVENESS_TIMEOUT_S = 2.0
# 冗長化の切り替えの操作（仮想 IP・DNS の切り替えなど）の時間．測れないので 0 と仮定し，見積もりに明記する
ASSUMED_SWITCH_ACTION_S = 0.0
# 頼みのファイルの待ち（秒）
CONTROL_POLL_S = 0.1
CONTROL_TIMEOUT_S = 900.0
CENTRAL_WORKER_TIMEOUT_S = 1800.0
CENTRAL_PROBE_K = 5


@dataclass(frozen=True)
class FailureParams:
    """測定の条件（コマンドの引数）．"""

    ks: tuple[int, ...] = DEFAULT_KS
    modes: tuple[str, ...] = MODES
    # 止める前に測る時間・止めておく時間・再開してから（窓の終わりと止めておく時間の後に）さらに測る時間（秒）
    baseline_s: float = 15.0
    down_s: float = 45.0
    post_s: float = 30.0
    bin_s: float = DEFAULT_BIN_S
    poll_interval_s: float = 0.2
    # 中央の答えられた割合を測る質問の数
    central_questions: int = 200
    ready_timeout_s: float = 300.0
    seed: int = DEFAULT_SEED


# ── 止め方 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InjectionResult:
    """止める・再開するの結果：頼んだ時刻と，終わった時刻（time.perf_counter）と，ホストごとの詳細．"""

    action: str
    hosts: tuple[str, ...]
    requested_at: float
    acked_at: float
    per_host: dict[str, dict[str, Any]] = field(default_factory=dict)


class FailureInjector(Protocol):
    """ノードを止める・再開する．止まっているノードを再び止める・動いているノードを再び再開するのは何もしない（冪等）．"""

    name: str

    async def stop(self, hosts: Sequence[str]) -> InjectionResult:
        """ホストのノードを止める（止まった状態になるまで待つ）．"""
        ...

    async def start(self, hosts: Sequence[str]) -> InjectionResult:
        """ホストのノードを再開する（再開の操作が終わるまで待つ．返事をするまでの待ちは呼び出し側が測る）．"""
        ...


class AvailabilityInjector:
    """`/v1/admin/availability` で止める（プロセスは生きていて，要求を 503 で断る）．"""

    name = "availability"

    def __init__(self, client: httpx.AsyncClient, url_of_host: Mapping[str, str]) -> None:
        """質問者の HTTP クライアントと，ホスト → ノードの URL．"""
        self._client = client
        self._url_of_host = url_of_host

    async def _set(self, hosts: Sequence[str], up: bool) -> InjectionResult:
        requested = time.perf_counter()

        async def one(host: str) -> tuple[str, dict[str, Any]]:
            begin = time.perf_counter()
            response = await self._client.post(
                f"{self._url_of_host[host]}/v1/admin/availability",
                json={"up": up},
                timeout=RETRIEVE_TIMEOUT_S,
            )
            response.raise_for_status()
            return host, {"rtt_s": time.perf_counter() - begin}

        done = await asyncio.gather(*(one(h) for h in hosts))
        return InjectionResult(
            "start" if up else "stop", tuple(hosts), requested, time.perf_counter(), dict(done)
        )

    async def stop(self, hosts: Sequence[str]) -> InjectionResult:
        """離脱させる．"""
        return await self._set(hosts, False)

    async def start(self, hosts: Sequence[str]) -> InjectionResult:
        """参加させる．"""
        return await self._set(hosts, True)


class ContainerInjector:
    """ノードのコンテナを実際に止めて再開する．制御点の failure_run.sh へ，`<ctl_dir>/req/<id>.json` の頼みで依頼する．

    頼み：`{"id", "action": "stop" | "start", "hosts": [...]}`．failure_run.sh は ssh で各ホストの
    `docker compose stop node` / `docker compose start node` だけを実行し（それ以外のコマンドは実行しない），
    `<ctl_dir>/ack/<id>.json`（`{"id", "ok", "hosts": {host: {"rc", "begin", "end"}}}`．時刻は制御点の epoch 秒）を書く．
    """

    name = "container"

    def __init__(
        self, ctl_dir: Path, poll_s: float = CONTROL_POLL_S, timeout_s: float = CONTROL_TIMEOUT_S
    ) -> None:
        """頼みのファイルを置くディレクトリ（failure_run.sh と共有する）．"""
        self._req = ctl_dir / "req"
        self._ack = ctl_dir / "ack"
        self._poll_s = poll_s
        self._timeout_s = timeout_s
        self._counter = itertools.count()
        self._req.mkdir(parents=True, exist_ok=True)
        self._ack.mkdir(parents=True, exist_ok=True)

    async def _request(self, action: str, hosts: Sequence[str]) -> InjectionResult:
        request_id = f"{time.time_ns()}-{next(self._counter)}"
        body = {"id": request_id, "action": action, "hosts": list(hosts)}
        tmp = self._req / f"{request_id}.tmp"
        tmp.write_text(json.dumps(body), encoding="utf-8")
        requested = time.perf_counter()
        tmp.rename(
            self._req / f"{request_id}.json"
        )  # 書きかけを読ませないため，書き終えてから名前を変える
        ack_path = self._ack / f"{request_id}.json"
        while not ack_path.exists():
            if time.perf_counter() - requested > self._timeout_s:
                raise TimeoutError(f"no ack for {action} {list(hosts)} within {self._timeout_s} s")
            await asyncio.sleep(self._poll_s)
        ack = json.loads(ack_path.read_text(encoding="utf-8"))
        acked = time.perf_counter()
        if not ack["ok"]:
            raise RuntimeError(f"{action} failed on {ack['hosts']}")
        return InjectionResult(action, tuple(hosts), requested, acked, ack["hosts"])

    async def stop(self, hosts: Sequence[str]) -> InjectionResult:
        """コンテナを止める（`docker compose stop node`）．"""
        return await self._request("stop", hosts)

    async def start(self, hosts: Sequence[str]) -> InjectionResult:
        """コンテナを再開する（`docker compose start node`）．"""
        return await self._request("start", hosts)


# ── 検出・再開の測定 ──────────────────────────────────────────────────────────


async def node_alive(client: httpx.AsyncClient, url: str) -> bool:
    """生存の確認：`/v1/profile` が 200 を返すか（離脱中は 503，コンテナが止まっていれば接続の失敗．打ち切りも「止まっている」）．"""
    try:
        response = await client.get(f"{url}/v1/profile", timeout=LIVENESS_TIMEOUT_S)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


async def measure_detection(
    client: httpx.AsyncClient, url: str, since: float, poll_interval_s: float, deadline_s: float
) -> float | None:
    """since（perf_counter）から，最初に生存の確認が失敗するまでの秒数（止めた直後なら，次の確認が失敗した時刻）．

    deadline_s の間に失敗しなければ None（止まっていない）．
    """
    while await node_alive(client, url):
        if time.perf_counter() - since > deadline_s:
            return None
        await asyncio.sleep(poll_interval_s)
    return time.perf_counter() - since


async def measure_ready(
    client: httpx.AsyncClient, url: str, since: float, poll_interval_s: float, timeout_s: float
) -> float | None:
    """since（perf_counter．再開の頼みを出した時刻）から，生存の確認が成功するまでの秒数（timeout_s を超えたら None）．"""
    while not await node_alive(client, url):
        if time.perf_counter() - since > timeout_s:
            return None
        await asyncio.sleep(poll_interval_s)
    return time.perf_counter() - since


# ── 中央の再起動 ──────────────────────────────────────────────────────────────


def _central_worker(config_path: str, data_dir: str, conn: Connection) -> None:
    """中央の索引を持つプロセス（子プロセスの入口）．索引を作って（読み込んで）から，検索の頼みに答える．"""
    cfg = load_config(Path(config_path))
    index = CentralIndex.from_corpus(cfg, dataset_paths(Path(data_dir), "enronqa"))
    conn.send(("ready", asdict(index.load), index.size_bytes()))
    while True:
        message = conn.recv()
        if message[0] == "exit":
            return
        _, embedding, k, text = message
        ids = index.search_ids(np.asarray(embedding, dtype=np.float32), k, text)
        conn.send(("result", [index.doc_ids[i] for i in ids]))


class CentralProcess:
    """中央の索引を持つ子プロセス（spawn．インタプリタの起動・import・索引の読み込みを再起動の時間に含めるため）．"""

    def __init__(self, config_path: Path, data_dir: Path) -> None:
        """子プロセスの引数（設定ファイルとデータのディレクトリ）．"""
        self._args = (str(config_path), str(data_dir))
        self._process: mp.process.BaseProcess | None = None
        self._conn: Connection | None = None
        self.load: dict[str, Any] = {}
        self.index_bytes = 0

    def start(self) -> float:
        """子プロセスを起動し，索引を読み込んで頼みを受けられるまでの秒数を返す．"""
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        began = time.perf_counter()
        self._process = ctx.Process(target=_central_worker, args=(*self._args, child), daemon=True)
        self._process.start()
        child.close()
        self._conn = parent
        if not parent.poll(CENTRAL_WORKER_TIMEOUT_S):
            raise TimeoutError("the central process did not become ready")
        _, self.load, self.index_bytes = parent.recv()
        return time.perf_counter() - began

    def search(self, embedding: Sequence[float], k: int, text: str | None) -> list[str]:
        """子プロセスの索引で上位 k 通の文書 ID を返す（失敗は例外）．"""
        if self._conn is None:
            raise RuntimeError("the central process is not running")
        self._conn.send(("search", list(embedding), k, text))
        if not self._conn.poll(CENTRAL_WORKER_TIMEOUT_S):
            raise TimeoutError("the central process did not answer")
        _, ids = self._conn.recv()
        return list(ids)

    def kill(self) -> float:
        """子プロセスを強制終了し（障害），止まるまでの秒数を返す．"""
        began = time.perf_counter()
        if self._process is not None:
            self._process.kill()
            self._process.join()
        if self._conn is not None:
            self._conn.close()
        self._process = None
        self._conn = None
        return time.perf_counter() - began

    def stop(self) -> None:
        """後始末（終了の頼みを送り，応じなければ強制終了する）．"""
        if self._conn is not None:
            try:
                self._conn.send(("exit",))
            except (BrokenPipeError, OSError):
                logger.warning("the central process was already gone")
        if self._process is not None:
            self._process.join(timeout=10)
            if self._process.is_alive():
                self._process.kill()
                self._process.join()
        self._process = None
        self._conn = None


def measure_central_restart(
    central: CentralProcess, probe: tuple[Sequence[float], str | None]
) -> dict[str, Any]:
    """中央の障害（プロセスの強制終了）から，再起動して最初の質問に答えられるまでの時間を測る．

    起動済みのプロセスで 1 回答えてから止め，止めた直後に起動し直して，起動の頼みから最初の質問に答えるまでを再起動の時間（restart_to_answer_s）とする．
    probe は（質問の埋め込み，質問の文字列）．索引の読み込み（`central_index` と同じ内訳）は load に残す．
    """
    embedding, text = probe
    central.search(embedding, CENTRAL_PROBE_K, text)
    kill_s = central.kill()
    began = time.perf_counter()
    ready_s = central.start()
    central.search(embedding, CENTRAL_PROBE_K, text)
    answered_s = time.perf_counter() - began
    return {
        "kill_s": kill_s,
        "restart_to_ready_s": ready_s,
        "restart_to_answer_s": answered_s,
        "index_load": central.load,
        "index_bytes": central.index_bytes,
    }


def central_hit_rate(
    central: CentralProcess,
    questions: Sequence[Question],
    gold_doc_of: Mapping[str, str],
    embed: Mapping[str, Sequence[float]],
    use_text: bool,
) -> float:
    """中央の索引（子プロセス）が，質問の正解のメールを上位 5 通に入れる割合．"""
    hits = [
        gold_doc_of[q.qid]
        in central.search(embed[q.qid], CENTRAL_PROBE_K, q.question if use_text else None)
        for q in questions
    ]
    return float(np.mean(hits)) if hits else 0.0


def redundancy_estimate(
    restart: Mapping[str, Any],
    detect_s: float | None,
    rate_after: float,
    window_s: float,
) -> dict[str, Any]:
    """冗長化（中央を 2 台にする）の見積もり．2 台目は立てず，同じ手順の実測値から計算する．

    - 切り替え時間 = 障害の検出（P2P の検出の測定の中央値）＋ 切り替えの操作（測れないので ASSUMED_SWITCH_ACTION_S と仮定）．
      待機側が既に索引を読み込んでいる（hot）場合．読み込んでいない（cold）場合は再起動と同じ restart_to_answer_s．
    - データ複製の費用 = 索引（埋め込みと本文）の複製が 1 つ増える（保存は 2 倍）．更新ごとに転送が 1 回増える．
    """
    hot_s = (detect_s if detect_s is not None else 0.0) + ASSUMED_SWITCH_ACTION_S
    return {
        "assumption": f"切り替えの操作は {ASSUMED_SWITCH_ACTION_S:g} 秒と仮定（測れない）．検出は P2P の生存の確認の測定の中央値",
        "hot_standby_switch_s": hot_s,
        "cold_standby_switch_s": restart["restart_to_answer_s"],
        "integral_hot_standby": central_rate_integral(hot_s, rate_after, window_s),
        "integral_cold_standby": central_rate_integral(
            restart["restart_to_answer_s"], rate_after, window_s
        ),
        "replication": {
            "index_bytes_per_copy": restart["index_bytes"],
            "copies": 2,
            "extra_storage_bytes": restart["index_bytes"],
            "extra_transfer_per_new_mail": "1 回（転送を 2 台へ）",
        },
    }


# ── P2P の障害 ────────────────────────────────────────────────────────────────


@dataclass
class FailureContext:
    """P2P の測定に使うもの．"""

    requester: Requester
    client: httpx.AsyncClient
    questions: list[Question]
    gold_doc_of: dict[str, str]
    # ホスト → ノードの URL，ホスト → そのノードが持つ受信箱（シャード）
    url_of_host: dict[str, str]
    inboxes_of_host: dict[str, list[str]]


async def run_fault(
    ctx: FailureContext,
    injector: FailureInjector,
    hosts: Sequence[str],
    params: FailureParams,
    window_s: float,
) -> dict[str, Any]:
    """hosts を止めて再開するあいだ質問を流し続け，答えられた割合の時間変化と，検出・再開の時間を測る．

    時刻 0 は止める操作が終わった時刻．止める前の baseline_s，止めておく down_s，再開してから
    max(窓 W の終わり, 止めた後 down_s + post_s) までを測る．途中で失敗しても，止めたノードは再開する．
    """
    cycle = itertools.cycle(ctx.questions)
    rows: list[dict[str, Any]] = []
    running = asyncio.Event()
    running.set()

    async def stream() -> None:
        while running.is_set():
            question = next(cycle)
            asked = time.perf_counter()
            record = await ctx.requester.process(question)
            rows.append(
                {
                    "asked_at": asked,
                    "hit": is_hit(record, ctx.gold_doc_of[question.qid]),
                    "bank": question.bank,
                    "e2e_s": record["timings"].get("e2e_s"),
                    "error": record["error"],
                    "failed_nodes": record.get("failed_nodes", 0) + record.get("probe_failed", 0),
                }
            )

    stream_task = asyncio.create_task(stream())
    stopped = False
    detect: dict[str, asyncio.Task[float | None]] = {}
    try:
        await asyncio.sleep(params.baseline_s)
        stop = await injector.stop(hosts)
        stopped = True
        t0 = stop.acked_at
        detect = {
            h: asyncio.create_task(
                measure_detection(
                    ctx.client, ctx.url_of_host[h], t0, params.poll_interval_s, params.down_s
                )
            )
            for h in hosts
        }
        await asyncio.sleep(max(0.0, t0 + params.down_s - time.perf_counter()))
        start = await injector.start(hosts)
        stopped = False
        ready = {
            h: asyncio.create_task(
                measure_ready(
                    ctx.client,
                    ctx.url_of_host[h],
                    start.requested_at,
                    params.poll_interval_s,
                    params.ready_timeout_s,
                )
            )
            for h in hosts
        }
        ready_s = {h: await t for h, t in ready.items()}
        end_at = t0 + max(window_s, params.down_s) + params.post_s
        await asyncio.sleep(max(0.0, end_at - time.perf_counter()))
        detect_s = {h: await t for h, t in detect.items()}
    finally:
        running.clear()
        await stream_task
        for task in detect.values():
            task.cancel()
        if stopped:
            # 途中で失敗しても止めたノードは再開する（データ・image は消えていない．compose start で戻る）
            await injector.start(hosts)
    return summarize_fault(
        rows, t0, stop, start, detect_s, ready_s, hosts, params, window_s, ctx.inboxes_of_host
    )


def summarize_fault(
    rows: Sequence[Mapping[str, Any]],
    t0: float,
    stop: InjectionResult,
    start: InjectionResult,
    detect_s: Mapping[str, float | None],
    ready_s: Mapping[str, float | None],
    hosts: Sequence[str],
    params: FailureParams,
    window_s: float,
    inboxes_of_host: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """質問の記録から，止める前・止めている間の答えられた割合と，時間積分（窓 [0, window_s]）・検出・再開をまとめる．

    質問の時刻は t0（止める操作の終わり）からの秒．止める前の割合は baseline の質問の平均（積分の基準）．
    """
    rel = [r["asked_at"] - t0 for r in rows]
    hits = [bool(r["hit"]) for r in rows]
    # 止める操作の途中（docker stop の待ちなど）の質問は平常に含めない
    baseline = [h for r, h in zip(rows, hits, strict=True) if r["asked_at"] < stop.requested_at]
    during = [h for t, h in zip(rel, hits, strict=True) if 0 <= t < params.down_s]
    after_start = start.acked_at - t0
    horizon = max(window_s, params.down_s) + params.post_s
    rates = binned_rates(rel, hits, -params.baseline_s, horizon, params.bin_s)
    base_rate = float(np.mean(baseline)) if baseline else 0.0
    filled = fill_forward(rates, base_rate)
    integral = integrate_rates(filled, -params.baseline_s, params.bin_s, 0.0, window_s)
    down_inboxes = {i for h in hosts for i in inboxes_of_host[h]}
    stream_down = [
        r["bank"] in down_inboxes for t, r in zip(rel, rows, strict=True) if 0 <= t < params.down_s
    ]
    failed_rows = [
        (t, r) for t, r in zip(rel, rows, strict=True) if r["failed_nodes"] > 0 and t >= 0
    ]
    first_failed = failed_rows[0] if failed_rows else None
    return {
        "hosts": list(hosts),
        "k": len(hosts),
        "stop": {
            "requested_to_acked_s": stop.acked_at - stop.requested_at,
            "per_host": stop.per_host,
        },
        "start": {
            "requested_at_s": start.requested_at - t0,
            "requested_to_acked_s": start.acked_at - start.requested_at,
            "acked_at_s": after_start,
            "per_host": start.per_host,
        },
        "detection_s": dict(detect_s),
        "detection_s_p50": statistics.median(v for v in detect_s.values() if v is not None)
        if any(v is not None for v in detect_s.values())
        else None,
        "ready_s": dict(ready_s),
        "ready_s_max": max((v for v in ready_s.values() if v is not None), default=None),
        "first_failed_request_s": None if first_failed is None else first_failed[0],
        "first_failed_request_e2e_s": None if first_failed is None else first_failed[1]["e2e_s"],
        "n_questions": len(rows),
        "n_errors": sum(r["error"] is not None for r in rows),
        "baseline_hit_rate": base_rate if baseline else None,
        "fault_hit_rate": float(np.mean(during)) if during else None,
        # 止めている間の質問のうち，正解の受信箱が止まったノードにあった割合（オフラインの予測の入力）
        "fault_gold_down_share": float(np.mean(stream_down)) if stream_down else None,
        "bin_s": params.bin_s,
        "bin_start_s": -params.baseline_s,
        "rates": filled,
        "window_s": window_s,
        "integral": integral,
        "integral_relative": integral / (base_rate * window_s)
        if base_rate > 0 and window_s > 0
        else None,
    }


# ── 全体 ──────────────────────────────────────────────────────────────────────


def predict_fault_hit_rate(baseline_rate: float, gold_down_share: float) -> float:
    """オフラインの予測（churn_eval の hit_given_gold_online と同じ考え方）：正解の受信箱が止まった問は答えられず，残りは平常の割合．"""
    return baseline_rate * (1.0 - gold_down_share)


async def run_failure(
    ctx: FailureContext,
    injectors: Mapping[str, FailureInjector],
    central: CentralProcess,
    central_questions: Sequence[Question],
    embeddings: Mapping[str, Sequence[float]],
    params: FailureParams,
    out_dir: Path,
    use_text: bool,
) -> dict[str, Any]:
    """中央の再起動を測り，止め方 × k で P2P の障害を測って，failure.json・verdict.json・sim_vs_live.json を書く．"""
    first = ctx.questions[0]
    restart = await asyncio.to_thread(
        measure_central_restart,
        central,
        (embeddings[first.qid], first.question if use_text else None),
    )
    rate_after = await asyncio.to_thread(
        central_hit_rate, central, central_questions, ctx.gold_doc_of, embeddings, use_text
    )
    window_s = max(restart["restart_to_answer_s"], params.down_s)
    hosts_all = sorted(ctx.url_of_host)
    p2p_rows: list[dict[str, Any]] = []
    for mode in params.modes:
        for k in params.ks:
            hosts = choose_names(hosts_all, k, params.seed)
            result = await run_fault(ctx, injectors[mode], hosts, params, window_s)
            central_integral = central_rate_integral(
                restart["restart_to_answer_s"], rate_after, window_s
            )
            p2p_rows.append(
                {
                    "mode": mode,
                    **result,
                    "central_integral": central_integral,
                    "central_integral_relative": central_integral / (rate_after * window_s)
                    if rate_after > 0
                    else None,
                }
            )
    detections = [r["detection_s_p50"] for r in p2p_rows if r["detection_s_p50"] is not None]
    detect_s = statistics.median(detections) if detections else None
    verdict = write_verdict(
        out_dir,
        judge_failure(
            [
                {
                    "k": f"{r['mode']}:k={r['k']}",
                    "p2p_integral": r["integral"],
                    "central_integral": r["central_integral"],
                }
                for r in p2p_rows
            ]
        ),
    )
    write_json(
        out_dir / FAILURE_FILE,
        {
            "params": {
                "ks": list(params.ks),
                "modes": list(params.modes),
                "baseline_s": params.baseline_s,
                "down_s": params.down_s,
                "post_s": params.post_s,
                "bin_s": params.bin_s,
                "poll_interval_s": params.poll_interval_s,
                "seed": params.seed,
                "unreachable_request_timeout_s": RETRIEVE_TIMEOUT_S,
                "note": (
                    "docker compose stop ではホストは到達できたままなので接続は即座に拒否される．"
                    "応答の無いデバイスへの要求は RETRIEVE_TIMEOUT_S まで待つが，この実験では測っていない"
                ),
            },
            "window_s": window_s,
            "central": {
                "restart": restart,
                "hit_rate_after_restart": rate_after,
                "integral": central_rate_integral(
                    restart["restart_to_answer_s"], rate_after, window_s
                ),
                "redundancy": redundancy_estimate(restart, detect_s, rate_after, window_s),
            },
            "p2p": p2p_rows,
        },
    )
    write_json(out_dir / SIM_VS_LIVE_FILE, sim_vs_live(p2p_rows))
    return verdict


def sim_vs_live(p2p_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """オフラインの予測（plans/p0007 §10）と実機の値：止めている間の答えられた割合（予測 = 平常の割合 × 正解の受信箱が動いている割合）．"""
    rows = []
    for r in p2p_rows:
        if r["baseline_hit_rate"] is None or r["fault_hit_rate"] is None:
            continue
        predicted = predict_fault_hit_rate(
            r["baseline_hit_rate"], r["fault_gold_down_share"] or 0.0
        )
        rows.append(
            {
                "mode": r["mode"],
                "k": r["k"],
                "predicted_fault_hit_rate": predicted,
                "live_fault_hit_rate": r["fault_hit_rate"],
                "difference": r["fault_hit_rate"] - predicted,
            }
        )
    return {
        "rows": rows,
        "max_abs_difference": max((abs(r["difference"]) for r in rows), default=None),
        "causes": "止めている間の質問の数が少ないと割合が揺れる．再開の前後・検出の遅れの質問は予測に含まれない",
    }


async def run_failure_command(
    cfg: AtriumConfig,
    config_path: Path,
    data_dir: Path,
    placement_path: Path,
    out_dir: Path,
    ollama_url: str,
    params: FailureParams,
) -> dict[str, Any]:
    """質問者・中央のプロセス・止め方を組み立て，run_failure を流す（コンテナの中で `atrium failure-run` から呼ばれる）．"""
    from atrium.experiment import build_http_client, build_requester, read_image_git_head
    from atrium.manifest import read_placement

    paths = dataset_paths(data_dir, "enronqa")
    placement = read_placement(placement_path)
    questions, gold = load_test_questions(paths)
    out_dir.mkdir(parents=True, exist_ok=True)
    shuffled = list(questions)
    random.Random(params.seed).shuffle(shuffled)
    gold_doc_of = {q.qid: gold[q.qid]["path"] for q in questions}
    central = CentralProcess(config_path, data_dir)
    async with build_http_client(cfg.experiment.http_pool, placement) as client:
        requester, _, _ = await build_requester(
            cfg, data_dir, placement, client, ollama_url, tolerate_node_failures=True
        )
        encoder = cfg.data.require_enronqa().encoder
        embeddings = {
            q.qid: [float(x) for x in requester.embedder.embed(q)[encoder]] for q in shuffled
        }
        url_of_host = {n.host: n.url for n in placement.nodes}
        ctx = FailureContext(
            requester=requester,
            client=client,
            questions=shuffled,
            gold_doc_of=gold_doc_of,
            url_of_host=url_of_host,
            inboxes_of_host={n.host: list(n.shard_ids) for n in placement.nodes},
        )
        injectors: dict[str, FailureInjector] = {
            "availability": AvailabilityInjector(client, url_of_host),
            "container": ContainerInjector(out_dir / "ctl"),
        }
        write_json(
            out_dir / RUN_META,
            {
                "run_id": out_dir.name,
                "kind": "failure_run",
                "git_head": read_image_git_head(),
                "routing": cfg.experiment.routing,
                "answer_mode": cfg.experiment.answer_mode,
                "n_nodes": len(placement.nodes),
                "config": cfg.model_dump(mode="json"),
            },
        )
        central.start()
        try:
            return await run_failure(
                ctx,
                injectors,
                central,
                shuffled[: params.central_questions],
                embeddings,
                params,
                out_dir,
                cfg.retrieval.fusion == "rrf",
            )
        finally:
            central.stop()
            # 全てのノードを動いている状態にして終える（止めたまま終わらない）
            for injector in injectors.values():
                if injector.name == "availability":
                    await injector.start(sorted(url_of_host))


def install_sigterm_cancel(task: asyncio.Task[Any]) -> None:
    """SIGTERM（docker stop）を受けたら実行を中断させる（run_fault の finally が止めたノードを再開する）．"""
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
