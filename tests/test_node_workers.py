"""experiment.node_workers（専門家ノードを複数のプロセスで動かす．Iteration 10）と，ノードの全体の CPU の測り方の仕様．"""

from __future__ import annotations

import multiprocessing as mp
import os
import socket
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from pydantic import ValidationError

from atrium.config import AtriumConfig, ChurnConfig
from atrium.cpu_stat import CpuSample, parse_cgroup_usage_s
from atrium.manifest import NodeAssignment, Placement
from atrium.node import WorkerArgs, create_app, serve_workers
from atrium.node_bench import NodeSample, RequestRecord, Window, WorkerReport, summarize_level
from atrium.requester_procs import read_node_cpu_seconds
from atrium.store import load_shard
from tests.conftest import REPO_ROOT, with_experiment, write_faiss_shard

DIM = 8
# 新しい接続を張り直して /healthz を読む回数の上限（2 個のプロセスの両方が答えるまで）
MAX_PROBES = 200
STARTUP_TIMEOUT_S = 60.0


def _unit(rng: np.random.Generator, n: int) -> np.ndarray:
    x = rng.normal(size=(n, DIM)).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


# ── 設定 ─────────────────────────────────────────────────────────────────────


def test_default_node_workers_is_one(cfg: AtriumConfig) -> None:
    assert cfg.experiment.node_workers == 1


def test_zero_node_workers_is_rejected(cfg: AtriumConfig) -> None:
    raw = cfg.experiment.model_dump() | {"node_workers": 0}
    with pytest.raises(ValidationError):
        type(cfg.experiment).model_validate(raw)


def test_churn_with_several_node_workers_is_rejected(cfg: AtriumConfig) -> None:
    raw = cfg.experiment.model_dump() | {
        "dataset": "enronqa",
        "answer_mode": "retrieval_only",
        "routing": "flood_score",
        "parallel": 1,
        "node_workers": 2,
        "churn": ChurnConfig(availability=0.9).model_dump(),
    }
    with pytest.raises(ValidationError, match="churn requires experiment.node_workers = 1"):
        type(cfg.experiment).model_validate(raw)


# ── CPU の測り方 ─────────────────────────────────────────────────────────────


def test_parse_cgroup_usage_s_reads_usage_usec_in_seconds() -> None:
    text = "usage_usec 2500000\nuser_usec 2000000\nsystem_usec 500000\n"
    assert parse_cgroup_usage_s(text) == 2.5


def test_parse_cgroup_usage_s_rejects_a_file_without_usage() -> None:
    with pytest.raises(ValueError, match="usage_usec"):
        parse_cgroup_usage_s("user_usec 1\n")


def _empty_report() -> WorkerReport:
    return WorkerReport(
        index=0, concurrency=1, cpu_s=0.0, records=[], scores={}, score_mismatches=0
    )


def test_summarize_level_takes_node_cpu_from_the_cgroup_and_reports_workers() -> None:
    before = NodeSample(10.0, None, 8, 0.0, cgroup_cpu_s=100.0, pid=7, workers=2)
    after = NodeSample(12.0, None, 8, 0.0, cgroup_cpu_s=108.0, pid=7, workers=2)
    level = summarize_level(1, [_empty_report()], Window(0.0, 2.0), before, after, None, None)
    assert level.node_cgroup_util == 4.0
    assert level.node_proc_util == 1.0
    assert level.node_workers == 2


def test_summarize_level_drops_process_cpu_when_another_process_answered() -> None:
    # 複数のプロセスのノードでは，前後の /healthz に別のプロセスが答えうる．その差はノードの CPU ではない
    before = NodeSample(10.0, None, 8, 0.0, cgroup_cpu_s=100.0, pid=7, workers=2)
    after = NodeSample(3.0, None, 8, 0.0, cgroup_cpu_s=104.0, pid=8, workers=2)
    level = summarize_level(1, [_empty_report()], Window(0.0, 2.0), before, after, None, None)
    assert level.node_proc_util is None
    assert level.node_cgroup_util == 2.0


def test_summarize_level_keeps_process_cpu_of_a_node_without_pid() -> None:
    # Iteration 9 までのノードは pid を返さない（1 プロセスなので差を取ってよい）
    record = RequestRecord("q", 0.5, 0.6, 0.01, None)
    report = WorkerReport(0, 1, 0.0, [record], {}, 0)
    before = NodeSample(10.0, CpuSample(0, 100, 0), 8, 0.0)
    after = NodeSample(11.0, CpuSample(50, 200, 0), 8, 0.0)
    level = summarize_level(1, [report], Window(0.0, 1.0), before, after, None, None)
    assert level.node_proc_util == 1.0
    assert level.node_cgroup_util is None


async def test_healthz_reports_pid_and_the_configured_workers(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    write_faiss_shard(tmp_path, "s1", "src1", {"f0": _unit(np.random.default_rng(0), 4)})
    app = create_app(
        "node-a",
        {"s1": load_shard(tmp_path / "s1")},
        with_experiment(cfg, node_workers=3),
        ollama_url="http://ollama",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://node-a") as client:
        body = (await client.get("/healthz")).json()
    assert body["workers"] == 3
    assert isinstance(body["pid"], int)
    assert "cgroup_cpu_s" in body


# ── 複数のプロセスでの起動（実際に uvicorn を 2 個のプロセスで立てる） ──────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until_up(url: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/healthz", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise TimeoutError(f"{url} did not come up")


def test_serve_workers_answers_from_every_process_and_stops_on_sigterm(tmp_path: Path) -> None:
    write_faiss_shard(tmp_path, "s1", "src1", {"f0": _unit(np.random.default_rng(0), 4)})
    port = _free_port()
    args = WorkerArgs(
        REPO_ROOT / "config.yaml",
        "127.0.0.1",
        port,
        "node-a",
        tmp_path,
        ["s1"],
        "http://ollama",
        None,
    )
    supervisor = mp.get_context("spawn").Process(target=serve_workers, args=(args, 2))
    supervisor.start()
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_up(url)
        pids: set[int] = set()
        # 1 要求ごとに新しい接続を張る（SO_REUSEPORT は接続ごとにソケットを選ぶ）．2 個目のプロセスの起動が遅れる分も待つ
        for _ in range(MAX_PROBES):
            pids.add(httpx.get(f"{url}/healthz", timeout=5.0).json()["pid"])
            if len(pids) == 2:
                break
            time.sleep(0.05)
        assert len(pids) == 2
    finally:
        supervisor.terminate()
        supervisor.join(timeout=30)
    assert supervisor.exitcode == 0
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            # シグナル 0 は存在の確認だけをする
            os.kill(pid, 0)


async def test_read_node_cpu_seconds_uses_the_cgroup_of_a_node_with_several_workers() -> None:
    # atrium run の expert_cpu_util：複数のプロセスのノードは，答えたプロセスの値ではなくコンテナの合計を読む
    def healthz(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"process_cpu_s": 1.0, "cgroup_cpu_s": 9.0, "workers": 4})

    placement = Placement(
        dataset="enronqa", nodes=[NodeAssignment(host="a", url="http://node-a", shard_ids=[])]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(healthz)) as client:
        seconds = await read_node_cpu_seconds(client, placement)
    assert seconds == {"a": 9.0}


async def test_read_node_cpu_seconds_keeps_the_process_cpu_of_a_single_worker_node() -> None:
    def healthz(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"process_cpu_s": 1.0, "cgroup_cpu_s": 9.0, "workers": 1})

    placement = Placement(
        dataset="enronqa", nodes=[NodeAssignment(host="a", url="http://node-a", shard_ids=[])]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(healthz)) as client:
        seconds = await read_node_cpu_seconds(client, placement)
    assert seconds == {"a": 1.0}
