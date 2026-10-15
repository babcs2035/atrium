"""experiment.requester_procs（複数の質問者のプロセス）の問と parallel の割り方，結果のまとめ方，CPU の測り方の仕様．"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from atrium.benchmarks import Question
from atrium.config import AtriumConfig, ChurnConfig
from atrium.manifest import NodeAssignment, Placement
from atrium.node import create_app
from atrium.requester_procs import (
    CpuSample,
    control_cpu_fractions,
    expert_cpu_meta,
    merge_part_files,
    parse_proc_stat,
    part_path,
    read_node_cpu_seconds,
    split_parallel,
    split_questions,
)
from tests.conftest import HostDispatchTransport, with_experiment


def _questions(n: int) -> list[Question]:
    return [Question(f"q{i}", "b", f"question {i}", {}, None) for i in range(n)]


# ── 設定 ─────────────────────────────────────────────────────────────────────


def test_default_requester_procs_is_one(cfg: AtriumConfig) -> None:
    assert cfg.experiment.requester_procs == 1


def test_parallel_smaller_than_requester_procs_is_rejected(cfg: AtriumConfig) -> None:
    raw = cfg.experiment.model_dump() | {"parallel": 4, "requester_procs": 8}
    with pytest.raises(ValidationError, match="parallel must be >= experiment.requester_procs"):
        type(cfg.experiment).model_validate(raw)


def test_churn_with_several_requester_procs_is_rejected(cfg: AtriumConfig) -> None:
    raw = cfg.experiment.model_dump() | {
        "dataset": "enronqa",
        "answer_mode": "retrieval_only",
        "routing": "flood_score",
        "parallel": 2,
        "requester_procs": 2,
        "churn": ChurnConfig(availability=0.9).model_dump(),
    }
    with pytest.raises(ValidationError, match="churn requires experiment.requester_procs = 1"):
        type(cfg.experiment).model_validate(raw)


# ── 問と parallel の割り方 ────────────────────────────────────────────────────


def test_split_parallel_keeps_the_total_and_differs_by_at_most_one() -> None:
    assert split_parallel(30, 4) == [8, 8, 7, 7]
    assert split_parallel(30, 8) == [4, 4, 4, 4, 4, 4, 3, 3]


def test_split_parallel_with_one_process_is_the_whole_parallel() -> None:
    assert split_parallel(30, 1) == [30]


def test_split_parallel_rejects_fewer_slots_than_processes() -> None:
    with pytest.raises(ValueError, match="parallel >= procs"):
        split_parallel(2, 3)


def test_split_questions_deals_in_order_and_covers_each_question_once() -> None:
    parts = split_questions(_questions(7), 3)
    assert [[q.qid for q in p] for p in parts] == [["q0", "q3", "q6"], ["q1", "q4"], ["q2", "q5"]]


def test_split_questions_leaves_extra_processes_empty_when_questions_are_few() -> None:
    assert [len(p) for p in split_questions(_questions(2), 4)] == [1, 1, 0, 0]


# ── 結果のまとめ方 ────────────────────────────────────────────────────────────


def test_part_path_is_next_to_the_results_file(tmp_path: Path) -> None:
    assert part_path(tmp_path / "results.jsonl", 3) == tmp_path / "results.part3.jsonl"


def test_merge_part_files_appends_all_rows_and_removes_the_parts(tmp_path: Path) -> None:
    out = tmp_path / "results.jsonl"
    out.write_text(json.dumps({"qid": "old"}) + "\n")
    part_path(out, 0).write_text(json.dumps({"qid": "a"}) + "\n" + json.dumps({"qid": "b"}) + "\n")
    part_path(out, 1).write_text(json.dumps({"qid": "c"}) + "\n")
    assert merge_part_files(out) == 3
    rows = [json.loads(line)["qid"] for line in out.read_text().splitlines()]
    assert sorted(rows) == ["a", "b", "c", "old"]
    assert list(tmp_path.glob("results.part*.jsonl")) == []


def test_merge_part_files_without_parts_does_not_create_the_results_file(tmp_path: Path) -> None:
    out = tmp_path / "results.jsonl"
    assert merge_part_files(out) == 0
    assert not out.exists()


# ── CPU の測り方 ──────────────────────────────────────────────────────────────


def test_parse_proc_stat_counts_idle_and_iowait_as_not_busy() -> None:
    # user nice system idle iowait irq softirq steal guest guest_nice
    sample = parse_proc_stat("cpu  10 2 3 80 5 0 4 1 7 0\ncpu0 1 1 1 1 1 1 1 1 0 0\n")
    assert sample == CpuSample(busy=20, total=105, softirq=4)


def test_control_cpu_fractions_are_shares_of_all_cpus() -> None:
    before = CpuSample(busy=100, total=1000, softirq=10)
    after = CpuSample(busy=400, total=2000, softirq=60)
    assert control_cpu_fractions(before, after) == {
        "control_cpu_busy": 0.3,
        "control_cpu_softirq": 0.05,
    }


def test_control_cpu_fractions_are_none_without_samples() -> None:
    assert control_cpu_fractions(None, None) == {
        "control_cpu_busy": None,
        "control_cpu_softirq": None,
    }


def test_expert_cpu_meta_skips_nodes_that_could_not_be_read() -> None:
    meta = expert_cpu_meta(
        {"h0": 1.0, "h1": 2.0, "h2": None}, {"h0": 3.0, "h1": 2.5, "h2": 9.0}, wall_s=2.0
    )
    assert meta == {
        "expert_cpu_util": {"h0": 1.0, "h1": 0.25},
        "expert_cpu_util_max": 1.0,
        "expert_cpu_util_median": 0.625,
    }


@pytest.mark.asyncio
async def test_node_healthz_reports_a_growing_process_cpu_time(cfg: AtriumConfig) -> None:
    app = create_app("node-a", {}, cfg, ollama_url="http://ollama")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://node-a"
    ) as client:
        first = (await client.get("/healthz")).json()["process_cpu_s"]
        sum(i * i for i in range(200_000))
        second = (await client.get("/healthz")).json()["process_cpu_s"]
    assert 0 < first < second


@pytest.mark.asyncio
async def test_read_node_cpu_seconds_records_none_for_an_unreachable_node(
    cfg: AtriumConfig,
) -> None:
    app = create_app("node-a", {}, with_experiment(cfg), ollama_url="http://ollama")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    transport = HostDispatchTransport(
        {"node-a": httpx.ASGITransport(app=app), "node-b": httpx.MockTransport(refuse)}
    )
    placement = Placement(
        dataset="enronqa",
        nodes=[
            NodeAssignment(host="a", url="http://node-a", shard_ids=[]),
            NodeAssignment(host="b", url="http://node-b", shard_ids=[]),
        ],
    )
    async with httpx.AsyncClient(transport=transport) as client:
        seconds = await read_node_cpu_seconds(client, placement)
    assert seconds["b"] is None
    assert isinstance(seconds["a"], float)
