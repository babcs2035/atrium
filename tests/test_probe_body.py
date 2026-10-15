"""experiment.probe_body（probe の本文の作り方）の仕様：pre_encoded は per_request とバイト単位で同じ本文を送り，得点も同じ．"""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pytest

from atrium.config import AtriumConfig
from atrium.protocol import ProbeRequest
from atrium.requester import encode_json_like_httpx
from atrium.routing.advert import FloodScoreRouter
from tests.conftest import with_experiment
from tests.test_requester import DIM, _cfg, _questions, _requester, corpus  # noqa: F401


def _httpx_json_body(shard_ids: list[str], embedding: list[float]) -> bytes:
    """per_request の経路が実際に送る本文（ProbeRequest → model_dump → httpx の json=）．"""
    req = ProbeRequest(shard_ids=shard_ids, embedding=embedding)
    return httpx.Request("POST", "http://node/v1/probe", json=req.model_dump()).content


def _pre_encoded_body(shard_ids: list[str], embedding: list[float]) -> bytes:
    """pre_encoded の経路の本文の組み立て（requester._probe と同じ形）．"""
    return (
        b'{"shard_ids":'
        + encode_json_like_httpx(shard_ids)
        + b',"embedding":'
        + encode_json_like_httpx(embedding)
        + b"}"
    )


def test_default_probe_body_is_per_request(cfg: AtriumConfig) -> None:
    assert cfg.experiment.probe_body == "per_request"


@pytest.mark.parametrize("shard_ids", [["a"], ["pubmed-01", "textbooks-00"], []])
def test_pre_encoded_body_equals_httpx_json_body_byte_for_byte(shard_ids: list[str]) -> None:
    rng = np.random.default_rng(0)
    embedding = rng.normal(size=768).astype(np.float32)
    embedding[0], embedding[1], embedding[2] = -0.5, 1e-30, 0.0
    values = embedding.tolist()
    assert _pre_encoded_body(shard_ids, values) == _httpx_json_body(shard_ids, values)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_embedding_raises_in_both_paths(bad: float) -> None:
    values = [0.1, bad]
    with pytest.raises(ValueError):
        _httpx_json_body(["a"], values)
    with pytest.raises(ValueError):
        _pre_encoded_body(["a"], values)


async def test_pre_encoded_probe_scores_and_bodies_equal_per_request(
    cfg: AtriumConfig,
    tmp_path: Any,
    corpus: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = {}
    for mode in ("per_request", "pre_encoded"):
        question = _questions(1)[0]
        requester = await _requester(
            with_experiment(_cfg(cfg, "retrieval_only"), probe_body=mode),
            tmp_path,
            FloodScoreRouter(1),
            {question.qid: np.arange(1, DIM + 1, dtype=np.float32) / DIM},
        )
        bodies: dict[str, bytes] = {}
        original = requester.client.send

        async def spy(
            request: httpx.Request, *, _o: Any = original, _b: dict[str, bytes] = bodies, **kw: Any
        ) -> httpx.Response:
            if request.url.path == "/v1/probe":
                _b[request.url.host or ""] = request.read()
            return await _o(request, **kw)

        monkeypatch.setattr(requester.client, "send", spy)
        embeddings = requester._embed(question, {"timings": {}})  # noqa: SLF001
        scores = await requester._probe(embeddings, {"timings": {}})  # noqa: SLF001
        results[mode] = (scores, bodies)
    assert results["pre_encoded"][0] == results["per_request"][0]
    assert set(results["pre_encoded"][1]) == {"node-a", "node-b"}
    assert results["pre_encoded"][1] == results["per_request"][1]
