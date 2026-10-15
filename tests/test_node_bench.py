"""専門家ノード 1 台への負荷試験（atrium.node_bench．Iteration 9）の集計・本文・閉じた系の送り方と，/healthz の読み方の仕様．"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

import httpx
import numpy as np
import pytest

from atrium.config import AtriumConfig
from atrium.cpu_stat import CpuSample
from atrium.manifest import NodeAssignment, Placement
from atrium.node import create_app
from atrium.node_bench import (
    BenchTarget,
    NodeSample,
    RequestRecord,
    Window,
    WorkerReport,
    build_probe_bodies,
    drive_closed_loop,
    question_order,
    read_node_sample,
    resolve_target,
    summarize_level,
)
from atrium.protocol import ProbeRequest
from atrium.requester_procs import fetch_healthz
from atrium.store import load_shard
from tests.conftest import write_faiss_shard

DIM = 8
NO_NODE = NodeSample(None, None, None, 0.0)


def _unit(rng: np.random.Generator, n: int) -> np.ndarray:
    x = rng.normal(size=(n, DIM)).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _report(records: list[RequestRecord], cpu_s: float = 0.0, index: int = 0) -> WorkerReport:
    return WorkerReport(index, 1, cpu_s, records, {}, 0)


# ── 集計 ─────────────────────────────────────────────────────────────────────


def test_summarize_level_counts_only_requests_that_finished_inside_the_window() -> None:
    window = Window(10.0, 12.0)
    records = [
        RequestRecord("warmup", 9.0, 9.9, 0.001, None),
        RequestRecord("a", 10.0, 10.1, 0.001, None),
        RequestRecord("b", 11.0, 11.3, 0.002, None),
        RequestRecord("late", 11.9, 12.0, 0.001, None),
    ]
    level = summarize_level(1, [_report(records)], window, NO_NODE, NO_NODE, None, None)
    assert level.n_ok == 2
    assert level.throughput_rps == 1.0
    assert level.latency_mean_s == pytest.approx(0.2)


def test_summarize_level_counts_errors_by_kind_and_excludes_them_from_throughput() -> None:
    window = Window(0.0, 1.0)
    records = [
        RequestRecord("a", 0.1, 0.2, 0.001, None),
        RequestRecord("b", 0.1, 0.3, None, "ReadTimeout: timed out"),
        RequestRecord("c", 0.1, 0.4, None, "ReadTimeout: timed out"),
    ]
    level = summarize_level(1, [_report(records)], window, NO_NODE, NO_NODE, None, None)
    assert level.n_ok == 1
    assert level.n_errors == 2
    assert level.error_kinds == {"ReadTimeout": 2}


def test_summarize_level_little_ratio_is_one_when_streams_never_wait_outside_requests() -> None:
    # 同時に 2 本．各要求は 0.5 秒で，切れ目なく次を送る → 4 要求 / 1 秒，平均 0.5 秒，2 × 0.5 × ... = 1
    window = Window(0.0, 1.0)
    records = [
        RequestRecord(q, start, start + 0.5, 0.4, None)
        for q, start in [("a", -0.5), ("b", 0.0), ("c", -0.5), ("d", 0.0)]
    ]
    level = summarize_level(2, [_report(records)], window, NO_NODE, NO_NODE, None, None)
    assert level.throughput_rps == 4.0
    assert level.little_ratio == pytest.approx(1.0)


def test_summarize_level_turns_cpu_samples_into_utilizations() -> None:
    window = Window(0.0, 2.0)
    before = NodeSample(10.0, CpuSample(busy=100, total=1000, softirq=0), 8, 0.01)
    after = NodeSample(13.0, CpuSample(busy=600, total=2000, softirq=100), 8, 0.02)
    level = summarize_level(
        4,
        [_report([], cpu_s=0.5, index=1), _report([], cpu_s=0.2, index=0)],
        window,
        before,
        after,
        CpuSample(busy=0, total=100, softirq=0),
        CpuSample(busy=30, total=200, softirq=0),
    )
    assert level.node_proc_util == 1.5
    assert level.node_host_busy == 0.5
    assert level.node_host_softirq == 0.1
    assert level.generator_cpu_util_per_proc == [0.1, 0.25]
    assert level.control_cpu_busy == 0.3


def test_summarize_level_leaves_cpu_empty_when_the_node_could_not_be_read() -> None:
    level = summarize_level(1, [_report([])], Window(0.0, 1.0), NO_NODE, NO_NODE, None, None)
    assert level.node_proc_util is None
    assert level.node_host_busy is None
    assert level.latency_s["p95"] is None


# ── 問の順と本文 ──────────────────────────────────────────────────────────────


def test_question_order_is_the_same_shuffle_as_atrium_run() -> None:
    qids = [f"q{i}" for i in range(50)]
    expected = list(qids)
    random.Random(12).shuffle(expected)
    assert question_order(qids, 12) == expected


def test_build_probe_bodies_sends_the_embedding_row_of_each_question(tmp_path: Path) -> None:
    ids_path = tmp_path / "query_ids.json"
    ids_path.write_text(json.dumps(["q0", "q1", "q2"]))
    matrix = np.arange(12, dtype=np.float32).reshape(3, 4)
    np.save(tmp_path / "enc.npy", matrix)
    bodies = build_probe_bodies(["q2", "q0"], ids_path, tmp_path / "enc.npy", ["s1", "s2"])
    assert [qid for qid, _ in bodies] == ["q2", "q0"]
    first = ProbeRequest.model_validate_json(bodies[0][1])
    assert first.shard_ids == ["s1", "s2"]
    assert first.embedding == matrix[2].tolist()


# ── 閉じた系の送り方（実際のノードの ASGI アプリへ送る） ───────────────────────


@pytest.fixture
def node_app(cfg: AtriumConfig, tmp_path: Path) -> object:
    rng = np.random.default_rng(0)
    write_faiss_shard(tmp_path, "s1", "src1", {"f0": _unit(rng, 6)})
    write_faiss_shard(tmp_path, "s2", "src2", {"f0": _unit(rng, 5)})
    stores = {sid: load_shard(tmp_path / sid) for sid in ("s1", "s2")}
    return create_app("node-a", stores, cfg, ollama_url="http://ollama")


def _bodies(rng: np.random.Generator, n: int) -> list[tuple[str, bytes]]:
    return [
        (
            f"q{i}",
            json.dumps({"shard_ids": ["s1", "s2"], "embedding": e.tolist()}).encode(),
        )
        for i, e in enumerate(_unit(rng, n))
    ]


async def test_drive_closed_loop_records_scores_and_stops_at_the_window_end(
    node_app: object,
) -> None:
    target = BenchTarget("a", "http://node-a", "node-a", ["s1", "s2"], "enc")
    transport = httpx.ASGITransport(app=node_app)  # type: ignore[arg-type]
    t0 = time.time()
    window = Window(t0 + 0.05, t0 + 0.3)
    async with httpx.AsyncClient(transport=transport) as client:
        report = await drive_closed_loop(
            client, target, _bodies(np.random.default_rng(1), 3), 2, t0, window
        )
    assert report.records
    assert all(r.error is None for r in report.records)
    assert all(r.sent_at < window.end for r in report.records)
    assert set(report.scores) == {"q0", "q1", "q2"}
    assert all(set(s) == {"s1", "s2"} for s in report.scores.values())
    # 同じ問を何度送っても得点は変わらない
    assert report.score_mismatches == 0


async def test_drive_closed_loop_marks_a_response_from_another_node_as_an_error(
    node_app: object,
) -> None:
    target = BenchTarget("a", "http://node-a", "node-other", ["s1", "s2"], "enc")
    transport = httpx.ASGITransport(app=node_app)  # type: ignore[arg-type]
    t0 = time.time()
    async with httpx.AsyncClient(transport=transport) as client:
        report = await drive_closed_loop(
            client, target, _bodies(np.random.default_rng(1), 2), 1, t0, Window(t0, t0 + 0.1)
        )
    assert report.records
    assert all((r.error or "").startswith("UnexpectedResponse") for r in report.records)
    assert report.scores == {}


async def test_resolve_target_takes_shards_from_the_placement_and_encoder_from_the_profile(
    node_app: object,
) -> None:
    placement = Placement(
        dataset="enronqa",
        nodes=[NodeAssignment(host="a", url="http://node-a", shard_ids=["s2", "s1"])],
    )
    transport = httpx.ASGITransport(app=node_app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport) as client:
        target = await resolve_target(client, placement, "a")
    assert target == BenchTarget("a", "http://node-a", "node-a", ["s2", "s1"], "enc")


async def test_resolve_target_rejects_a_node_whose_shards_differ_from_the_placement(
    node_app: object,
) -> None:
    placement = Placement(
        dataset="enronqa",
        nodes=[NodeAssignment(host="a", url="http://node-a", shard_ids=["s1"])],
    )
    transport = httpx.ASGITransport(app=node_app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="announces shards"):
            await resolve_target(client, placement, "a")


async def test_resolve_target_rejects_a_host_outside_the_placement(node_app: object) -> None:
    placement = Placement(dataset="enronqa", nodes=[])
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="not in the placement"):
            await resolve_target(client, placement, "a")


# ── /healthz の読み方 ─────────────────────────────────────────────────────────


async def test_read_node_sample_reads_process_and_device_cpu(node_app: object) -> None:
    transport = httpx.ASGITransport(app=node_app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport) as client:
        sample = await read_node_sample(client, "http://node-a")
    assert sample.process_cpu_s is not None and sample.process_cpu_s > 0
    if Path("/proc/stat").exists():
        assert sample.host_cpu is not None and sample.host_cpu.total > 0
        assert sample.n_cpu is not None and sample.n_cpu >= 1


async def test_fetch_healthz_retries_once_after_the_node_dropped_a_reused_connection() -> None:
    # Iteration 8 の fs_n2_p30_r1：古い keep-alive の接続で "Server disconnected" になった 11 台が読めなかった
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected", request=request)
        return httpx.Response(200, json={"status": "ok", "process_cpu_s": 1.5})

    async with httpx.AsyncClient(transport=httpx.MockTransport(flaky)) as client:
        body = await fetch_healthz(client, "http://node-a")
    assert body is not None and body["process_cpu_s"] == 1.5
    assert calls["n"] == 2


async def test_fetch_healthz_gives_up_after_the_retry() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        assert await fetch_healthz(client, "http://node-a") is None
