"""関連ラベル・学習の分割・分析の指標の仕様．"""

from __future__ import annotations

import numpy as np

from atrium.analysis import (
    compare_runs,
    compute_metrics,
    mcnemar_exact,
    selection_metrics,
    wilson_interval,
)
from atrium.labels import contributing_sources_from_topk, make_split


def test_contributing_sources_are_sources_inside_merged_top_k() -> None:
    top = {"a": np.array([[0.9, 0.1]], np.float32), "b": np.array([[0.5, 0.4]], np.float32)}
    assert contributing_sources_from_topk(top, 1) == [["a"]]
    assert contributing_sources_from_topk(top, 2) == [["a", "b"]]


def test_contributing_sources_ignore_missing_results() -> None:
    top = {
        "a": np.array([[0.9, -np.inf]], np.float32),
        "b": np.array([[-np.inf, -np.inf]], np.float32),
    }
    assert contributing_sources_from_topk(top, 3) == [["a"]]


def test_medrag_split_is_per_bank_disjoint_and_deterministic() -> None:
    qids = [f"{bank}/{i:04d}" for bank in ("medqa", "pubmedqa") for i in range(100)]
    split = make_split("medrag", qids)
    assert make_split("medrag", list(reversed(qids))) == split
    assert set(split["train"]) | set(split["val"]) | set(split["test"]) == set(qids)
    assert not set(split["train"]) & set(split["test"])
    assert len(split["test"]) == 120  # 問題集ごとに 60%
    assert sum(q.startswith("medqa/") for q in split["test"]) == 60


def test_feb4rag_split_uses_30_10_60() -> None:
    split = make_split("feb4rag", [f"feb4rag/{i}" for i in range(700)])
    assert (len(split["train"]), len(split["val"]), len(split["test"])) == (210, 70, 420)


def test_wilson_interval_contains_point_estimate() -> None:
    lo, hi = wilson_interval(65, 100)
    assert lo < 0.65 < hi
    assert round(hi - lo, 2) == 0.18


def test_mcnemar_exact_is_one_without_disagreement_and_small_for_lopsided() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(10, 0) < 0.01


def test_selection_metrics_count_pairs_of_question_and_source() -> None:
    rows = [{"qid": "q1", "selected_sources": ["a", "b"], "n_shards_queried": 3}]
    m = selection_metrics(rows, {"q1": ["a", "c"]}, n_sources=4)
    assert (m["precision"], m["recall"]) == (0.5, 0.5)
    assert m["query_reduction_vs_all"] == 0.5


def _row(qid: str, correct: bool, error: str | None = None) -> dict[str, object]:
    return {
        "qid": qid,
        "bank": "medqa",
        "selected_sources": ["a"],
        "correct": correct,
        "choice": "A",
        "error": error,
        "timings": {"e2e_s": 1.0},
        "bytes_received": 10,
        "snippets_exposed": 1,
    }


def test_compute_metrics_reports_test_subset_and_failures_separately() -> None:
    rows = [_row("q1", True), _row("q2", False), _row("q3", True, error="boom")]
    metrics = compute_metrics(rows, {"q1": ["a"], "q2": ["a"]}, {"test": ["q2"]}, n_sources=2)
    assert metrics["all"]["n"] == 3
    assert round(metrics["all"]["failure_rate"], 3) == 0.333
    assert metrics["all"]["accuracy"]["overall"]["accuracy"] == 0.5
    assert metrics["test"]["n"] == 1
    assert metrics["test"]["accuracy"]["overall"]["accuracy"] == 0.0


def test_compare_runs_uses_only_shared_questions() -> None:
    a = [_row("q1", True), _row("q2", True)]
    b = [_row("q1", False), _row("q3", True)]
    result = compare_runs(a, b)
    assert result["n_shared"] == 1
    assert (result["only_a_correct"], result["only_b_correct"]) == (1, 0)


def test_selection_ignores_questions_without_labels() -> None:
    rows = [_row("q1", True), _row("q_unjudged", True)]
    metrics = compute_metrics(rows, {"q1": ["a"]}, {}, n_sources=2)
    assert metrics["all"]["n_unlabeled"] == 1
    assert metrics["all"]["selection"]["precision"] == 1.0


def test_label_consistency_is_only_reported_when_requested() -> None:
    rows = [{**_row("q1", True), "contributing_sources": ["a"]}]
    labels = {"q1": ["a"]}
    assert (
        compute_metrics(rows, labels, {}, 2, check_label_consistency=True)["all"][
            "label_consistency"
        ]
        == 1.0
    )
    assert compute_metrics(rows, labels, {}, 2)["all"]["label_consistency"] is None


def test_exposure_is_absent_for_runs_without_the_new_fields() -> None:
    from atrium.analysis import exposure_metrics

    assert exposure_metrics([_row("q1", True)]) is None


def test_exposure_averages_exposed_docs_and_query_recipients() -> None:
    from atrium.analysis import exposure_metrics

    rows = [
        {**_row("q1", True), "docs_exposed_by_source": {"a": 5}, "query_recipients": 1},
        {**_row("q2", True), "docs_exposed_by_source": {}, "query_recipients": 3},
    ]
    assert exposure_metrics(rows) == {"mean_docs_exposed": 2.5, "mean_query_recipients": 2.0}
