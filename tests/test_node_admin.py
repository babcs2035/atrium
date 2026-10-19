"""ノードの管理 API（p0007 I5．cluster.node_admin）の仕様：メールの非表示・追加・取り出し・埋め込みモデルの読み込み．"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest

from atrium.config import AtriumConfig
from atrium.node import NodeError, create_app, load_cpu_arctic_embedder
from atrium.paths import dataset_paths
from atrium.store import load_shard
from tests.conftest import ollama_mock
from tests.live_fixtures import DIM, HashEmbedder, enron_cfg, write_inbox

INBOX = "alice"


def _client(cfg: AtriumConfig, root: Path, embedder: HashEmbedder | None) -> httpx.AsyncClient:
    app = create_app(
        "node-1",
        {INBOX: load_shard(root / "shards" / INBOX)},
        cfg,
        "http://ollama",
        httpx.AsyncClient(transport=ollama_mock(lambda _: "x")),
        embedder=embedder,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://node-1")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write_inbox(tmp_path, dataset_paths(tmp_path, "enronqa"), INBOX, HashEmbedder(), 6)
    return tmp_path


async def test_added_mail_is_embedded_on_the_node_and_timings_are_returned(
    cfg: AtriumConfig, root: Path
) -> None:
    embedder = HashEmbedder()
    content = "a brand new mail about zebras"
    async with _client(enron_cfg(cfg, [INBOX]), root, embedder) as client:
        response = await client.post(
            "/v1/admin/docs/add",
            json={
                "shard_id": INBOX,
                "docs": [{"doc_id": "alice/new", "title": "t", "content": content}],
            },
        )
        body = response.json()
        assert response.status_code == 200
        assert body["n_added"] == 1
        assert body["device"] == "cpu"
        assert body["embedder"] == embedder.name
        assert body["embed_s"] >= 0 and body["add_s"] >= 0 and body["total_s"] >= body["embed_s"]
        assert body["n_visible"] == 7
        query = embedder.encode_texts([content])[0]
        found = (
            await client.post(
                "/v1/retrieve", json={"shard_id": INBOX, "k": 1, "embedding": query.tolist()}
            )
        ).json()
    assert found["docs"][0]["doc_id"] == "alice/new"


async def test_hidden_mail_is_not_retrieved_but_can_be_exported(
    cfg: AtriumConfig, root: Path
) -> None:
    embedder = HashEmbedder()
    doc_id = "alice/inbox/2."
    async with _client(enron_cfg(cfg, [INBOX]), root, embedder) as client:
        shard = (root / "shards" / INBOX / "chunk" / f"{INBOX}.jsonl").read_text("utf-8")
        content = next(line for line in shard.splitlines() if doc_id in line)
        query = embedder.encode_texts([json.loads(content)["content"]])[0]
        hidden = await client.post(
            "/v1/admin/docs/hide", json={"shard_id": INBOX, "doc_ids": [doc_id]}
        )
        assert hidden.json() == {"shard_id": INBOX, "n_hidden": 1, "n_visible": 5}
        found = (
            await client.post(
                "/v1/retrieve", json={"shard_id": INBOX, "k": 3, "embedding": query.tolist()}
            )
        ).json()
        exported = (
            await client.post(
                "/v1/admin/docs/export", json={"shard_id": INBOX, "doc_ids": [doc_id]}
            )
        ).json()
    assert doc_id not in [d["doc_id"] for d in found["docs"]]
    assert exported["docs"][0]["doc_id"] == doc_id


async def test_unknown_document_and_unknown_shard_are_client_errors(
    cfg: AtriumConfig, root: Path
) -> None:
    async with _client(enron_cfg(cfg, [INBOX]), root, HashEmbedder()) as client:
        unknown_doc = await client.post(
            "/v1/admin/docs/hide", json={"shard_id": INBOX, "doc_ids": ["nope"]}
        )
        unknown_shard = await client.post(
            "/v1/admin/docs/export", json={"shard_id": "nope", "doc_ids": None}
        )
    assert unknown_doc.status_code == 400
    assert unknown_shard.status_code == 404


async def test_embedder_load_reports_the_device_and_warms_it_up(
    cfg: AtriumConfig, root: Path
) -> None:
    async with _client(enron_cfg(cfg, [INBOX]), root, HashEmbedder()) as client:
        body = (await client.post("/v1/admin/embedder/load")).json()
    assert body["device"] == "cpu"
    assert body["load_s"] == 0.0  # 注入した埋め込みは読み込み済み
    assert body["warm_s"] >= 0.0


async def test_admin_api_does_not_exist_without_node_admin(cfg: AtriumConfig, root: Path) -> None:
    plain = enron_cfg(cfg, [INBOX], node_admin=False)
    async with _client(plain, root, None) as client:
        hide = await client.post("/v1/admin/docs/hide", json={"shard_id": INBOX, "doc_ids": []})
        availability = await client.post("/v1/admin/availability", json={"up": False})
    assert hide.status_code == 404
    assert availability.status_code == 404


async def test_node_admin_enables_availability_and_a_down_node_refuses_admin_docs(
    cfg: AtriumConfig, root: Path
) -> None:
    async with _client(enron_cfg(cfg, [INBOX]), root, HashEmbedder()) as client:
        assert (await client.post("/v1/admin/availability", json={"up": False})).status_code == 200
        down = await client.post("/v1/admin/docs/hide", json={"shard_id": INBOX, "doc_ids": []})
        assert down.status_code == 503
        await client.post("/v1/admin/availability", json={"up": True})
        up = await client.post("/v1/admin/docs/hide", json={"shard_id": INBOX, "doc_ids": []})
    assert up.status_code == 200


def test_node_without_torch_refuses_to_build_the_default_embedder(
    cfg: AtriumConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """atrium-node のイメージ（torch が無い）では 501 になる（ImportError を再現する）．"""
    monkeypatch.setitem(
        sys.modules, "atrium.encoders", None
    )  # None の項目は import を ImportError にする
    with pytest.raises(NodeError) as error:
        load_cpu_arctic_embedder(enron_cfg(cfg, [INBOX]))
    assert error.value.problem.status == 501


def test_hash_embedder_is_deterministic_and_unit_length() -> None:
    a = HashEmbedder().encode_texts(["x", "y"])
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)
    assert np.array_equal(a[:1], HashEmbedder().encode_texts(["x"]))
    assert a.shape == (2, DIM)
