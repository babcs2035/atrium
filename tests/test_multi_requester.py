"""複数の質問者（cluster.extra_requesters．d0009 A1）の設定・割り当て・結果のまとめ・集計の仕様．"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.multi_requester_eval import (
    check_assignment,
    decide_equivalence,
    decide_load,
    exposure,
    exposure_curve,
    match_rate,
    requester_of_position,
    retrieval_signature,
    scoring_dispersion_estimate,
    throughput,
)
from atrium.render import render_compose
from atrium.requester_procs import merge_requester_dirs, shuffled_questions, split_questions
from atrium.shell_config import shell_variables
from tests.conftest import REPO_ROOT

TEMPLATE_DIR = REPO_ROOT / "docker"


def _config_dict() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["experiment"].update(dataset="enronqa", routing="flood_score", answer_mode="retrieval_only")
    raw["experiment"]["parallel"] = 30
    return raw


def _with_extras(hosts: list[str]) -> dict[str, Any]:
    raw = _config_dict()
    raw["cluster"]["extra_requesters"] = [{"host": h} for h in hosts]
    return raw


# ── 設定 ─────────────────────────────────────────────────────────────────────


def test_extra_requesters_default_to_empty_and_keep_the_single_requester() -> None:
    cfg = AtriumConfig.model_validate(_config_dict())
    assert cfg.cluster.extra_requesters == []
    assert cfg.cluster.extra_requester_hosts() == []


def test_extra_requester_may_also_be_an_expert() -> None:
    raw = _config_dict()
    expert = raw["cluster"]["experts"][0]["host"]
    raw["cluster"]["extra_requesters"] = [{"host": expert}]
    cfg = AtriumConfig.model_validate(raw)
    assert cfg.cluster.extra_requester_hosts() == [expert]


def test_extra_requester_may_not_repeat_the_main_requester() -> None:
    raw = _config_dict()
    raw["cluster"]["extra_requesters"] = [{"host": raw["cluster"]["requester"]["host"]}]
    with pytest.raises(ValidationError, match="extra_requesters must be distinct"):
        AtriumConfig.model_validate(raw)


def test_extra_requesters_may_not_repeat_each_other() -> None:
    raw = _config_dict()
    host = raw["cluster"]["experts"][0]["host"]
    raw["cluster"]["extra_requesters"] = [{"host": host}, {"host": host}]
    with pytest.raises(ValidationError, match="extra_requesters must be distinct"):
        AtriumConfig.model_validate(raw)


def test_extra_requesters_need_one_concurrent_question_each() -> None:
    raw = _config_dict()
    raw["experiment"]["parallel"] = 2
    raw["cluster"]["extra_requesters"] = [
        {"host": raw["cluster"]["experts"][0]["host"]},
        {"host": raw["cluster"]["experts"][1]["host"]},
    ]
    with pytest.raises(ValidationError, match="parallel must be >= the number of requesters"):
        AtriumConfig.model_validate(raw)


def test_extra_requesters_are_rejected_with_several_processes_per_requester() -> None:
    raw = _config_dict()
    raw["experiment"]["requester_procs"] = 2
    raw["cluster"]["extra_requesters"] = [{"host": raw["cluster"]["experts"][0]["host"]}]
    with pytest.raises(ValidationError, match="extra_requesters requires"):
        AtriumConfig.model_validate(raw)


def test_shell_config_lists_extra_requesters_and_their_ssh_users() -> None:
    raw = _config_dict()
    host = raw["cluster"]["experts"][0]["host"]
    raw["cluster"]["extra_requesters"] = [{"host": host}]
    variables = shell_variables(raw)
    assert variables["EXTRA_REQUESTERS"] == host
    assert shell_variables(_config_dict())["EXTRA_REQUESTERS"] == ""


def test_requester_extra_compose_uses_node_embed_image_and_no_gpu(cfg: AtriumConfig) -> None:
    text = render_compose(cfg, "requester_extra", 1000, 1000, template_dir=TEMPLATE_DIR)
    assert "${" not in text
    requester = yaml.safe_load(text)["services"]["requester"]
    assert requester["image"].endswith("atrium-node-embed:latest")
    assert "runtime" not in requester and "deploy" not in requester
    assert yaml.safe_load(text)["services"].keys() == {"requester"}


# ── 質問者への問の割り当て ────────────────────────────────────────────────────


def _questions(n: int) -> list[Question]:
    return [Question(f"q{i}", "b", f"question {i}", {}, None) for i in range(n)]


def test_shuffled_questions_is_reproducible_and_keeps_every_question() -> None:
    a = shuffled_questions(_questions(50), seed=12)
    assert [q.qid for q in a] == [q.qid for q in shuffled_questions(_questions(50), seed=12)]
    assert sorted(q.qid for q in a) == sorted(q.qid for q in _questions(50))


def test_each_question_goes_to_the_requester_of_its_position_in_the_shuffled_order() -> None:
    ordered = shuffled_questions(_questions(20), seed=12)
    parts = split_questions(ordered, 3)
    for k, part in enumerate(parts):
        for q in part:
            assert requester_of_position(ordered.index(q), 3) == k


# ── 結果のまとめ ─────────────────────────────────────────────────────────────


def _write_requester_dir(
    run_dir: Path, index: int, started: float, finished: float, qids: list[str]
) -> None:
    d = run_dir / f"req{index}"
    d.mkdir(parents=True)
    rows = [
        {"qid": q, "error": None, "requester_index": index, "requester_host": f"h{index}"}
        for q in qids
    ]
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    meta = {
        "started_at": started,
        "finished_at": finished,
        "failures": 0,
        "requester_index": index,
        "requester_host": f"h{index}",
    }
    (d / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_merge_requester_dirs_spans_the_earliest_start_to_the_latest_finish(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_requester_dir(run, 0, 100.0, 160.0, ["a", "b"])
    _write_requester_dir(run, 1, 101.0, 170.0, ["c"])
    summary = merge_requester_dirs(run)
    meta = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    assert summary == {"requesters": 2, "rows": 3, "failures": 0}
    assert (meta["started_at"], meta["finished_at"], meta["requester_count"]) == (100.0, 170.0, 2)
    assert [r["requester_index"] for r in meta["requesters"]] == [0, 1]
    qids = [json.loads(line)["qid"] for line in (run / "results.jsonl").read_text().splitlines()]
    assert qids == ["a", "b", "c"]


def test_merge_requester_dirs_is_idempotent(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_requester_dir(run, 0, 1.0, 2.0, ["a"])
    merge_requester_dirs(run)
    first = (run / "results.jsonl").read_text()
    merge_requester_dirs(run)
    assert (run / "results.jsonl").read_text() == first


def test_merge_requester_dirs_without_directories_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        merge_requester_dirs(tmp_path)


# ── ゲートと判定 ─────────────────────────────────────────────────────────────


def test_check_assignment_passes_when_every_question_is_on_its_i_mod_n_requester() -> None:
    order = ["a", "b", "c", "d"]
    rows = [
        {"qid": q, "requester_index": i % 3, "requester_host": "h"} for i, q in enumerate(order)
    ]
    assert check_assignment(rows, order, 3)["passes"] is True


def test_check_assignment_reports_a_question_on_the_wrong_requester() -> None:
    order = ["a", "b", "c"]
    rows = [{"qid": q, "requester_index": 0, "requester_host": "h"} for q in order]
    result = check_assignment(rows, order, 3)
    assert result["n_wrong_requester"] == 2 and result["passes"] is False


def test_check_assignment_reports_missing_and_hostless_rows() -> None:
    order = ["a", "b"]
    result = check_assignment([{"qid": "a", "requester_index": 0}], order, 2)
    assert (result["n_missing"], result["n_without_requester_host"]) == (1, 1)


def _row(qid: str, docs: dict[str, list[str]]) -> dict[str, Any]:
    cands = [{"source": s, "context_doc_ids": d, "answer": f"a-{s}"} for s, d in docs.items()]
    return {"qid": qid, "error": None, "owner_answer": {"candidates": cands}}


def test_match_rate_counts_questions_with_identical_candidate_contexts() -> None:
    a = [_row("q1", {"x": ["x/1"]}), _row("q2", {"x": ["x/1"]})]
    b = [_row("q1", {"x": ["x/1"]}), _row("q2", {"x": ["x/2"]})]
    assert match_rate(a, b, retrieval_signature) == {"n": 2, "matched": 1, "rate": 0.5}


@pytest.mark.parametrize(
    ("boot", "analytic", "delta", "verdict"),
    [
        ((-0.004, 0.004), (-0.003, 0.003), 0.0, "equivalent"),
        ((-0.005, 0.005), (-0.005, 0.005), 0.0, "equivalent"),  # 境界は含む
        ((-0.006, 0.004), (-0.003, 0.003), 0.0, "undecided"),
        ((0.006, 0.012), (0.005, 0.011), 0.008, "differs"),
        ((-0.001, 0.012), (0.006, 0.011), 0.008, "undecided"),
    ],
)
def test_decide_equivalence_boundaries(
    boot: tuple[float, float], analytic: tuple[float, float], delta: float, verdict: str
) -> None:
    assert decide_equivalence(boot, analytic, delta)["verdict"] == verdict


@pytest.mark.parametrize(
    ("ratios", "verdict"),
    [
        ([0.95, 1.05], "unchanged"),
        ([0.9, 1.1], "unchanged"),
        ([1.2, 1.3], "rises"),
        ([0.5, 0.8], "falls"),
        ([0.95, 1.3], "undecided"),
    ],
)
def test_decide_load(ratios: list[float], verdict: str) -> None:
    assert decide_load(ratios) == verdict


def test_scoring_dispersion_moves_the_bottleneck_to_generation_from_two_requesters() -> None:
    estimate = scoring_dispersion_estimate(x_gen=1.0)
    assert estimate["x_live_by_n"] == {"1": 0.6, "2": 1.0, "3": 1.0, "4": 1.0, "5": 1.0}
    assert estimate["bottleneck_moves_to_generation_at_n"] == 2


def test_throughput_spans_all_requesters(tmp_path: Path) -> None:
    run = tmp_path / "run"
    rows = [
        {"qid": f"q{i}", "error": None, "requester_index": i % 2, "timings": {"e2e_s": float(i)}}
        for i in range(10)
    ]
    run.mkdir()
    (run / "results.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    meta = {
        "started_at": 0.0,
        "finished_at": 5.0,
        "requesters": [
            {"requester_index": 0, "requester_host": "a", "started_at": 0.0, "finished_at": 4.0},
            {"requester_index": 1, "requester_host": "b", "started_at": 1.0, "finished_at": 5.0},
        ],
    }
    (run / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    result = throughput(run)
    assert result["throughput"] == 2.0
    assert [r["throughput"] for r in result["requesters"]] == [5 / 4, 5 / 4]
    assert result["requesters"][1]["started_at_offset_s"] == 1.0


# ── 露出 ─────────────────────────────────────────────────────────────────────

INBOXES = {"pc1": {"x"}, "pc2": {"y"}}


def test_exposure_excludes_documents_of_inboxes_on_the_requesters_own_pc() -> None:
    rows = [_row("q1", {"x": ["x/1", "x/2"], "y": ["y/1"]})]
    result = exposure(rows, {"q1": "pc1"}, INBOXES, total_docs=10)
    assert result["docs_per_requester"] == {"pc1": 1}  # x の 2 通は pc1 自身の受信箱なので数えない
    assert result["E_any"] == 0.1


def test_exposure_counts_a_document_once_in_e_any_and_once_per_requester_in_e_pair() -> None:
    rows = [_row("q1", {"y": ["y/1"]}), _row("q2", {"y": ["y/1"]})]
    result = exposure(rows, {"q1": "ctrl", "q2": "pc1"}, INBOXES, total_docs=10)
    assert (result["E_any"], result["E_pair"], result["E_max"]) == (0.1, 0.2, 0.1)


def test_exposure_ignores_failed_rows() -> None:
    row = _row("q1", {"y": ["y/1"]}) | {"error": "boom"}
    assert exposure([row], {"q1": "ctrl"}, INBOXES, total_docs=10)["E_any"] == 0.0


def test_exposure_curve_assigns_the_ith_question_to_the_ith_mod_n_requester() -> None:
    order = ["q0", "q1"]
    rows = [_row("q0", {"y": ["y/1"]}), _row("q1", {"y": ["y/1"]})]
    curve = exposure_curve(rows, order, ["ctrl", "pc1"], INBOXES, total_docs=10, counts=(1, 2, 5))
    assert set(curve) == {"1", "2"}  # 質問者の候補が足りない N は出さない
    assert curve["1"]["E_pair"] == 0.1 and curve["2"]["E_pair"] == 0.2
