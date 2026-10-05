"""検索結果の統合と多数決の仕様．"""

from __future__ import annotations

from atrium.merge import (
    NodeVote,
    SourcedDoc,
    contributing_sources,
    merge_by_qrels,
    merge_by_score,
    top_per_source,
    vote,
)
from atrium.protocol import DocOut


def _doc(source: str, shard: str, doc_id: str, score: float) -> SourcedDoc:
    return SourcedDoc(source, shard, DocOut(doc_id=doc_id, title="", content="", score=score))


def test_top_per_source_merges_shards_into_source_level_top_k() -> None:
    docs = [
        _doc("pubmed", "pubmed-00", "a", 0.9),
        _doc("pubmed", "pubmed-00", "b", 0.1),
        _doc("pubmed", "pubmed-01", "c", 0.5),
        _doc("textbooks", "textbooks-00", "d", 0.2),
    ]
    assert [d.doc.doc_id for d in top_per_source(docs, k_ret=2)] == ["a", "c", "d"]


def test_merge_by_score_returns_highest_scores_across_sources() -> None:
    docs = [_doc("x", "x-0", "a", 0.1), _doc("y", "y-0", "b", 0.9), _doc("x", "x-0", "c", 0.5)]
    assert [d.doc.doc_id for d in merge_by_score(docs, 2)] == ["b", "c"]


def test_merge_by_qrels_orders_by_relevance_and_puts_unlabeled_last() -> None:
    docs = [_doc("x", "x-0", "u", 0.9), _doc("x", "x-0", "r0", 0.8), _doc("y", "y-0", "r2", 0.1)]
    merged = merge_by_qrels(docs, {"r0": 0, "r2": 2}, k=3)
    assert [d.doc.doc_id for d in merged] == ["r2", "r0", "u"]


def test_contributing_sources_lists_each_source_once() -> None:
    docs = [_doc("y", "y-0", "a", 1), _doc("x", "x-0", "b", 1), _doc("y", "y-1", "c", 1)]
    assert contributing_sources(docs) == ["x", "y"]


def test_vote_takes_majority() -> None:
    answers = [NodeVote("n1", "A", 0.1), NodeVote("n2", "B", 0.9), NodeVote("n3", "A", 0.2)]
    assert vote(answers) == "A"


def test_vote_breaks_tie_by_best_retrieval_score() -> None:
    answers = [NodeVote("n1", "A", 0.1), NodeVote("n2", "B", 0.9)]
    assert vote(answers) == "B"


def test_vote_ignores_unparsed_answers() -> None:
    assert vote([NodeVote("n1", None, 1.0), NodeVote("n2", "C", 0.0)]) == "C"
    assert vote([NodeVote("n1", None, 1.0)]) is None
