"""参加の測定を主構成で流す部分（d0009 A6 参加．atrium.join_run）の仕様：bm25_probe のルーター・owner_answer の「答えられた」・中央 C1-RRF-CE・空の感度．"""

from __future__ import annotations

import dataclasses
import json
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from atrium.config import AtriumConfig
from atrium.join_run import (
    CENTRAL_MODE_C1_RRF_CE,
    JoinContext,
    JoinParams,
    agreement_questions,
    central_config,
    compare_bm25_stats,
    failure_kind_of,
    parse_delays,
    plan_join,
    run_join,
)
from atrium.live_common import is_owner_answered
from atrium.requester import Requester, TableCandidateScorer, TableReranker
from atrium.routing import AllRouter
from atrium.routing.central import CentralIndex
from atrium.routing.summary import Bm25ProbeRouter
from atrium.store import load_shard
from tests import live_fixtures
from tests.conftest import with_experiment
from tests.live_fixtures import ENCODER, MiniCluster, StubEmbedder, build_cluster

INBOXES = tuple(f"box{i}" for i in range(8))
N_NODES = 4
DOCS = 6


@pytest.fixture
async def cluster(
    cfg: AtriumConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> MiniCluster:
    # ノードの検索器が本文の BM25（bm25_probe の probe に要る）を持つように fusion: rrf・body_bm25_probe で作る
    monkeypatch.setattr(live_fixtures, "load_shard", partial(load_shard, body_bm25_probe=True))
    rrf = cfg.model_copy(update={"retrieval": cfg.retrieval.model_copy(update={"fusion": "rrf"})})
    return await build_cluster(rrf, tmp_path, INBOXES, N_NODES, DOCS, tolerate_node_failures=True)


def write_table(cluster: MiniCluster, path: Path) -> Path:
    """全ての（質問，文書）の組の表（正解のメールだけ 1，他は 0）．"""
    with path.open("w", encoding="utf-8") as f:
        for q in cluster.questions:
            for docs in cluster.doc_ids.values():
                for doc_id in docs:
                    score = 1.0 if doc_id == cluster.gold[q.qid]["path"] else 0.0
                    f.write(
                        json.dumps(
                            {"qid": q.qid, "query": q.question, "doc_id": doc_id, "score": score}
                        )
                        + "\n"
                    )
    return path


def main_config(cfg: AtriumConfig) -> AtriumConfig:
    """主構成（bm25_probe・owner_answer・R3′・RRF・候補を保存・表の得点）．"""
    changed = with_experiment(cfg, routing="bm25_probe", answer_mode="owner_answer")
    return changed.model_copy(
        update={
            "answer": changed.answer.model_copy(
                update={"owner_m": 2, "aggregate": "cross_encoder", "save_candidates": True}
            ),
            "retrieval": changed.retrieval.model_copy(
                update={"fusion": "rrf", "ce_scores": "table"}
            ),
        }
    )


def build_context(cluster: MiniCluster, table: Path) -> tuple[JoinContext, AtriumConfig]:
    """主構成の質問者（R3′ の表）と，加わる 1 台の計画の JoinContext を作る．"""
    cfg = main_config(cluster.cfg)
    requester = Requester(
        cfg=cfg,
        placement=cluster.placement,
        sources=[],
        router=AllRouter(),
        embedder=StubEmbedder(
            {q.qid: v for q, v in zip(cluster.questions, _vectors(cluster), strict=True)}
        ),
        client=cluster.client,
        ollama_url="http://ollama",
        candidate_scorer=TableCandidateScorer(table),
    )
    ctx = JoinContext(
        cfg=cfg,
        requester=requester,
        client=cluster.client,
        full_placement=cluster.placement,
        plan=plan_join(cluster.placement, INBOXES, 1, 12),
        questions=cluster.questions,
        gold_doc_of={q.qid: cluster.gold[q.qid]["path"] for q in cluster.questions},
        source_order=list(INBOXES),
        encoder_name=ENCODER,
        answered=is_owner_answered,
        sort_questions_by_qid=False,
    )
    return ctx, cfg


def _vectors(cluster: MiniCluster) -> list[Any]:
    """質問ごとの埋め込み（正解のメールの本文の埋め込み）．"""
    contents = {
        doc_id: json.loads(line)["content"]
        for inbox in INBOXES
        for doc_id, line in zip(
            cluster.doc_ids[inbox],
            (cluster.paths.corpus(inbox) / "chunk" / f"{inbox}.jsonl")
            .read_text("utf-8")
            .splitlines(),
            strict=True,
        )
    }
    return [
        cluster.embedder.encode_texts([contents[cluster.gold[q.qid]["path"]]])[0]
        for q in cluster.questions
    ]


async def test_main_config_join_uses_bm25_probe_owner_answer_and_c1_rrf_ce(
    cluster: MiniCluster, tmp_path: Path
) -> None:
    table = write_table(cluster, tmp_path / "table.jsonl")
    ctx, cfg = build_context(cluster, table)
    plan = ctx.plan
    central = CentralIndex.from_corpus(cfg, cluster.paths, sources=plan.initial_inboxes)
    # 中央の質問者：索引の上位 retrieval.k_ret 通を表で並べ替えて上位 5 通．生成は（テストでは GPU のノードが無いので）質問者の LLM
    central_cfg = central_config(cfg)
    assert central_cfg.experiment.routing == "central_local"
    assert central_cfg.experiment.central_generation == "gpu_nodes"
    assert central_cfg.retrieval.merge == "cross_encoder"
    assert central_cfg.retrieval.ce_scores == "table"
    ctx.central_requester = Requester(
        cfg=central_cfg.model_copy(
            update={
                "experiment": central_cfg.experiment.model_copy(
                    update={"central_generation": "requester"}
                )
            }
        ),
        placement=cluster.placement,
        sources=[],
        router=AllRouter(),
        embedder=ctx.requester.embedder,
        client=cluster.client,
        ollama_url="http://ollama",
        central_index=central,
        reranker=TableReranker(table),
    )
    params = JoinParams(
        n_devices=1,
        delays=parse_delays(""),
        max_tries_per_inbox=3,
        central_mode=CENTRAL_MODE_C1_RRF_CE,
    )
    out_dir = tmp_path / "out"

    await run_join(ctx, central, cluster.embedder, params, out_dir, None)

    result = json.loads((out_dir / "join.json").read_text("utf-8"))
    # P2P：bm25_probe のルーターで，owner_answer の選んだ候補の文脈に正解のメールがあれば答えられた
    assert isinstance(cluster.requester.router, AllRouter)
    assert isinstance(ctx.requester.router, Bm25ProbeRouter)
    assert ctx.requester.router.artifact.bm25 is not None
    assert ctx.requester.router.artifact.bm25.n_docs == len(INBOXES) * DOCS
    answers = [a for d in result["p2p"]["devices"] for a in d["answers"]]
    assert answers and all(a["answer_s"] is not None for a in answers)
    # 試行の記録：質問 ID・失敗の形・時間の内訳（質問の本文は残さない）
    row = answers[0]["rows"][0]
    assert set(row) == {"qid", "answered", "finished_s", "e2e_s", "failure_kind", "timings"}
    assert row["answered"] is True and row["failure_kind"] is None
    # 試行は加わる受信箱の H 型の質問をファイルの順に使う（先頭の問から）
    first_qid = [q.qid for q in cluster.questions if q.bank == answers[0]["inbox"]][0]
    assert row["qid"] == first_qid
    # 中央：再ランクと生成まで通して答えられる．追加の BM25 の作り直しの秒数を残す
    central_device = result["central"]["devices"][0]
    assert all(a["answer_s"] is not None for a in central_device["answers"])
    assert central_device["bm25_s"] > 0
    assert central_device["answers"][0]["rows"][0]["timings"]["rerank_s"] >= 0
    assert "generate_s" in central_device["answers"][0]["rows"][0]["timings"]
    # 感度は流さない
    assert result["delays"] == []
    sim = json.loads((out_dir / "sim_vs_live.json").read_text("utf-8"))
    assert sim["delay_zero_vs_main"] is None
    assert sim["bm25_stats_vs_offline"] is None


def test_table_miss_is_told_apart_from_other_key_errors() -> None:
    assert failure_kind_of(None) is None
    assert failure_kind_of("KeyError: ('what is x', 'box0/inbox/1.')") == "table_miss"
    assert failure_kind_of("KeyError: '192.168.15.106'") == "other"
    assert failure_kind_of("HTTPStatusError: Server error '503'") == "http"


def test_empty_delays_mean_no_sensitivity_run() -> None:
    assert parse_delays("") == ()
    assert parse_delays("0,10,100") == (0, 10, 100)


async def test_agreement_questions_take_the_first_two_per_inbox_for_non_test_splits(
    cluster: MiniCluster, tmp_path: Path
) -> None:
    ctx, _ = build_context(cluster, write_table(cluster, tmp_path / "t.jsonl"))
    held_out = dataclasses.replace(ctx, cfg=with_experiment(ctx.cfg, question_split="holdout"))
    chosen = agreement_questions(held_out)
    assert len(chosen) == 2 * len(INBOXES)
    assert [q.qid for q in chosen if q.bank == "box0"] == [
        q.qid for q in cluster.questions if q.bank == "box0"
    ][:2]


async def test_bm25_stats_comparison_flags_a_difference(
    cluster: MiniCluster, tmp_path: Path
) -> None:
    ctx, cfg = build_context(cluster, write_table(cluster, tmp_path / "t.jsonl"))
    from atrium.join_run import (
        MemberState,
        collect_initial_members,
        make_router_and_sources,
    )

    await collect_initial_members(ctx, JoinParams(n_devices=1))
    ctx.state = ctx.state if isinstance(ctx.state, MemberState) else MemberState()
    _, _, live = make_router_and_sources(ctx, ctx.state)
    assert isinstance(live, Bm25ProbeRouter)
    same = compare_bm25_stats(live, live)
    assert same is not None and same["g_a_stats_match"] is True
    skewed_stats = dataclasses.replace(
        live.artifact.bm25, avg_doc_len=live.artifact.bm25.avg_doc_len * 1.01
    )
    skewed = Bm25ProbeRouter(
        2,
        1,
        dataclasses.replace(live.artifact, bm25=skewed_stats),
        ENCODER,
        cfg.routing.summary_weight,
        cfg.routing.bm25_weight,
    )
    diff = compare_bm25_stats(live, skewed)
    assert diff is not None and diff["g_a_stats_match"] is False
    assert diff["avg_doc_len_rel_diff"] == pytest.approx(0.01 / 1.01)
