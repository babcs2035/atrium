"""専門家ノードの HTTP API の仕様（サーバーを起動せず ASGI アプリへ直接要求を送る）．"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from atrium.config import AtriumConfig
from atrium.node import AGENT_CARD_PATH, create_app
from atrium.store import load_shard
from tests.conftest import ollama_mock, write_faiss_shard

DIM = 8


def _unit(rng: np.random.Generator, n: int) -> np.ndarray:
    x = rng.normal(size=(n, DIM)).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


@pytest.fixture
def shard_root(tmp_path: Path) -> Path:
    rng = np.random.default_rng(0)
    write_faiss_shard(tmp_path, "pubmed-00", "pubmed", {"f0": _unit(rng, 5), "f1": _unit(rng, 4)})
    return tmp_path


def _client(
    cfg: AtriumConfig, shard_root: Path, reply: str = '"answer_choice": "B"'
) -> httpx.AsyncClient:
    stores = {"pubmed-00": load_shard(shard_root / "pubmed-00")}
    app = create_app(
        "node-1",
        stores,
        cfg,
        "http://ollama",
        httpx.AsyncClient(transport=ollama_mock(lambda _: reply)),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://node-1")


async def test_retrieve_returns_the_document_identical_to_the_query_first(
    cfg: AtriumConfig, shard_root: Path
) -> None:
    query = np.load(shard_root / "pubmed-00" / "emb" / "f1.f16.npy")[2].astype(np.float32)
    async with _client(cfg, shard_root) as client:
        response = await client.post(
            "/v1/retrieve", json={"shard_id": "pubmed-00", "k": 3, "embedding": query.tolist()}
        )
    body = response.json()
    assert response.status_code == 200
    assert [d["doc_id"] for d in body["docs"]][0] == "pubmed-00/f1/2"
    assert len(body["docs"]) == 3
    scores = [d["score"] for d in body["docs"]]
    assert scores == sorted(scores, reverse=True)


async def test_retrieve_unknown_shard_returns_problem_details(
    cfg: AtriumConfig, shard_root: Path
) -> None:
    async with _client(cfg, shard_root) as client:
        response = await client.post(
            "/v1/retrieve", json={"shard_id": "nope", "k": 1, "embedding": [0.0] * DIM}
        )
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Unknown shard"


async def test_retrieve_with_wrong_dimension_is_a_client_error(
    cfg: AtriumConfig, shard_root: Path
) -> None:
    async with _client(cfg, shard_root) as client:
        response = await client.post(
            "/v1/retrieve", json={"shard_id": "pubmed-00", "k": 1, "embedding": [0.0] * 3}
        )
    assert response.status_code == 400


async def test_agent_card_announces_each_shard_as_a_skill(
    cfg: AtriumConfig, shard_root: Path
) -> None:
    async with _client(cfg, shard_root) as client:
        card = (await client.get(AGENT_CARD_PATH)).json()
    assert card["url"] == "http://node-1"
    assert [s["id"] for s in card["skills"]] == ["pubmed-00"]


async def test_profile_reports_centroid_and_document_count(
    cfg: AtriumConfig, shard_root: Path
) -> None:
    async with _client(cfg, shard_root) as client:
        profile = (await client.get("/v1/profile")).json()
    shard = profile["shards"][0]
    assert shard["n_docs"] == 9
    assert len(shard["centroid"]) == DIM


async def test_answer_returns_only_the_answer_and_parsed_choice(
    cfg: AtriumConfig, shard_root: Path
) -> None:
    request = {
        "dataset": "medrag",
        "question": "q?",
        "options": {"A": "a", "B": "b"},
        "shard_ids": ["pubmed-00"],
        "k": 2,
        "embedding": [1.0] + [0.0] * (DIM - 1),
    }
    async with _client(cfg, shard_root) as client:
        response = await client.post("/v1/answer", json=request)
    body = response.json()
    assert body["choice"] == "B"
    assert body["n_context_docs"] == 2
    assert "pubmed-00/" not in json.dumps(body)  # 原文の断片は返さない
