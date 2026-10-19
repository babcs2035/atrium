"""retrieval.fusion=rrf（p0007 A2・I3）の仕様：BM25 の式・RRF の統合・ノードと中央の索引の振る舞い．既定（none）が従来どおりであること．"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from atrium.arrays import F32Array
from atrium.central_eval import bm25_top_k
from atrium.config import AtriumConfig
from atrium.hybrid import Bm25Index, ranked_indices, rrf_top_k
from atrium.node import create_app
from atrium.paths import dataset_paths
from atrium.routing.advert import tokenize
from atrium.routing.central import CentralIndex
from atrium.store import load_shard
from tests.conftest import ollama_mock, write_faiss_shard
from tests.test_central_local import central_cfg, write_corpus

DIM = 4
CONTENTS = [
    "budget meeting with alice",
    "budget review for the quarter",
    "lunch with the team",
    "quarterly budget numbers",
    "the zebra schedule was moved",
    "unrelated newsletter text",
]


def _with_fusion(cfg: AtriumConfig, fusion: str, depth: int = 50) -> AtriumConfig:
    return cfg.model_copy(
        update={
            "retrieval": cfg.retrieval.model_copy(update={"fusion": fusion, "fusion_depth": depth})
        }
    )


def _embeddings() -> F32Array:
    """質問（e0）に doc0 が一致し，doc4（zebra）は直交する．"""
    emb = np.zeros((len(CONTENTS), DIM), np.float32)
    emb[0] = [1, 0, 0, 0]
    emb[1] = [0.9, 0.1, 0, 0]
    emb[2] = [0.8, 0.2, 0, 0]
    emb[3] = [0.7, 0.3, 0, 0]
    emb[4] = [0, 0, 0, 1]
    emb[5] = [0, 0, 1, 0]
    return emb


def _write_shard(root: Path) -> None:
    write_faiss_shard(root, "s", "s", {"f": _embeddings()})
    with (root / "s" / "chunk" / "f.jsonl").open("w", encoding="utf-8") as f:
        for i, text in enumerate(CONTENTS):
            f.write(json.dumps({"id": f"s/f/{i}", "title": "t", "content": text}) + "\n")


QUERY = [1.0, 0.0, 0.0, 0.0]


def test_bm25_index_scores_rank_documents_like_the_existing_central_bm25() -> None:
    rng = np.random.default_rng(0)
    words = [f"w{i}" for i in range(40)]
    docs = [list(rng.choice(words, size=int(rng.integers(3, 30)))) for _ in range(60)]
    queries = [list(rng.choice(words, size=5)) for _ in range(20)]

    index = Bm25Index(docs)
    existing = bm25_top_k(queries, docs, 5)

    for query, expected in zip(queries, existing.tolist(), strict=True):
        scores = index.scores(query)
        got = ranked_indices(scores, 5).tolist()
        # 同点の入れ替えは許し，上位 5 通の得点の並びが一致することを確かめる
        assert np.allclose(scores[got], scores[expected], rtol=1e-5)


def test_bm25_score_is_zero_for_documents_without_any_query_term() -> None:
    index = Bm25Index([tokenize(t) for t in CONTENTS])

    scores = index.scores(tokenize("zebra"))

    assert scores[4] > 0
    assert np.count_nonzero(scores) == 1


def test_rrf_prefers_documents_found_by_both_rankings_over_the_top_of_one() -> None:
    # 密ベクトルの順位は 0・1・3・2，BM25 の順位は 1・3（文書 0 は BM25 の得点 0）
    dense = np.array([0.9, 0.8, 0.1, 0.7])
    bm25 = np.array([0.0, 5.0, 0.0, 3.0])

    assert rrf_top_k(dense, bm25, 2, depth=10) == [1, 3]
    assert rrf_top_k(dense, bm25, 1, depth=10) == [1]


def test_rrf_only_ranks_documents_within_the_depth() -> None:
    dense = np.array([0.9, 0.8, 0.7])
    bm25 = np.array([0.0, 0.0, 9.0])

    # depth 1 では，密ベクトルの 1 位（文書 0）と BM25 の 1 位（文書 2）だけが候補になる
    assert rrf_top_k(dense, bm25, 3, depth=1) == [0, 2]


def test_rrf_gives_no_vote_to_documents_with_zero_bm25_score() -> None:
    dense = np.array([0.1, 0.9, 0.5])
    bm25 = np.zeros(3)

    assert rrf_top_k(dense, bm25, 3, depth=3) == [1, 2, 0]


def test_rrf_breaks_ties_by_dense_score_then_by_index() -> None:
    dense = np.array([0.5, 0.5, 0.9])
    bm25 = np.array([0.0, 0.0, 0.0])

    # 密ベクトルだけの順位（2 位と 3 位は同点）．同点は番号の小さい方が先
    assert rrf_top_k(dense, bm25, 3, depth=3) == [2, 0, 1]


def test_node_store_with_rrf_adds_the_keyword_document_dense_search_misses(tmp_path: Path) -> None:
    _write_shard(tmp_path)
    plain = load_shard(tmp_path / "s")
    fused = load_shard(tmp_path / "s", "rrf", 50)

    dense_ids = [d.doc_id for d in plain.search(k=2, embedding=QUERY, query_id=None)]
    fused_docs = fused.search(k=2, embedding=QUERY, query_id=None, query_text="zebra schedule")

    assert dense_ids == ["s/f/0", "s/f/1"]
    assert {d.doc_id for d in fused_docs} == {"s/f/0", "s/f/4"}
    # score は密ベクトルの内積のまま，並びは内積の降順（受信箱をまたぐ統合の比べ方を変えない）
    assert [d.doc_id for d in fused_docs] == ["s/f/0", "s/f/4"]
    assert fused_docs[0].score == pytest.approx(1.0)
    assert fused_docs[1].score == pytest.approx(0.0)


def test_node_store_with_rrf_requires_the_query_text(tmp_path: Path) -> None:
    _write_shard(tmp_path)
    fused = load_shard(tmp_path / "s", "rrf", 50)

    with pytest.raises(ValueError, match="query text"):
        fused.search(k=2, embedding=QUERY, query_id=None)


def test_node_store_with_fusion_none_ignores_the_query_text(tmp_path: Path) -> None:
    _write_shard(tmp_path)
    store = load_shard(tmp_path / "s")

    with_text = store.search(k=3, embedding=QUERY, query_id=None, query_text="zebra")
    without = store.search(k=3, embedding=QUERY, query_id=None)

    assert with_text == without


async def test_node_retrieve_uses_the_query_text_for_bm25_when_fusion_is_rrf(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    _write_shard(tmp_path)
    app = create_app(
        "n",
        {"s": load_shard(tmp_path / "s", "rrf", 50)},
        _with_fusion(cfg, "rrf"),
        "http://ollama",
        httpx.AsyncClient(transport=ollama_mock(lambda _: "x")),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://n"
    ) as client:
        response = await client.post(
            "/v1/retrieve",
            json={"shard_id": "s", "k": 2, "embedding": QUERY, "query_text": "zebra schedule"},
        )

    assert response.status_code == 200
    assert {d["doc_id"] for d in response.json()["docs"]} == {"s/f/0", "s/f/4"}


def test_central_index_with_rrf_shares_bm25_statistics_across_inboxes(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    write_corpus(tmp_path)
    # 受信箱 bob の 3 番目のメールにだけ，質問の語を入れる
    chunk = dataset_paths(tmp_path, "enronqa").corpus("bob") / "chunk" / "bob.jsonl"
    rows = [json.loads(line) for line in chunk.read_text(encoding="utf-8").splitlines()]
    rows[2]["content"] = "zebra schedule"
    chunk.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    config = _with_fusion(central_cfg(cfg), "rrf")
    plain = CentralIndex.from_corpus(central_cfg(cfg), dataset_paths(tmp_path, "enronqa"))
    fused = CentralIndex.from_corpus(config, dataset_paths(tmp_path, "enronqa"))
    query = np.random.default_rng(1).normal(size=6).astype(np.float32)
    plain_ids = [d.doc_id for d in plain.search(query, 3)]

    fused_docs = fused.search(query, 3, "zebra schedule")

    assert "bob/inbox/2." not in plain_ids
    assert "bob/inbox/2." in [d.doc_id for d in fused_docs]
    assert fused.load.bm25_s > 0
    assert plain.load.bm25_s == 0
