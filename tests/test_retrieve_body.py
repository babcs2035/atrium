"""experiment.retrieve_body（retrieve の本文の作り方）の仕様：pre_encoded は per_request とバイト単位で同じ本文を送り，応答も同じ．"""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx
import numpy as np
import pytest

from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.protocol import RetrieveRequest
from atrium.requester import encode_json_like_httpx
from atrium.routing.advert import FloodScoreRouter
from tests.conftest import with_experiment
from tests.test_requester import DIM, _cfg, _questions, _requester, corpus  # noqa: F401


def _httpx_json_body(
    shard_id: str, k: int, embedding: list[float] | None, query_id: str | None
) -> bytes:
    """per_request の経路が実際に送る本文（RetrieveRequest → model_dump → httpx の json=）．"""
    req = RetrieveRequest(shard_id=shard_id, k=k, embedding=embedding, query_id=query_id)
    return httpx.Request("POST", "http://node/v1/retrieve", json=req.model_dump()).content


def _pre_encoded_body(shard_id: str, k: int, embedding: list[float], query_id: str | None) -> bytes:
    """pre_encoded の経路の本文の組み立て（requester._retrieve と同じ形）．"""
    return (
        b'{"shard_id":'
        + encode_json_like_httpx(shard_id)
        + b',"k":'
        + encode_json_like_httpx(k)
        + b',"embedding":'
        + encode_json_like_httpx(embedding)
        + b',"query_id":'
        + encode_json_like_httpx(query_id)
        + b"}"
    )


def test_default_retrieve_body_is_per_request(cfg: AtriumConfig) -> None:
    assert cfg.experiment.retrieve_body == "per_request"


@pytest.mark.parametrize("shard_id", ["a", "pubmed-00", 'q"\\/é日本-1'])
@pytest.mark.parametrize("k", [1, 30])
@pytest.mark.parametrize("query_id", [None, "enron/0001", 'ü\n"x"'])
def test_pre_encoded_body_equals_httpx_json_body_byte_for_byte(
    shard_id: str, k: int, query_id: str | None
) -> None:
    rng = np.random.default_rng(0)
    embedding = rng.normal(size=768).astype(np.float32)
    embedding[0], embedding[1], embedding[2], embedding[3] = -0.5, 1e-30, 0.0, 1e-5
    values = embedding.tolist()
    assert _pre_encoded_body(shard_id, k, values, query_id) == _httpx_json_body(
        shard_id, k, values, query_id
    )


@pytest.mark.parametrize("probe_body", ["per_request", "pre_encoded"])
async def test_pre_encoded_retrieve_and_probe_bodies_and_results_equal_per_request(
    cfg: AtriumConfig,
    tmp_path: Any,
    corpus: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    probe_body: str,
) -> None:
    """flood_score で probe と retrieve を送る．retrieve_body だけを変えても，両方の本文と top_doc_ids が同じ．"""
    results = {}
    for mode in ("per_request", "pre_encoded"):
        question = _questions(1)[0]
        requester = await _requester(
            with_experiment(_cfg(cfg, "retrieval_only"), probe_body=probe_body, retrieve_body=mode),
            tmp_path,
            FloodScoreRouter(2),
            {question.qid: np.arange(1, DIM + 1, dtype=np.float32) / DIM},
        )
        bodies: dict[str, bytes] = {}
        original = requester.client.send

        async def spy(
            request: httpx.Request, *, _o: Any = original, _b: dict[str, bytes] = bodies, **kw: Any
        ) -> httpx.Response:
            if request.url.path in ("/v1/probe", "/v1/retrieve"):
                _b[f"{request.url.host}{request.url.path}:{len(_b)}"] = request.read()
            return await _o(request, **kw)

        monkeypatch.setattr(requester.client, "send", spy)
        record = await requester.process(question)
        assert record["error"] is None
        results[mode] = (record["top_doc_ids"], sorted(bodies.values()))
    assert results["pre_encoded"][0] == results["per_request"][0]
    assert len(results["pre_encoded"][1]) == 2 + 3  # probe 2 ノード + retrieve 3 シャード
    assert results["pre_encoded"][1] == results["per_request"][1]


async def test_non_faiss_shard_body_is_unchanged_under_pre_encoded(
    cfg: AtriumConfig,
    tmp_path: Any,
    corpus: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """faiss 以外のシャードは埋め込みを送らない（embedding は null）．pre_encoded でも従来の本文．"""
    question: Question = _questions(1)[0]
    requester = await _requester(
        with_experiment(_cfg(cfg, "retrieval_only"), retrieve_body="pre_encoded"),
        tmp_path,
        FloodScoreRouter(1),
        {question.qid: np.arange(1, DIM + 1, dtype=np.float32) / DIM},
    )
    source = dataclasses.replace(requester.sources[0], kind="search_results")
    sent: list[bytes] = []
    original = requester.client.send

    async def spy(request: httpx.Request, **kw: Any) -> httpx.Response:
        sent.append(request.read())
        return await original(request, **kw)

    monkeypatch.setattr(requester.client, "send", spy)
    shard_id = source.shard_ids[0]
    embedding = np.arange(1, DIM + 1, dtype=np.float32)
    # 本物のノードは search_results の埋め込み無しの要求を受けて失敗しうるので，本文だけを確かめる
    with pytest.raises(httpx.HTTPStatusError):
        await requester._retrieve(  # noqa: SLF001
            source, shard_id, embedding, question, encode_json_like_httpx(embedding.tolist())
        )
    assert sent == [_httpx_json_body(shard_id, requester._k_ret(), None, question.source_qid)]  # noqa: SLF001
