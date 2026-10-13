"""EQ7b（実機の参加・離脱の再生）の仕様：時刻表と質問の順番が EQ7a と同じこと，メンバー表の更新，指標の数え方．"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from atrium.arrays import F32Array
from atrium.churn_eval import simulate_availability
from atrium.churn_live import (
    build_schedule,
    compare_with_offline,
    question_order,
    run_churn_questions,
    summarize_run,
)
from atrium.config import AtriumConfig, ChurnConfig
from atrium.routing import AllRouter
from tests.conftest import with_experiment
from tests.test_requester import DIM, _cfg, _questions, _requester, corpus  # noqa: F401


def test_churn_is_only_accepted_for_sequential_enronqa_retrieval(cfg: AtriumConfig) -> None:
    churn = ChurnConfig(availability=0.7)
    ok = {
        "dataset": "enronqa",
        "answer_mode": "retrieval_only",
        "parallel": 1,
        "routing": "multi_centroid",
    }
    type(cfg.experiment).model_validate({**cfg.experiment.model_dump(), **ok, "churn": churn})
    for bad in ({"dataset": "medrag"}, {"parallel": 4}, {"routing": "ragroute"}):
        with pytest.raises(ValidationError):
            type(cfg.experiment).model_validate(
                {**cfg.experiment.model_dump(), **ok, **bad, "churn": churn}
            )


def test_schedule_uses_the_same_seeds_as_the_offline_evaluation(cfg: AtriumConfig) -> None:
    churn = ChurnConfig(availability=0.7, schedule_seed=3)
    rng = np.random.default_rng([cfg.experiment.seed, 3, 70])
    expected = simulate_availability(30, 50, 0.7, churn.mean_up_steps, rng)
    assert np.array_equal(build_schedule(cfg, churn, 30, 50), expected)


def test_question_order_is_the_seeded_permutation_used_by_the_offline_evaluation() -> None:
    assert question_order(6, 12) == [int(i) for i in np.random.default_rng(12).permutation(6)]


def _record(bank: str, up: str, selected: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "bank": bank,
        "error": None,
        "selected_sources": selected,
        "churn": {"step": 0, "up": up, "n_visible": 2},
        **extra,
    }


def test_hit_needs_the_gold_node_to_be_up_and_selected() -> None:
    node_of = {"a": 0, "b": 1}
    records = [
        _record("a", "11", ["a"], contacted_nodes=1, failed_nodes=0),  # 当たり
        _record("a", "01", ["a"], contacted_nodes=1, failed_nodes=1),  # 選ばれたが離脱していた
        _record("b", "11", ["a"], contacted_nodes=1, failed_nodes=0),  # 選ばれなかった
        _record("b", "10", [], contacted_nodes=0, failed_nodes=0),  # 離脱していて選ばれない
    ]
    stats = summarize_run(records, "multi_centroid", node_of)
    assert stats["hit"] == 0.25
    assert stats["gold_online_rate"] == 0.5
    assert stats["hit_given_gold_online"] == 0.5
    assert stats["contacted"] == 0.75
    assert stats["wasted"] == 0.25


def test_flood_score_counts_the_first_stage_nodes_as_contacted() -> None:
    records = [_record("a", "11", ["a"], probe_recipients=2, probe_failed=1)]
    stats = summarize_run(records, "flood_score", {"a": 0, "b": 1})
    assert stats["contacted"] == 2
    assert stats["wasted"] == 1


def test_a_failed_question_counts_as_a_miss() -> None:
    record = _record("a", "11", ["a"])
    record["error"] = "RuntimeError: bug"
    assert summarize_run([record], "multi_centroid", {"a": 0})["hit"] == 0.0


def test_equivalence_with_the_offline_evaluation_allows_two_questions() -> None:
    key = {"method": "multi_centroid", "availability": 0.7, "refresh": 1, "seed": 0}
    offline = [{**key, "hit": 0.5}]
    close = compare_with_offline([{**key, "hit": 0.5 + 2 / 2940}], offline, 2940)
    far = compare_with_offline([{**key, "hit": 0.5 + 3 / 2940}], offline, 2940)
    assert close["equivalent"] is True
    assert far["equivalent"] is False


async def test_replay_updates_nodes_and_the_member_table_on_schedule(
    cfg: AtriumConfig,
    tmp_path: Path,
    corpus: dict[str, F32Array],  # noqa: F811
) -> None:
    questions = _questions(4)
    churn = ChurnConfig(availability=0.7, refresh_steps=2)
    cfg_churn = with_experiment(_cfg(cfg, "retrieval_only"), churn=churn)
    vectors = {q.qid: np.ones(DIM, np.float32) for q in questions}
    requester = await _requester(cfg_churn, tmp_path, AllRouter(), vectors)
    # node-b は刻み 1・2 で離脱する．表は刻み 0 と 2 で更新する
    schedule = np.array([[True, True], [True, False], [True, False], [True, True]])
    out = tmp_path / "results.jsonl"
    failures = await run_churn_questions(requester, questions, out, churn, schedule)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert failures == 0
    assert [r["churn"]["up"] for r in rows] == ["11", "10", "10", "11"]
    # 刻み 1 の表は刻み 0 のまま（node-b が見えている）ので問い合わせは無駄になり，刻み 2 の表では node-b が外れる
    assert [r["failed_nodes"] for r in rows] == [0, 1, 0, 0]
    assert [r["churn"]["n_visible"] for r in rows] == [2, 2, 1, 1]
    # 終わるとき，全ノードが稼働に戻っている
    response = await requester.client.get("http://node-b/v1/profile")
    assert response.status_code == 200


def test_report_skips_the_hc1_judgement_until_all_its_runs_exist(tmp_path: Path) -> None:
    from atrium.churn_live import evaluate_live, render_live_report

    run = tmp_path / "gate"
    run.mkdir()
    churn = {"availability": 1.0, "refresh_steps": 1, "schedule_seed": 0}
    (run / "run_meta.json").write_text(
        json.dumps({"routing": "multi_centroid", "config": {"experiment": {"churn": churn}}}),
        encoding="utf-8",
    )
    (run / "placement.json").write_text(
        json.dumps({"nodes": [{"shard_ids": ["a"]}, {"shard_ids": ["b"]}]}), encoding="utf-8"
    )
    record = _record("a", "11", ["a"], contacted_nodes=1, failed_nodes=0)
    record.update(qid="q0", timings={"e2e_s": 0.1})
    (run / "results.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    offline = {"n_test": 1, "m": 2, "no_churn_hit": {"multi_centroid": 1.0}, "runs": []}
    metrics = evaluate_live([run], offline)
    assert metrics["hc1"] is None
    assert metrics["equivalence"]["equivalent"] is None
    assert metrics["regression"]["passed"] is True
    assert "判定していない" in render_live_report(metrics)
