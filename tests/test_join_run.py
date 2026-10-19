"""参加の測定（p0007 I7．atrium.join_run）の仕様：加えるデバイスの選び方・質問の流れ・P2P と中央の参加・通知の遅れ・判定と出力．"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atrium.config import AtriumConfig
from atrium.join_run import (
    JoinContext,
    JoinParams,
    build_stream,
    plan_join,
    run_join,
)
from atrium.routing.central import CentralIndex
from tests.failure_helpers import alive_hosts
from tests.live_fixtures import ENCODER, MiniCluster, build_cluster

INBOXES = tuple(f"box{i}" for i in range(8))
N_NODES = 4
DOCS = 6


@pytest.fixture
async def cluster(cfg: AtriumConfig, tmp_path: Path) -> MiniCluster:
    return await build_cluster(cfg, tmp_path, INBOXES, N_NODES, DOCS, tolerate_node_failures=True)


def test_plan_picks_reproducible_joiner_devices_and_leaves_the_others(cluster: MiniCluster) -> None:
    plan = plan_join(cluster.placement, INBOXES, 1, 12)
    assert plan == plan_join(cluster.placement, INBOXES, 1, 12)
    assert len(plan.joiner_hosts) == 1
    joiner = next(n for n in cluster.placement.nodes if n.host == plan.joiner_hosts[0])
    assert plan.joiner_inboxes == [i for i in INBOXES if i in joiner.shard_ids]
    assert {n.host for n in plan.initial_placement.nodes} == {
        n.host for n in cluster.placement.nodes
    } - set(plan.joiner_hosts)
    assert set(plan.initial_inboxes) | set(plan.joiner_inboxes) == set(INBOXES)
    assert set(plan.initial_inboxes).isdisjoint(plan.joiner_inboxes)


def test_stream_mixes_all_joiner_questions_with_the_same_number_of_others(
    cluster: MiniCluster,
) -> None:
    joiners = ["box0", "box1"]
    stream = build_stream(cluster.questions, joiners, None, 12)
    assert sum(q.bank in joiners for q in stream) == 2 * DOCS
    assert sum(q.bank not in joiners for q in stream) == 2 * DOCS
    assert [q.qid for q in stream] == [
        q.qid for q in build_stream(cluster.questions, joiners, None, 12)
    ]
    assert len(build_stream(cluster.questions, joiners, 5, 12)) == 5


async def test_join_run_measures_p2p_and_central_and_the_notice_delay(
    cluster: MiniCluster, tmp_path: Path
) -> None:
    plan = plan_join(cluster.placement, INBOXES, 1, 12)
    url_of_host = {n.host: n.url for n in cluster.placement.nodes}
    ctx = JoinContext(
        cfg=cluster.cfg,
        requester=cluster.requester,
        client=cluster.client,
        full_placement=cluster.placement,
        plan=plan,
        questions=cluster.questions,
        gold_doc_of={q.qid: cluster.gold[q.qid]["path"] for q in cluster.questions},
        source_order=list(INBOXES),
        encoder_name=ENCODER,
    )
    central = CentralIndex.from_corpus(cluster.cfg, cluster.paths, sources=plan.initial_inboxes)
    n_initial_central = central.n_docs
    params = JoinParams(n_devices=1, delays=(0, 6), max_tries_per_inbox=6)
    out_dir = tmp_path / "out"

    verdict = await run_join(ctx, central, cluster.embedder, params, out_dir, None)

    result = json.loads((out_dir / "join.json").read_text("utf-8"))
    n_joiner_questions = len(plan.joiner_inboxes) * DOCS
    # 参加の前は，メンバー表に入っていない受信箱の質問に答えられない
    assert result["pre_join"] == {"n_questions": n_joiner_questions, "hit_rate": 0.0}
    # P2P：公開情報をデバイスが作り（CPU），メンバー表に載り，その受信箱の質問に答えられる
    device = result["p2p"]["devices"][0]
    assert device["device"] == "cpu"
    assert 0 < device["up_s"] <= device["published_s"] <= device["member_s"]
    assert device["payload_bytes"] > 0 and device["published_bytes"] > 0
    assert all(a["answer_s"] is not None for a in device["answers"])
    assert all(a["answer_s"] >= device["member_s"] for a in device["answers"])
    assert result["p2p"]["exposed_mails"] == 0
    # 中央：全メールを転送し（転送量・メール数），埋め込んで足し，その受信箱の質問に答えられる
    central_device = result["central"]["devices"][0]
    assert central_device["n_mails"] == len(plan.joiner_inboxes) * DOCS
    assert central_device["transfer_bytes"] > 0
    assert all(a["answer_s"] is not None for a in central_device["answers"])
    assert all(a["answer_s"] >= central_device["indexed_s"] for a in central_device["answers"])
    assert result["central"]["exposed_mails"] == central_device["n_mails"]
    assert central.n_docs == n_initial_central + central_device["n_mails"]
    # 通知の遅れ：通知の前は答えられず，後は答えられる．遅れが大きいほど通知の前の質問が多い
    by_delay = {d["delay_questions"]: d for d in result["delays"]}
    assert by_delay[0]["joiner_questions_before_notice"] == 0
    assert by_delay[0]["hit_rate_after_notice"] == 1.0
    assert by_delay[6]["joiner_hits_before_notice"] == 0
    assert by_delay[6]["joiner_questions_before_notice"] >= 1
    assert by_delay[6]["hit_rate_after_notice"] == 1.0
    # 判定・§10 の記録・全ノードが有効
    written = json.loads((out_dir / "verdict.json").read_text("utf-8"))
    assert written == verdict
    assert written["verdict_original"]["p2p_bytes"] == result["p2p"]["payload_bytes"]
    assert written["verdict_original"]["central_bytes"] == result["central"]["transfer_bytes"]
    sim = json.loads((out_dir / "sim_vs_live.json").read_text("utf-8"))
    assert sim["pre_join"] == {"predicted_hit_rate": 0.0, "live_hit_rate": 0.0}
    assert sim["delay_zero_vs_main"]["inboxes_answered_stream_l0"] == len(plan.joiner_inboxes)
    assert await alive_hosts(cluster.client, cluster.placement) == sorted(url_of_host)
    # 質問者は最終のメンバー表（全受信箱）で終わる
    assert {s.source for s in cluster.requester.sources} == set(INBOXES)


def test_smoke_option_keeps_only_the_first_initial_devices(cluster: MiniCluster) -> None:
    plan = plan_join(cluster.placement, INBOXES, 1, 12, initial_devices=2)
    assert len(plan.initial_placement.nodes) == 2
    initial_shards = {sid for n in plan.initial_placement.nodes for sid in n.shard_ids}
    assert set(plan.initial_inboxes) == initial_shards
    assert plan.joiner_hosts == plan_join(cluster.placement, INBOXES, 1, 12).joiner_hosts
