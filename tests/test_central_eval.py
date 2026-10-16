"""中央集約ベースライン（p0006 P1）のオフラインの評価の仕様：上位 K の取り方・BM25・一致率・Recall．"""

from __future__ import annotations

import numpy as np

from atrium.central_eval import (
    agreement_with_run,
    bm25_top_k,
    dense_top_k,
    recall_flags,
    top_k_indices,
)


def test_top_k_orders_by_score_and_breaks_ties_by_the_smaller_index() -> None:
    scores = np.array([[0.1, 0.9, 0.9, 0.5]], np.float32)
    assert top_k_indices(scores, 3).tolist() == [[1, 2, 3]]


def test_dense_top_k_matches_a_brute_force_search_across_query_chunks() -> None:
    rng = np.random.default_rng(0)
    emb = rng.normal(size=(40, 6)).astype(np.float32)
    queries = rng.normal(size=(300, 6)).astype(np.float32)  # QUERY_CHUNK（256）をまたぐ
    expected = np.argsort(-(queries @ emb.T), axis=1)[:, :5]
    assert np.array_equal(dense_top_k(queries, emb, 5), expected)


def test_bm25_prefers_the_document_with_the_rare_query_term() -> None:
    docs = [["gas", "price", "today"], ["gas", "pipeline", "gas"], ["lunch", "menu", "gas"]]
    top = bm25_top_k([["pipeline"], ["lunch"]], docs, 1)
    assert top.ravel().tolist() == [1, 2]


def test_agreement_counts_set_matches_and_order_matches_separately() -> None:
    central = {"q0": ["a", "b"], "q1": ["a", "b"], "q2": ["a", "b"]}
    rows = [
        {"qid": "q0", "top_doc_ids": ["a", "b"], "error": None},
        {"qid": "q1", "top_doc_ids": ["b", "a"], "error": None},
        {"qid": "q2", "top_doc_ids": ["a", "z"], "error": None},
    ]
    result = agreement_with_run(central, rows)
    assert result["set_agreement"] == 2 / 3
    assert result["ordered_agreement"] == 1 / 3


def test_agreement_excludes_failed_questions_and_reports_them() -> None:
    central = {"q0": ["a"], "q1": ["a"]}
    rows = [{"qid": "q0", "top_doc_ids": ["a"], "error": None}]
    result = agreement_with_run(central, rows)
    assert (result["n_compared"], result["n_failed_or_missing"]) == (1, 1)


def test_recall_flags_only_counts_the_gold_email_within_the_top_k() -> None:
    flags = recall_flags([["x", "g"], ["x", "y"]], ["g", "g"], 1)
    assert flags.tolist() == [0.0, 0.0]
    assert recall_flags([["x", "g"]], ["g"], 2).tolist() == [1.0]
