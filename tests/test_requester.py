"""質問者の end-to-end の仕様（専門家ノード 2 台を ASGI アプリとして直接呼ぶ）．

構成: pubmed を 2 シャードに分けて node-a / node-b に置き，textbooks を node-b に重ねて置く．
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from atrium.arrays import F32Array
from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.labels import contributing_sources_from_topk
from atrium.manifest import NodeAssignment, Placement
from atrium.node import create_app
from atrium.requester import RETRIEVE_RETRIES, Requester, discover_shards, run_questions
from atrium.routing import AllRouter, NoneRouter, Router, build_source_profiles
from atrium.store import load_shard
from tests.conftest import HostDispatchTransport, ollama_mock, with_experiment, write_faiss_shard

DIM = 8
K_RET = 3
K_RERANK = 4


class StubEmbedder:
    """質問 ID → 固定のクエリ埋め込み．"""

    def __init__(self, vectors: dict[str, F32Array]) -> None:
        self._vectors = vectors

    def embed(self, question: Question) -> dict[str, F32Array]:
        return {"enc": self._vectors[question.qid]}


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, F32Array]:
    rng = np.random.default_rng(1)
    mats = {
        name: rng.normal(size=(n, DIM)).astype(np.float32)
        for name, n in (("p0", 6), ("p1", 5), ("t0", 7))
    }
    write_faiss_shard(tmp_path, "pubmed-00", "pubmed", {"p0": mats["p0"]})
    write_faiss_shard(tmp_path, "pubmed-01", "pubmed", {"p1": mats["p1"]})
    write_faiss_shard(tmp_path, "textbooks-00", "textbooks", {"t0": mats["t0"]})
    return mats


def _questions(n: int) -> list[Question]:
    return [
        Question(f"medqa/{i:04d}", "medqa", f"question {i}", {"A": "x", "B": "y"}, "B")
        for i in range(n)
    ]


def _cfg(cfg: AtriumConfig, mode: str) -> AtriumConfig:
    cfg = with_experiment(cfg, answer_mode=mode, dataset="medrag")
    return cfg.model_copy(
        update={
            "retrieval": cfg.retrieval.model_copy(update={"k_ret": K_RET, "k_rerank": K_RERANK})
        }
    )


async def _requester(
    cfg: AtriumConfig, root: Path, router: Router, vectors: dict[str, F32Array]
) -> Requester:
    ollama = ollama_mock(lambda _: '{"answer_choice": "B"}')
    llm_client = httpx.AsyncClient(transport=ollama)
    apps = {
        "node-a": create_app(
            "node-a",
            {"pubmed-00": load_shard(root / "pubmed-00")},
            cfg,
            "http://ollama",
            llm_client,
        ),
        "node-b": create_app(
            "node-b",
            {
                "pubmed-01": load_shard(root / "pubmed-01"),
                "textbooks-00": load_shard(root / "textbooks-00"),
            },
            cfg,
            "http://ollama",
            llm_client,
        ),
    }
    transport = HostDispatchTransport(
        {
            "node-a": httpx.ASGITransport(app=apps["node-a"]),
            "node-b": httpx.ASGITransport(app=apps["node-b"]),
            "ollama": ollama,
        }
    )
    client = httpx.AsyncClient(transport=transport)
    placement = Placement(
        dataset="medrag",
        nodes=[
            NodeAssignment(host="node-a", url="http://node-a", shard_ids=["pubmed-00"]),
            NodeAssignment(
                host="node-b", url="http://node-b", shard_ids=["pubmed-01", "textbooks-00"]
            ),
        ],
    )
    sources = build_source_profiles(
        await discover_shards(client, placement), ["pubmed", "textbooks"]
    )
    return Requester(
        cfg=cfg,
        placement=placement,
        sources=sources,
        router=router,
        embedder=StubEmbedder(vectors),
        client=client,
        ollama_url="http://ollama",
    )


async def test_runtime_merge_matches_hub_labels(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    rng = np.random.default_rng(2)
    questions = _questions(12)
    vectors = {q.qid: rng.normal(size=DIM).astype(np.float32) for q in questions}
    requester = await _requester(_cfg(cfg, "retrieval_only"), tmp_path, AllRouter(), vectors)

    # 中継点と同じ計算：データ源ごとの全文書との内積から上位 K_RET を取り，統合後の上位 K_RERANK を見る
    def f16(m: F32Array) -> F32Array:
        return m.astype(np.float16).astype(np.float32)

    q = np.stack([vectors[x.qid] for x in questions])
    top = {
        "pubmed": -np.sort(-(q @ np.concatenate([f16(corpus["p0"]), f16(corpus["p1"])]).T), axis=1)[
            :, :K_RET
        ],
        "textbooks": -np.sort(-(q @ f16(corpus["t0"]).T), axis=1)[:, :K_RET],
    }
    expected = contributing_sources_from_topk(top, K_RERANK)

    records = [await requester.process(x) for x in questions]
    assert [r["contributing_sources"] for r in records] == expected
    assert all(r["n_shards_queried"] == 3 for r in records)
    assert all(r["snippets_exposed"] == 3 * K_RET for r in records)


async def test_snippet_return_grades_the_requester_llm_answer(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    questions = _questions(2)
    vectors = {q.qid: np.ones(DIM, dtype=np.float32) for q in questions}
    requester = await _requester(_cfg(cfg, "snippet_return"), tmp_path, AllRouter(), vectors)
    out = tmp_path / "results.jsonl"
    failures = await run_questions(requester, questions, out, parallel=2)
    rows = [__import__("json").loads(line) for line in out.read_text().splitlines()]
    assert failures == 0
    assert all(r["correct"] is True and r["prompt_tokens"] == 100 for r in rows)


async def test_local_answer_returns_no_snippets_and_votes(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "local_answer"), tmp_path, AllRouter(), {question.qid: np.ones(DIM, np.float32)}
    )
    record = await requester.process(question)
    assert record["snippets_exposed"] == 0
    assert {a["node_id"] for a in record["node_answers"]} == {"node-a", "node-b"}
    assert record["choice"] == "B" and record["correct"] is True


async def test_none_routing_answers_without_retrieval(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "snippet_return"),
        tmp_path,
        NoneRouter(),
        {question.qid: np.ones(DIM, np.float32)},
    )
    record = await requester.process(question)
    assert record["selected_sources"] == []
    assert record["bytes_received"] == 0
    assert record["correct"] is True


class FailingEmbedder:
    """埋め込みで想定外の例外を投げる（GPU のメモリ不足などを模す）．"""

    def embed(self, question: Question) -> dict[str, F32Array]:
        raise RuntimeError("CUDA out of memory")


async def test_unexpected_exception_is_recorded_not_raised(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(_cfg(cfg, "retrieval_only"), tmp_path, AllRouter(), {})
    requester.embedder = FailingEmbedder()
    record = await requester.process(question)
    assert record["error"] == "RuntimeError: CUDA out of memory"


async def test_failing_shard_is_recorded_as_error_for_that_question(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"), tmp_path, AllRouter(), {question.qid: np.ones(DIM, np.float32)}
    )
    # node-a は pubmed-01 を持たないので 404 を返す（配置の誤りを模す）
    requester._url_of_shard["pubmed-01"] = "http://node-a"
    record = await requester.process(question)
    assert record["error"] is not None and record["error"].startswith("HTTPStatusError")


async def test_retrieve_is_retried_when_the_node_closed_an_idle_connection(
    cfg: AtriumConfig,
    tmp_path: Path,
    corpus: dict[str, F32Array],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"),
        tmp_path,
        AllRouter(),
        {question.qid: np.ones(DIM, np.float32)},
    )
    real_post = requester.client.post
    failures_left = {"n": RETRIEVE_RETRIES}

    async def flaky_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/v1/retrieve") and failures_left["n"] > 0:
            failures_left["n"] -= 1
            raise httpx.ReadError("connection closed by the node")
        return await real_post(url, **kwargs)

    monkeypatch.setattr(requester.client, "post", flaky_post)
    record = await requester.process(question)
    assert record["error"] is None
    assert failures_left["n"] == 0
    assert record["n_shards_queried"] == 3


async def test_retrieve_gives_up_after_the_retry_limit(
    cfg: AtriumConfig,
    tmp_path: Path,
    corpus: dict[str, F32Array],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"),
        tmp_path,
        AllRouter(),
        {question.qid: np.ones(DIM, np.float32)},
    )

    async def always_fails(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ReadError("connection closed by the node")

    monkeypatch.setattr(requester.client, "post", always_fails)
    record = await requester.process(question)
    assert "ReadError" in record["error"]


async def test_flood_score_asks_every_node_for_scores_then_selects_the_best(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    from atrium.routing.advert import FloodScoreRouter

    question = _questions(1)[0]
    # textbooks の 1 件目の文書と同じ向きのクエリなら，textbooks の最高スコアが最大になる
    vec = corpus["t0"][0].astype(np.float16).astype(np.float32)
    requester = await _requester(
        _cfg(cfg, "retrieval_only"), tmp_path, FloodScoreRouter(1), {question.qid: vec}
    )
    record = await requester.process(question)
    assert record["error"] is None
    assert record["probe_recipients"] == 2
    assert record["selected_sources"] == ["textbooks"]
