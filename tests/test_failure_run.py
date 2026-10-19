"""障害の測定（p0007 I6．atrium.failure_run）の仕様：止め方・検出と再開の測定・時間積分・中央の再起動・止めたノードの再開．"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from atrium.config import AtriumConfig
from atrium.failure_run import (
    AvailabilityInjector,
    CentralProcess,
    ContainerInjector,
    FailureContext,
    FailureParams,
    central_hit_rate,
    measure_central_restart,
    node_alive,
    predict_fault_hit_rate,
    redundancy_estimate,
    run_failure,
    run_fault,
)
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
    return [n.host for n in cluster.placement.nodes if await node_alive(cluster.client, n.url)]


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
        ctx, AvailabilityInjector(cluster.client, urls), ["node-0"], params, 0.8
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
    # 窓 [0, 0.8] の積分は，全問に答えられる場合（0.8）より小さく，止まった半分の分だけ減る
    assert 0.2 < result["integral"] < 0.7
    assert result["integral_relative"] == pytest.approx(result["integral"] / 0.8)
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
    assert window == max(restart, 0.8)
    row = summary["p2p"][0]
    # 中央は再起動が終わるまで 0．窓が再起動の時間と同じなら積分は 0 で，P2P の積分の方が大きい
    assert row["central_integral"] == pytest.approx(
        summary["central"]["hit_rate_after_restart"] * max(0.0, window - restart)
    )
    assert row["integral"] > 0
    assert verdict["verdict_original"]["holds"] is (row["integral"] > row["central_integral"])
    assert (out_dir / "sim_vs_live.json").exists()
    sim = json.loads((out_dir / "sim_vs_live.json").read_text("utf-8"))
    assert sim["rows"][0]["k"] == 1
    assert await _alive_hosts(cluster) == ["node-0", "node-1"]
