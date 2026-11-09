"""新しいメールへの追従の測定（p0007 I5．atrium.freshness_run）の仕様：届けるメールの選び方・P2P と中央の時間・判定と出力．"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from atrium.arrays import F32Array
from atrium.benchmarks import Question, write_questions
from atrium.config import AtriumConfig
from atrium.data_enronqa import QUESTION_FILES, read_inbox
from atrium.experiment import _make_embedder
from atrium.freshness_run import (
    FreshnessContext,
    FreshnessParams,
    flush_to_central,
    load_freshness_questions,
    plan_arrivals,
    run_freshness,
)
from atrium.routing.central import CentralIndex
from tests.conftest import with_experiment
from tests.live_fixtures import ENCODER, HashEmbedder, MiniCluster, build_cluster

N_ARRIVALS = 4


@pytest.fixture
async def cluster(cfg: AtriumConfig, tmp_path: Path) -> MiniCluster:
    return await build_cluster(cfg, tmp_path)


def _params(**changes: Any) -> FreshnessParams:
    base = FreshnessParams(
        intervals=(0.0, 0.4, math.inf),
        n_arrivals=N_ARRIVALS,
        arrival_gap_s=0.05,
        ask_delay_s=0.3,
        poll_timeout_s=3.0,
        fraction=0.25,
        seed=12,
    )
    return replace(base, **changes)


def test_arrivals_are_a_reproducible_part_of_the_held_back_gold_mails(cluster: MiniCluster) -> None:
    params = _params()
    held, arrivals = plan_arrivals(cluster.questions, cluster.gold, params)
    assert len(held) == 10  # 40 通の 25%
    assert [a.doc_id for a in arrivals] == held[:N_ARRIVALS]
    assert (held, arrivals) == plan_arrivals(cluster.questions, cluster.gold, params)
    assert [a.offset_s for a in arrivals] == pytest.approx([0.0, 0.05, 0.10, 0.15])
    for a in arrivals:
        assert cluster.gold[a.qid]["path"] == a.doc_id
        assert cluster.gold[a.qid]["inbox"] == a.inbox


def test_all_held_back_mails_are_delivered_when_no_limit_is_given(cluster: MiniCluster) -> None:
    held, arrivals = plan_arrivals(cluster.questions, cluster.gold, _params(n_arrivals=None))
    assert [a.doc_id for a in arrivals] == held


async def test_freshness_run_measures_p2p_and_central_and_writes_the_outputs(
    cluster: MiniCluster, tmp_path: Path
) -> None:
    params = _params()
    held, arrivals = plan_arrivals(cluster.questions, cluster.gold, params)
    mails = {
        d["id"]: d
        for inbox in {a.inbox for a in arrivals}
        for d in read_inbox(cluster.paths, inbox)
    }
    url_of_shard = cluster.placement.url_of_shard()
    ctx = FreshnessContext(
        cfg=cluster.cfg,
        requester=cluster.requester,
        client=cluster.client,
        questions={q.qid: q for q in cluster.questions},
        mails=mails,
        url_of_inbox={s.source: url_of_shard[s.shard_ids[0]] for s in cluster.sources},
        encoder_name=ENCODER,
    )
    central = CentralIndex.from_corpus(cluster.cfg, cluster.paths, exclude_doc_ids=held)
    n_central = central.n_docs
    assert n_central == 30  # 外した 10 通を除く
    out_dir = tmp_path / "out"

    verdict = await run_freshness(
        ctx, central, cluster.embedder, cluster.questions, cluster.gold, params, out_dir, None
    )

    summary = json.loads((out_dir / "freshness.json").read_text("utf-8"))
    # 外した直後は，届く前のメールに答えられない
    assert summary["preparation"]["pre_arrival_hit_rate"] == 0.0
    assert summary["preparation"]["n_held_back"] == 10
    # P2P：持ち主のデバイスが足した直後から答えられる．その場の埋め込み・追加の時間を報告する
    rows = summary["p2p"]["rows"]
    assert len(rows) == N_ARRIVALS
    assert all(r["direct_s"] is not None and r["direct_s"] < 3.0 for r in rows)
    assert all(r["routed_s"] is not None for r in rows)
    assert all(r["device"] == "cpu" and r["node_embed_s"] >= 0 for r in rows)
    assert summary["p2p"]["hit_at_delay_rate"] == 1.0
    # 中央 T = 0：届いた直後に転送し，索引に足した後で答えられる（転送の大きさと時間が記録される）
    t0 = summary["central"]["0"]
    assert t0["summary"]["n_censored"] == 0
    assert t0["hit_at_delay_rate"] == 1.0
    assert sum(f["n_mails"] for f in t0["flushes"]) == N_ARRIVALS
    assert all(f["transfer_bytes"] > 0 and f["add_s"] >= 0 for f in t0["flushes"])
    # 中央 T = 0.4：転送の時刻より前には答えられない
    t04 = summary["central"]["0.4"]
    assert all(t >= w for t, w in zip(t04["times_s"], t04["scheduled_wait_s"], strict=True))
    # 中央 T = ∞：転送しないので答えられない（打ち切り）
    tinf = summary["central"]["inf"]
    assert tinf["summary"]["n_censored"] == N_ARRIVALS
    assert tinf["hit_at_delay_rate"] == 0.0
    assert tinf["flushes"] == []
    # 各条件の索引は元の状態に戻る
    assert central.n_docs == n_central
    # 判定は verdict_original に書かれる．T ≥ 60 秒の比較は T = ∞ だけで，4 組では片側の符号検定が有意にならない（p = 1/16）
    written = json.loads((out_dir / "verdict.json").read_text("utf-8"))
    comparison = written["verdict_original"]["comparisons"]["inf"]
    assert comparison["n_less"] == N_ARRIVALS
    assert comparison["p"] == pytest.approx(1 / 16)
    assert written["verdict_original"]["holds"] is False
    assert verdict == written
    # 質問の結果と §10 の記録
    lines = (out_dir / "p2p" / "results.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == N_ARRIVALS
    assert json.loads(lines[0])["fresh"]["condition"] == "p2p"
    sim = json.loads((out_dir / "sim_vs_live.json").read_text("utf-8"))
    assert set(sim["central"]) == {"0", "0.4"}
    assert sim["p2p"]["live_p50_s"] >= 0


async def test_p2p_deliveries_are_searchable_on_the_owner_device_afterwards(
    cluster: MiniCluster, tmp_path: Path
) -> None:
    """届けたメールは持ち主のデバイスの索引に残る（再実行は先頭で外し直す）．"""
    params = _params(intervals=(math.inf,), n_arrivals=2)
    held, arrivals = plan_arrivals(cluster.questions, cluster.gold, params)
    url_of_shard = cluster.placement.url_of_shard()
    ctx = FreshnessContext(
        cfg=cluster.cfg,
        requester=cluster.requester,
        client=cluster.client,
        questions={q.qid: q for q in cluster.questions},
        mails={
            d["id"]: d
            for inbox in {a.inbox for a in arrivals}
            for d in read_inbox(cluster.paths, inbox)
        },
        url_of_inbox={s.source: url_of_shard[s.shard_ids[0]] for s in cluster.sources},
        encoder_name=ENCODER,
    )
    central = CentralIndex.from_corpus(cluster.cfg, cluster.paths, exclude_doc_ids=held)
    for run in ("first", "second"):
        await run_freshness(
            ctx,
            central,
            cluster.embedder,
            cluster.questions,
            cluster.gold,
            params,
            tmp_path / run,
            None,
        )
        summary = json.loads((tmp_path / run / "freshness.json").read_text("utf-8"))
        assert (
            summary["preparation"]["pre_arrival_hit_rate"] == 0.0
        )  # 2 回目も，前の実行の足した行が外れる
        assert summary["p2p"]["direct"]["n_censored"] == 0


def test_accuracy_counts_every_asked_question_and_skips_unjudged_conditions(tmp_path: Path) -> None:
    """採点済みの条件だけ，流した全質問を分母に正答率を出す（失敗した質問は不正解）．"""
    from atrium.freshness_run import freshness_accuracy

    for name, judged in (("p2p", True), ("central_T0", False)):
        d = tmp_path / name
        d.mkdir()
        rows = [
            {"qid": "q1", "error": None},
            {"qid": "q2", "error": None},
            {"qid": "q3", "error": "boom"},
        ]
        (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
        if judged:
            judgements = [
                {"qid": "q1", "correct": True, "answer_overlap_lcs": 1.0},
                {"qid": "q2", "correct": False, "answer_overlap_lcs": 0.1},
            ]
            (d / "judgements.jsonl").write_text(
                "".join(json.dumps(j) + "\n" for j in judgements), "utf-8"
            )
    result = freshness_accuracy(tmp_path)
    assert set(result) == {"p2p"}
    assert result["p2p"] == {
        "n": 3,
        "n_failed": 1,
        "n_correct": 1,
        "accuracy": pytest.approx(1 / 3),
    }
    assert json.loads((tmp_path / "accuracy.json").read_text("utf-8")) == result


def _context(cluster: MiniCluster, arrivals: list[Any]) -> FreshnessContext:
    """cluster のノードと質問者から，measure_central・flush_to_central が使う文脈を組み立てる．"""
    url_of_shard = cluster.placement.url_of_shard()
    return FreshnessContext(
        cfg=cluster.cfg,
        requester=cluster.requester,
        client=cluster.client,
        questions={q.qid: q for q in cluster.questions},
        mails={
            d["id"]: d
            for inbox in {a.inbox for a in arrivals}
            for d in read_inbox(cluster.paths, inbox)
        },
        url_of_inbox={s.source: url_of_shard[s.shard_ids[0]] for s in cluster.sources},
        encoder_name=ENCODER,
    )


def test_questions_and_query_embeddings_are_read_from_the_configured_split(
    cluster: MiniCluster,
) -> None:
    """question_split: holdout なら，holdout の質問と queries/holdout/ のクエリ埋め込みを読む（test の質問は混ざらない）．"""
    source = cluster.questions[0]
    inbox = cluster.gold[source.qid]["inbox"]
    holdout = Question(f"h-{source.qid}", inbox, "holdout question", {}, "answer")
    write_questions(cluster.paths.questions.parent / QUESTION_FILES["holdout"], [holdout])
    gold_path = cluster.paths.questions.parent / "gold.jsonl"
    gold_path.write_text(
        gold_path.read_text("utf-8")
        + json.dumps({"qid": holdout.qid, "inbox": inbox, "path": "x"})
        + "\n",
        encoding="utf-8",
    )
    vector: F32Array = HashEmbedder().encode_texts(["holdout question"])
    queries = cluster.paths.split_queries("holdout")
    queries.mkdir(parents=True)
    np.save(cluster.paths.split_query_embeddings("holdout", ENCODER), vector)
    cluster.paths.split_query_ids("holdout").write_text(json.dumps([holdout.qid]), "utf-8")

    cfg = with_experiment(cluster.cfg, question_split="holdout")
    questions, gold = load_freshness_questions(cfg, cluster.paths)
    assert [q.qid for q in questions] == [holdout.qid]
    assert gold[holdout.qid]["inbox"] == inbox
    embedded = _make_embedder(cfg, cluster.paths.root.parent, []).embed(holdout)
    assert embedded[ENCODER] == pytest.approx(vector[0])
    # 既定（test）は従来どおり test の全質問
    assert len(load_freshness_questions(cluster.cfg, cluster.paths)[0]) == len(cluster.questions)


class _SlowEmbedder(HashEmbedder):
    """転送の間に次のメールが届くよう，埋め込みに固定の時間をかける．"""

    def encode_texts(self, texts: Any) -> F32Array:
        """HashEmbedder と同じ埋め込みを，0.3 秒かけて返す．"""
        import time

        time.sleep(0.3)
        return super().encode_texts(texts)


async def test_central_coalesce_flushes_all_mails_that_arrived_during_the_previous_flush_once(
    cluster: MiniCluster, tmp_path: Path
) -> None:
    """--central-coalesce：T = 0 で先頭の転送の間（0.3 秒）に届いた 3 通は，次の転送 1 回で足され，足すものの無い予定は飛ばされる．"""
    from atrium.freshness_run import measure_central

    params = _params(intervals=(0.0,), central_coalesce=True)
    held, arrivals = plan_arrivals(cluster.questions, cluster.gold, params)
    ctx = _context(cluster, arrivals)
    central = CentralIndex.from_corpus(cluster.cfg, cluster.paths, exclude_doc_ids=held)

    row = await measure_central(
        ctx, central, _SlowEmbedder(), arrivals, 0.0, params, None, tmp_path / "out"
    )

    assert [f["n_mails"] for f in row["flushes"]] == [1, N_ARRIVALS - 1]
    assert row["n_docs_after_truncate"] == row["n_docs_base"] == central.n_docs
    assert row["summary"]["n_censored"] == 0

    # 既定（まとめ足しなし）は予定ごとに 1 通ずつ転送する
    separate = await measure_central(
        ctx,
        central,
        cluster.embedder,
        arrivals,
        0.0,
        _params(intervals=(0.0,)),
        None,
        tmp_path / "separate",
    )
    assert [f["n_mails"] for f in separate["flushes"]] == [1] * N_ARRIVALS


async def test_flush_reports_the_bm25_rebuild_seconds_only_with_rrf_fusion(
    cluster: MiniCluster,
) -> None:
    """flushes[] の bm25_s は中央の BM25 の作り直しの秒数（fusion: rrf で > 0，fusion: none では 0）．"""
    params = _params()
    held, arrivals = plan_arrivals(cluster.questions, cluster.gold, params)
    ctx = _context(cluster, arrivals)
    rrf_cfg = cluster.cfg.model_copy(
        update={"retrieval": cluster.cfg.retrieval.model_copy(update={"fusion": "rrf"})}
    )
    for cfg, positive in ((rrf_cfg, True), (cluster.cfg, False)):
        central = CentralIndex.from_corpus(cfg, cluster.paths, exclude_doc_ids=held)
        cost = await flush_to_central(ctx, central, cluster.embedder, arrivals[:2])
        assert cost["n_mails"] == 2
        assert (cost["bm25_s"] > 0) is positive
        if not positive:
            assert cost["bm25_s"] == 0.0
