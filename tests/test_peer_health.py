"""質問者の生存確認（d0009 B1．atrium.peer_health）の仕様：外す・戻す条件，打ち切り，visible_urls の更新，委託先の止まり，設定の検査．"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from atrium.config import AtriumConfig, PeerHealthConfig
from atrium.failure_run import (
    AvailabilityInjector,
    FailureContext,
    FailureParams,
    delegate_is_excluded,
    run_fault,
)
from atrium.peer_health import (
    EVENT_DROP,
    EVENT_RESTORE,
    PeerHealthMonitor,
    PeerHealthTracker,
    check_alive,
)
from tests.conftest import with_experiment
from tests.live_fixtures import MiniCluster, build_cluster


@pytest.fixture
async def cluster(cfg: AtriumConfig, tmp_path: Path) -> MiniCluster:
    return await build_cluster(cfg, tmp_path, tolerate_node_failures=True)


def _urls(cluster: MiniCluster) -> dict[str, str]:
    return {n.host: n.url for n in cluster.placement.nodes}


def _monitor(cluster: MiniCluster, **changes: float) -> PeerHealthMonitor:
    cfg = PeerHealthConfig(interval_s=0.02, timeout_s=0.5, **changes)  # type: ignore[arg-type]
    return PeerHealthMonitor(cluster.client, cluster.requester, _urls(cluster), cfg)


def test_tracker_drops_after_consecutive_failures_and_restores_after_consecutive_successes() -> (
    None
):
    tracker = PeerHealthTracker(PeerHealthConfig(fail_threshold=2, recover_threshold=2))
    outcomes = [
        tracker.observe("h", False, 1.0),  # 1 回目の失敗では外さない
        tracker.observe("h", True, 2.0),  # 成功で連続の失敗の数は 0 に戻る
        tracker.observe("h", False, 3.0),
    ]
    assert outcomes == [None, None, None]
    drop = tracker.observe("h", False, 4.0)
    assert drop is not None and (drop.kind, drop.at) == (EVENT_DROP, 4.0)
    assert tracker.excluded_hosts == {"h"}
    assert tracker.observe("h", True, 5.0) is None  # 戻すには連続 2 回の成功が要る
    assert tracker.observe("h", False, 6.0) is None  # 外している間の失敗は事象にならない
    assert tracker.observe("h", True, 7.0) is None
    restore = tracker.observe("h", True, 8.0)
    assert restore is not None and restore.kind == EVENT_RESTORE
    assert tracker.excluded_hosts == set()


def test_tracker_with_default_thresholds_drops_on_one_failure_and_restores_on_one_success() -> None:
    tracker = PeerHealthTracker(PeerHealthConfig())
    assert tracker.observe("h", False, 1.0) is not None
    assert tracker.observe("h", True, 2.0) is not None
    assert tracker.excluded_hosts == set()


async def test_check_alive_counts_a_503_a_connection_failure_and_a_timeout_as_failures() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ok":
            return httpx.Response(200, json={"node_id": "ok"})
        if request.url.host == "down":
            return httpx.Response(503)
        if request.url.host == "refused":
            raise httpx.ConnectError("refused")
        await asyncio.sleep(5)  # 応答しないノード
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = {
            h: await check_alive(client, f"http://{h}", 0.2)
            for h in ("ok", "down", "refused", "hang")
        }
    assert results == {"ok": True, "down": False, "refused": False, "hang": False}


async def test_node_alive_endpoint_answers_200_and_503_while_the_node_is_down(
    cluster: MiniCluster,
) -> None:
    url = cluster.placement.nodes[0].url
    assert await check_alive(cluster.client, url, 1.0)
    injector = AvailabilityInjector(cluster.client, _urls(cluster))
    await injector.stop(["node-0"])
    response = await cluster.client.get(f"{url}/v1/alive")
    assert response.status_code == 503
    await injector.start(["node-0"])
    assert (await cluster.client.get(f"{url}/v1/alive")).json() == {"node_id": "node-0"}


async def test_dropped_node_inboxes_leave_the_first_stage_candidates_and_return_after_recovery(
    cluster: MiniCluster,
) -> None:
    monitor = _monitor(cluster)
    injector = AvailabilityInjector(cluster.client, _urls(cluster))
    all_sources = {s.source for s in cluster.requester.sources}
    await monitor.start()
    try:
        assert {s.source for s in cluster.requester._routable_sources()} == all_sources
        await injector.stop(["node-0"])
        await asyncio.sleep(0.3)
        node0_inboxes = set(cluster.placement.nodes[0].shard_ids)
        assert monitor.excluded_hosts == {"node-0"}
        assert cluster.requester.visible_urls == frozenset({"http://node-1"})
        assert {
            s.source for s in cluster.requester._routable_sources()
        } == all_sources - node0_inboxes
        await injector.start(["node-0"])
        await asyncio.sleep(0.3)
        assert monitor.excluded_hosts == frozenset()
        assert {s.source for s in cluster.requester._routable_sources()} == all_sources
    finally:
        await monitor.stop()
    assert cluster.requester.visible_urls is None
    assert [e.kind for e in monitor.events] == [EVENT_DROP, EVENT_RESTORE]


async def test_monitor_refuses_to_start_when_a_node_is_already_down(cluster: MiniCluster) -> None:
    injector = AvailabilityInjector(cluster.client, _urls(cluster))
    await injector.stop(["node-1"])
    try:
        with pytest.raises(RuntimeError, match="node-1"):
            await _monitor(cluster).start()
    finally:
        await injector.start(["node-1"])


async def test_run_fault_with_a_monitor_records_events_and_no_false_positive(
    cluster: MiniCluster,
) -> None:
    monitor = _monitor(cluster)
    ctx = FailureContext(
        requester=cluster.requester,
        client=cluster.client,
        questions=cluster.questions,
        gold_doc_of={q.qid: cluster.gold[q.qid]["path"] for q in cluster.questions},
        url_of_host=_urls(cluster),
        inboxes_of_host={n.host: list(n.shard_ids) for n in cluster.placement.nodes},
        peer_monitor=monitor,
    )
    params = FailureParams(
        ks=(1,),
        modes=("availability",),
        baseline_s=0.4,
        down_s=0.8,
        post_s=0.4,
        bin_s=0.2,
        poll_interval_s=0.02,
    )
    await monitor.start()
    try:
        result = await run_fault(
            ctx, AvailabilityInjector(cluster.client, ctx.url_of_host), ["node-0"], params, 1.6
        )
    finally:
        await monitor.stop()
    health = result["peer_health"]
    assert health["false_positive_drops"] == 0
    # availability は 503 が止める操作の応答より先に返りうるので，外した時刻は 0 の少し前でもよい
    assert -0.2 < health["dropped_at_s"]["node-0"] < 0.5
    assert health["restored_after_start_ack_s"]["node-0"] is not None
    assert all("n_visible" in q and "delegate_down" in q for q in result["questions"])


def test_delegate_is_excluded_when_a_selected_inbox_delegates_to_an_excluded_host() -> None:
    inboxes = {"cpu-a": ["a1"], "cpu-b": ["b1"], "gpu": ["g1"]}
    delegate = {"cpu-a": "gpu"}
    assert delegate_is_excluded(["a1", "b1"], inboxes, delegate, frozenset({"gpu"}))
    assert not delegate_is_excluded(
        ["b1"], inboxes, delegate, frozenset({"gpu"})
    )  # 委託先を持たない台
    assert not delegate_is_excluded(
        ["a1"], inboxes, delegate, frozenset({"cpu-a"})
    )  # 台自身が外れていても委託先ではない
    assert not delegate_is_excluded(["a1"], inboxes, delegate, frozenset())


def test_peer_health_is_rejected_with_churn(cfg: AtriumConfig) -> None:
    from atrium.config import ChurnConfig

    with pytest.raises(ValueError, match="peer_health"):
        AtriumConfig.model_validate(
            with_experiment(
                cfg, peer_health=PeerHealthConfig(), churn=ChurnConfig(availability=0.9)
            ).model_dump()
        )


def test_peer_health_is_rejected_by_commands_other_than_failure_run(cfg: AtriumConfig) -> None:
    from atrium.cli import reject_peer_health

    reject_peer_health(cfg, "run")  # 既定の null は通る
    with pytest.raises(ValueError, match="failure-run"):
        reject_peer_health(with_experiment(cfg, peer_health=PeerHealthConfig()), "run")


def test_peer_health_default_is_off(cfg: AtriumConfig) -> None:
    assert cfg.experiment.peer_health is None
