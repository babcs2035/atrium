"""障害の測定（p0007 I6．atrium.failure_run）の仕様：止め方・検出と再開の測定・時間積分・中央の再起動・止めたノードの再開．"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
import yaml

from atrium.config import AtriumConfig
from atrium.failure_run import (
    AvailabilityInjector,
    CentralProcess,
    ContainerInjector,
    FailureContext,
    FailureParams,
    InjectionResult,
    central_hit_rate,
    measure_central_restart,
    predict_fault_hit_rate,
    redundancy_estimate,
    run_failure,
    run_fault,
)
from tests.failure_helpers import alive_hosts
from tests.live_fixtures import ENCODER, MiniCluster, build_cluster


@pytest.fixture
async def cluster(cfg: AtriumConfig, tmp_path: Path) -> MiniCluster:
    return await build_cluster(cfg, tmp_path, tolerate_node_failures=True)


def _context(cluster: MiniCluster) -> FailureContext:
    return FailureContext(
        requester=cluster.requester,
        client=cluster.client,
        questions=cluster.questions,
        gold_doc_of={q.qid: cluster.gold[q.qid]["path"] for q in cluster.questions},
        url_of_host={n.host: n.url for n in cluster.placement.nodes},
        inboxes_of_host={n.host: list(n.shard_ids) for n in cluster.placement.nodes},
    )


def _params(**changes: float) -> FailureParams:
    base = {"baseline_s": 0.4, "down_s": 0.8, "post_s": 0.4, "bin_s": 0.2, "poll_interval_s": 0.02}
    return FailureParams(ks=(1,), modes=("availability",), **{**base, **changes})


async def _alive_hosts(cluster: MiniCluster) -> list[str]:
    return await alive_hosts(cluster.client, cluster.placement)


async def _fake_control_host(
    ctl: Path, ok: bool, stop_after_requests: int
) -> list[dict[str, object]]:
    """failure_run.sh の代わり：頼みのファイルを読んで，頼みを消し，ack を書く．"""
    seen: list[dict[str, object]] = []
    while len(seen) < stop_after_requests:
        for req in sorted((ctl / "req").glob("*.json")):
            body = json.loads(req.read_text("utf-8"))
            req.unlink()
            seen.append(body)
            ack = {
                "id": body["id"],
                "ok": ok,
                "hosts": {
                    h: {"rc": 0 if ok else 1, "begin": 1.0, "end": 2.0} for h in body["hosts"]
                },
            }
            (ctl / "ack" / f"{body['id']}.json").write_text(json.dumps(ack), "utf-8")
        await asyncio.sleep(0.01)
    return seen


async def test_container_injector_sends_requests_as_files_and_waits_for_the_ack(
    tmp_path: Path,
) -> None:
    injector = ContainerInjector(tmp_path / "ctl", poll_s=0.01, timeout_s=5)
    host = asyncio.create_task(_fake_control_host(tmp_path / "ctl", True, 2))
    stopped = await injector.stop(["h1", "h2"])
    started = await injector.start(["h1"])
    seen = await host
    assert [(r["action"], r["hosts"]) for r in seen] == [("stop", ["h1", "h2"]), ("start", ["h1"])]
    assert stopped.acked_at >= stopped.requested_at
    assert stopped.per_host["h2"]["rc"] == 0
    assert started.hosts == ("h1",)


async def test_container_injector_reports_a_failed_host_command(tmp_path: Path) -> None:
    injector = ContainerInjector(tmp_path / "ctl", poll_s=0.01, timeout_s=5)
    host = asyncio.create_task(_fake_control_host(tmp_path / "ctl", False, 1))
    with pytest.raises(RuntimeError, match="stop failed"):
        await injector.stop(["h1"])
    await host


async def test_container_injector_times_out_without_a_control_host(tmp_path: Path) -> None:
    injector = ContainerInjector(tmp_path / "ctl", poll_s=0.01, timeout_s=0.1)
    with pytest.raises(TimeoutError, match="no ack"):
        await injector.stop(["h1"])


async def test_availability_injector_stops_and_resumes_nodes(cluster: MiniCluster) -> None:
    urls = {n.host: n.url for n in cluster.placement.nodes}
    injector = AvailabilityInjector(cluster.client, urls)
    await injector.stop(["node-0"])
    assert await _alive_hosts(cluster) == ["node-1"]
    await injector.start(["node-0"])
    assert await _alive_hosts(cluster) == ["node-0", "node-1"]


async def test_fault_lowers_the_answered_rate_to_the_offline_prediction_and_nodes_come_back(
    cluster: MiniCluster,
) -> None:
    ctx = _context(cluster)
    urls = ctx.url_of_host
    params = _params()
    result = await run_fault(
        ctx, AvailabilityInjector(cluster.client, urls), ["node-0"], params, 1.6
    )
    # 平常は全問に答えられ，半分の受信箱が止まっている間は，その受信箱の質問に答えられない
    assert result["baseline_hit_rate"] == 1.0
    assert result["fault_gold_down_share"] == pytest.approx(0.5, abs=0.1)
    # 予測との差は，止める・再開の前後にまたがった質問の分だけ（質問は 1 件が数 ms で，数百件のうちの数件）
    assert result["fault_hit_rate"] == pytest.approx(
        predict_fault_hit_rate(1.0, result["fault_gold_down_share"]), abs=0.05
    )
    # 検出（生存の確認が最初に失敗するまで）と再開（返事をするまで）を測る
    assert 0 <= result["detection_s"]["node-0"] < 0.5
    assert result["ready_s"]["node-0"] is not None
    assert result["first_failed_request_s"] is not None
    # 窓 [0, 1.6] の積分は，全問に答えられる場合（1.6）より小さい（止まっている 0.8 秒の間，止まった半分の分だけ減る）
    assert 0.8 < result["integral"] < 1.6
    assert result["integral_relative"] == pytest.approx(result["integral"] / 1.6)
    assert await _alive_hosts(cluster) == ["node-0", "node-1"]


async def test_stopped_nodes_are_resumed_when_the_measurement_is_cancelled(
    cluster: MiniCluster,
) -> None:
    """途中で中断（SIGTERM を取り消しに変えたもの）しても，止めたノードは再開する．"""
    ctx = _context(cluster)
    injector = AvailabilityInjector(cluster.client, ctx.url_of_host)
    task: asyncio.Task[object] = asyncio.create_task(
        run_fault(ctx, injector, ["node-1"], _params(baseline_s=0.1, down_s=30.0), 30.0)
    )
    while await _alive_hosts(cluster) == ["node-0", "node-1"]:
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _alive_hosts(cluster) == ["node-0", "node-1"]


def _write_config(cfg: AtriumConfig, path: Path) -> None:
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json")), encoding="utf-8")


def test_central_restart_is_measured_including_process_start_and_index_load(
    cluster: MiniCluster, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(cluster.cfg, config_path)
    central = CentralProcess(config_path, tmp_path)
    central.start()
    try:
        q = cluster.questions[0]
        embedding = [float(x) for x in cluster.requester.embedder.embed(q)[ENCODER]]
        restart = measure_central_restart(central, (embedding, None))
        gold = {x.qid: cluster.gold[x.qid]["path"] for x in cluster.questions}
        embeds = {
            x.qid: [float(v) for v in cluster.requester.embedder.embed(x)[ENCODER]]
            for x in cluster.questions
        }
        rate = central_hit_rate(central, cluster.questions, gold, embeds, False)
    finally:
        central.stop()
    assert restart["restart_to_answer_s"] >= restart["restart_to_ready_s"] > 0
    assert (
        restart["restart_to_ready_s"] > restart["index_load"]["total_s"]
    )  # 起動と import の分が上乗せ
    assert restart["index_load"]["n_docs"] == len(cluster.questions)
    assert restart["index_bytes"] > 0
    assert rate == 1.0


def test_redundancy_estimate_uses_measured_detection_and_states_its_assumption() -> None:
    restart = {"restart_to_answer_s": 40.0, "index_bytes": 1_000}
    estimate = redundancy_estimate(restart, detect_s=0.5, rate_after=0.8, window_s=100.0)
    assert estimate["hot_standby_switch_s"] == pytest.approx(0.5)
    assert estimate["cold_standby_switch_s"] == 40.0
    assert estimate["integral_hot_standby"] == pytest.approx(0.8 * 99.5)
    assert estimate["integral_cold_standby"] == pytest.approx(0.8 * 60.0)
    assert estimate["replication"]["extra_storage_bytes"] == 1_000
    assert "仮定" in estimate["assumption"]


async def test_run_failure_compares_p2p_with_the_central_restart_on_the_same_window(
    cluster: MiniCluster, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(cluster.cfg, config_path)
    ctx = _context(cluster)
    embeddings = {
        q.qid: [float(x) for x in cluster.requester.embedder.embed(q)[ENCODER]]
        for q in cluster.questions
    }
    central = CentralProcess(config_path, tmp_path)
    central.start()
    out_dir = tmp_path / "out"
    try:
        verdict = await run_failure(
            ctx,
            {"availability": AvailabilityInjector(cluster.client, ctx.url_of_host)},
            central,
            cluster.questions,
            embeddings,
            _params(),
            out_dir,
            False,
        )
    finally:
        central.stop()
    summary = json.loads((out_dir / "failure.json").read_text("utf-8"))
    window = summary["window_s"]
    restart = summary["central"]["restart"]["restart_to_answer_s"]
    # 窓 = 復旧の操作を始めるまで + 中央の再起動 + 復旧のあと．中央はその再起動が終わるまで 0，あとは再起動後の割合
    assert window == pytest.approx(0.8 + restart + 0.4)
    assert summary["central"]["outage_s"] == pytest.approx(0.8 + restart)
    row = summary["p2p"][0]
    assert row["central_integral"] == pytest.approx(
        summary["central"]["hit_rate_after_restart"] * 0.4
    )
    assert row["integral"] > 0
    assert verdict["verdict_original"]["holds"] is (row["integral"] > row["central_integral"])
    assert (out_dir / "sim_vs_live.json").exists()
    sim = json.loads((out_dir / "sim_vs_live.json").read_text("utf-8"))
    assert sim["rows"][0]["k"] == 1
    assert await _alive_hosts(cluster) == ["node-0", "node-1"]


class HangingTransport(httpx.AsyncBaseTransport):
    """hang_hosts のノードは要求に応答しない（止まったコンテナのポートが SYN に応答しない状態の代わり）．"""

    def __init__(self, routes: dict[str, httpx.AsyncBaseTransport]) -> None:
        self._routes = routes
        self.hang_hosts: set[str] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.hang_hosts:
            await asyncio.sleep(3600)
        return await self._routes[host].handle_async_request(request)


class HangInjector:
    """HangingTransport のノードを応答しない状態にする・戻す止め方（テスト用）．"""

    name = "hang"

    def __init__(self, transport: HangingTransport) -> None:
        self._transport = transport

    async def stop(self, hosts: list[str]) -> InjectionResult:
        began = time.perf_counter()
        self._transport.hang_hosts |= set(hosts)
        return InjectionResult("stop", tuple(hosts), began, time.perf_counter())

    async def start(self, hosts: list[str]) -> InjectionResult:
        began = time.perf_counter()
        self._transport.hang_hosts -= set(hosts)
        return InjectionResult("start", tuple(hosts), began, time.perf_counter())


async def test_questions_to_a_silent_node_time_out_and_count_as_unanswered_without_blocking_others(
    cluster: MiniCluster, monkeypatch: pytest.MonkeyPatch
) -> None:
    """応答しないノードがあると，それへ要求を送る質問は時間切れになる．ほかの質問を止めず，検出は生存の確認の打ち切りで測る．"""
    monkeypatch.setattr("atrium.failure_run.LIVENESS_TIMEOUT_S", 0.3)
    transport = HangingTransport(cluster.routes)
    client = httpx.AsyncClient(transport=transport)
    cluster.requester.client = client
    ctx = FailureContext(
        requester=cluster.requester,
        client=client,
        questions=cluster.questions,
        gold_doc_of={q.qid: cluster.gold[q.qid]["path"] for q in cluster.questions},
        url_of_host={n.host: n.url for n in cluster.placement.nodes},
        inboxes_of_host={n.host: list(n.shard_ids) for n in cluster.placement.nodes},
    )
    params = _params(baseline_s=0.3, down_s=0.9, post_s=0.3, answer_deadline_s=0.3)
    result = await run_fault(ctx, HangInjector(transport), ["node-0"], params, 1.8)
    assert result["baseline_hit_rate"] > 0.9
    # 全ての受信箱へ問い合わせる設定なので，止まっている間は全ての質問が時間切れになる
    assert result["fault_hit_rate"] == 0.0
    assert result["n_timeouts"] > 0
    assert result["first_failed_request_e2e_s"] == pytest.approx(0.3)
    # 検出：生存の確認の打ち切り（0.3 秒）で止まったと分かる
    assert result["detection_s"]["node-0"] == pytest.approx(0.3, abs=0.2)
    assert result["ready_s"]["node-0"] is not None
