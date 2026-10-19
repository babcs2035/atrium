"""p0007 B の露出の評価の仕様：外に出た文書の復元・重複なしの数・受け手の内訳・H-B の判定（DB・実機は使わない）．"""

from __future__ import annotations

from typing import Any

import numpy as np

from atrium.exposure_eval import (
    InboxTop,
    exposed_ids_of_row,
    inbox_top,
    judge_hb,
    summarize_condition,
    upper_bound_docs,
)

# 2 次元の埋め込み．質問 0 は x 軸の向き，質問 1 は y 軸の向き
QUERIES = np.array([[1.0, 0.0], [0.0, 1.0]], np.float32)
EMB_A = np.array([[0.9, 0.0], [0.5, 0.0], [0.1, 0.0], [0.0, 0.7]], np.float32)
EMB_B = np.array([[0.8, 0.0], [0.0, 0.2]], np.float32)


def make_tops(k: int = 2) -> dict[str, InboxTop]:
    return {
        "a": inbox_top(QUERIES, EMB_A, ["a/0", "a/1", "a/2", "a/3"], k),
        "b": inbox_top(QUERIES, EMB_B, ["b/0", "b/1"], k),
    }


def b1_row(qid: str, exposed: dict[str, int], top_doc_ids: list[str]) -> dict[str, Any]:
    return {
        "qid": qid,
        "error": None,
        "answer_mode": "snippet_return",
        "docs_exposed_by_source": exposed,
        "top_doc_ids": top_doc_ids,
    }


def test_inbox_top_returns_the_best_documents_per_question_capped_by_inbox_size() -> None:
    tops = make_tops(k=3)
    assert tops["a"].ids_of(0) == ["a/0", "a/1", "a/2"]
    assert tops["a"].ids_of(1)[0] == "a/3"
    assert len(tops["b"].ids_of(0)) == 2  # 文書数 2 が上限


def test_exposed_ids_follow_the_recorded_count_per_source() -> None:
    assert exposed_ids_of_row({"a": 2, "b": 1}, 0, make_tops()) == ["a/0", "a/1", "b/0"]


def test_exposed_ids_of_a_multi_source_node_take_the_highest_scores_across_its_inboxes() -> None:
    # 同じノードの a と b の文脈：得点 0.9（a/0）・0.8（b/0）・0.5（a/1）の上位 2 通
    assert exposed_ids_of_row({"a+b": 2}, 0, make_tops()) == ["a/0", "b/0"]


def test_distinct_docs_count_each_document_once_across_questions() -> None:
    rows = [b1_row("q0", {"a": 2}, ["a/0"]), b1_row("q0b", {"a": 2}, ["a/1"])]
    qindex = {"q0": 0, "q0b": 0}
    summary = summarize_condition(rows, qindex, make_tops(), {"a": 4, "b": 2})
    assert summary["distinct_docs"] == 2
    assert summary["fraction"] == 2 / 6
    assert summary["mean_docs_exposed_per_query"] == 2.0


def test_b4_documents_go_to_the_delegate_gpu_and_not_to_the_requester() -> None:
    row = {
        "qid": "q0",
        "error": None,
        "answer_mode": "delegate_gpu",
        "docs_exposed_by_source": {"a": 2},
        "node_answers": [{"delegated_to": "gpu0"}, {"delegated_to": None}],
    }
    summary = summarize_condition([row], {"q0": 0}, make_tops(), {"a": 4, "b": 2})
    assert summary["distinct_docs_by_receiver"] == {"requester": 0, "delegate_gpu": 2}
    assert summary["mean_delegate_gpus"] == 1.0
    assert summary["bounds"]["lower_docs"] == 0  # B4 は文書 ID を記録しない


def test_owner_answer_documents_go_to_the_delegate_gpu_like_b4() -> None:
    row = {
        "qid": "q0",
        "error": None,
        "answer_mode": "owner_answer",
        "docs_exposed_by_source": {"a": 2},
        "node_answers": [{"delegated_to": "gpu0"}, {"delegated_to": None}],
    }
    summary = summarize_condition([row], {"q0": 0}, make_tops(), {"a": 4, "b": 2})
    assert summary["distinct_docs_by_receiver"] == {"requester": 0, "delegate_gpu": 2}


def test_a_gpu_node_answering_locally_exposes_no_documents() -> None:
    row = {"qid": "q0", "error": None, "answer_mode": "delegate_gpu", "docs_exposed_by_source": {}}
    summary = summarize_condition([row], {"q0": 0}, make_tops(), {"a": 4, "b": 2})
    assert summary["distinct_docs"] == 0


def test_reconstruction_is_invalid_when_the_recorded_top_doc_is_not_in_the_rebuilt_set() -> None:
    row = b1_row("q0", {"a": 2}, ["a/3"])  # a/3 は質問 0 の上位 2 通に入らない
    summary = summarize_condition([row], {"q0": 0}, make_tops(), {"a": 4, "b": 2})
    assert summary["validation"]["top_doc_ids_containment"] == 0.0
    assert summary["validation"]["valid"] is False


def test_a_swap_between_tied_duplicates_is_explained_but_not_counted_as_strict_containment() -> (
    None
):
    # a/1 と a/2 が同じ得点（重複メール）．上位 2 通の最下位と同点の a/2 が記録されていても検索の誤りではない
    emb = np.array([[0.9, 0.0], [0.5, 0.0], [0.5, 0.0]], np.float32)
    tops = {"a": inbox_top(QUERIES, emb, ["a/0", "a/1", "a/2"], 2)}
    row = b1_row("q0", {"a": 2}, ["a/0", "a/2"])
    validation = summarize_condition([row], {"q0": 0}, tops, {"a": 3})["validation"]
    assert validation["top_doc_ids_containment"] == 0.0
    assert validation["top_doc_ids_containment_tie_aware"] == 1.0


def test_failed_questions_are_excluded_and_reported() -> None:
    failed = {**b1_row("q0", {"a": 2}, []), "error": "timeout"}
    summary = summarize_condition([failed], {"q0": 0}, make_tops(), {"a": 4, "b": 2})
    assert (summary["n_questions"], summary["n_skipped"]) == (0, 1)


def test_upper_bound_is_capped_by_the_inbox_size() -> None:
    rows = [b1_row("q0", {"b": 2}, []), b1_row("q1", {"b": 2}, [])]
    assert upper_bound_docs(rows, {"a": 4, "b": 3}) == 3


def fake_condition(
    fraction: float, valid: bool = True, lower: int = 0, upper: int = 0
) -> dict[str, Any]:
    return {
        "fraction": fraction,
        "validation": {"valid": valid},
        "bounds": {"lower_docs": lower, "upper_docs": upper},
    }


def test_hb_is_supported_only_when_every_p2p_condition_is_below_half() -> None:
    below = {"p1": fake_condition(0.49), "p3": fake_condition(0.01)}
    assert judge_hb(below, ["p1", "p3"], 100)["verdict"] == "supported"
    boundary = {"p1": fake_condition(0.5), "p3": fake_condition(0.01)}
    assert judge_hb(boundary, ["p1", "p3"], 100)["verdict"] == "rejected"


def test_hb_falls_back_to_bounds_when_the_reconstruction_is_invalid() -> None:
    low = {"p1": fake_condition(0.1, valid=False, lower=0, upper=30)}
    assert judge_hb(low, ["p1"], 100)["verdict"] == "supported"
    high = {"p1": fake_condition(0.1, valid=False, lower=60, upper=90)}
    assert judge_hb(high, ["p1"], 100)["verdict"] == "rejected"
    straddle = {"p1": fake_condition(0.1, valid=False, lower=10, upper=90)}
    assert judge_hb(straddle, ["p1"], 100)["verdict"] == "inconclusive"
