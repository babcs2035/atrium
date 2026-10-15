"""experiment.pool_assign（httpcore の接続の池の割り当て）の仕様：lean は stock と同じ閉じる接続・残る接続・割り当て先・生存の判定を返し，版が違えば止まる．"""

from __future__ import annotations

import asyncio
import random
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpcore
import httpx
import pytest
import uvicorn
from httpcore._backends.anyio import AnyIOBackend, AnyIOStream

import atrium.requester as requester_module
from atrium.config import AtriumConfig
from atrium.node import create_app
from atrium.requester import (
    _assign_requests_to_connections_lean,
    apply_pool_assign,
)
from atrium.store import load_shard
from tests.conftest import write_faiss_shard

STOCK_ASSIGN = httpcore.AsyncConnectionPool._assign_requests_to_connections
STOCK_GET_EXTRA_INFO = AnyIOStream.get_extra_info


@pytest.fixture(autouse=True)
def restore_httpcore() -> Iterator[None]:
    """各テストの後に httpcore の差し替えと 1 回だけの印を元へ戻す．"""
    yield
    httpcore.AsyncConnectionPool._assign_requests_to_connections = STOCK_ASSIGN  # type: ignore[method-assign]
    AnyIOStream.get_extra_info = STOCK_GET_EXTRA_INFO  # type: ignore[method-assign]
    requester_module._pool_assign_applied = False  # noqa: SLF001


class FakeConnection:
    """池の掃除と割り当てが呼ぶ 5 つのメソッドだけを持つ偽の接続．"""

    def __init__(self, name: str, closed: bool, expired: bool, idle: bool, origin: str) -> None:
        self.name = name
        self._closed = closed
        self._expired = expired
        self._idle = idle
        self.origin = origin

    def is_closed(self) -> bool:
        return self._closed

    def has_expired(self) -> bool:
        return self._expired

    def is_idle(self) -> bool:
        return self._idle

    def is_available(self) -> bool:
        return self._idle

    def can_handle_request(self, origin: str) -> bool:
        return origin == self.origin


class FakeRequestUrl:
    def __init__(self, origin: str) -> None:
        self.origin = origin


class FakeInnerRequest:
    def __init__(self, origin: str) -> None:
        self.url = FakeRequestUrl(origin)


class FakePoolRequest:
    """割り当て先を記録する偽の待ち行列の要求（queued=False は既に割り当て済み）．"""

    def __init__(self, origin: str, queued: bool) -> None:
        self.request = FakeInnerRequest(origin)
        self._queued = queued
        self.assigned: str | None = None

    def is_queued(self) -> bool:
        return self._queued

    def assign_to_connection(self, connection: FakeConnection) -> None:
        self.assigned = connection.name


class FakePool:
    """元の関数と lean の関数を同じ状態に対して呼ぶための偽の池（新しい接続の名前は作った順）．"""

    def __init__(
        self,
        connections: list[FakeConnection],
        requests: list[FakePoolRequest],
        max_connections: int,
        max_keepalive_connections: int,
    ) -> None:
        self._connections = connections
        self._requests = requests
        self._max_connections = max_connections
        self._max_keepalive_connections = max_keepalive_connections
        self._created = 0

    def create_connection(self, origin: str) -> FakeConnection:
        self._created += 1
        return FakeConnection(f"new{self._created}", False, False, False, origin)


def build_pool_state(seed: int, max_connections: int, max_keepalive: int) -> Callable[[], FakePool]:
    """種で決まる同じ状態を何度でも作り直せる関数（元と lean に別々の複製を渡すため）．"""
    rng = random.Random(seed)
    origins = ["a", "b", "c"]
    conn_specs = [
        (rng.random() < 0.15, rng.random() < 0.2, rng.random() < 0.6, rng.choice(origins))
        for _ in range(rng.randint(0, 12))
    ]
    request_specs = [(rng.choice(origins), rng.random() < 0.8) for _ in range(rng.randint(0, 6))]

    def build() -> FakePool:
        connections = [FakeConnection(f"c{i}", *spec) for i, spec in enumerate(conn_specs)]
        requests = [FakePoolRequest(*spec) for spec in request_specs]
        return FakePool(connections, requests, max_connections, max_keepalive)

    return build


def snapshot(pool: FakePool, closing: list[Any]) -> tuple[Any, ...]:
    return (
        [c.name for c in closing],
        [c.name for c in pool._connections],  # noqa: SLF001
        [r.assigned for r in pool._requests],  # noqa: SLF001
    )


@pytest.mark.parametrize("max_keepalive", [sys.maxsize, 1, 3])
@pytest.mark.parametrize("max_connections", [64, 4])
def test_lean_cleanup_and_assignment_equal_stock(max_keepalive: int, max_connections: int) -> None:
    for seed in range(300):
        build = build_pool_state(seed, max_connections, max_keepalive)
        stock_pool, lean_pool = build(), build()
        stock_closing = STOCK_ASSIGN(stock_pool)  # type: ignore[arg-type]
        lean_closing = _assign_requests_to_connections_lean(lean_pool)  # type: ignore[arg-type]
        assert snapshot(lean_pool, lean_closing) == snapshot(stock_pool, stock_closing), seed


def test_default_pool_assign_is_stock(cfg: AtriumConfig) -> None:
    assert cfg.experiment.pool_assign == "stock"


def test_stock_leaves_httpcore_unchanged() -> None:
    version = apply_pool_assign("stock")
    assert version == httpcore.__version__
    assert httpcore.AsyncConnectionPool._assign_requests_to_connections is STOCK_ASSIGN
    assert AnyIOStream.get_extra_info is STOCK_GET_EXTRA_INFO


def test_lean_replaces_both_functions_and_applies_only_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="atrium.requester"):
        assert apply_pool_assign("lean") == httpcore.__version__
        replaced = AnyIOStream.get_extra_info
        assert apply_pool_assign("lean") == httpcore.__version__
    assert (
        httpcore.AsyncConnectionPool._assign_requests_to_connections
        is _assign_requests_to_connections_lean
    )
    assert AnyIOStream.get_extra_info is replaced
    assert replaced is not STOCK_GET_EXTRA_INFO
    assert sum("pool_assign=lean applied" in r.getMessage() for r in caplog.records) == 1


def test_lean_refuses_a_different_httpcore_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpcore, "__version__", "1.0.10")
    with pytest.raises(RuntimeError, match="httpcore"):
        apply_pool_assign("lean")
    assert httpcore.AsyncConnectionPool._assign_requests_to_connections is STOCK_ASSIGN


def test_stock_accepts_a_different_httpcore_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpcore, "__version__", "1.0.10")
    assert apply_pool_assign("stock") == "1.0.10"


async def _is_readable_pair(backend: AnyIOBackend, port: int) -> tuple[bool, bool]:
    stream = await backend.connect_tcp("127.0.0.1", port)
    try:
        # 相手が何も送っていない間は読めない（False）
        readable_idle = stream.get_extra_info("is_readable")
        await asyncio.sleep(0.2)  # 相手が閉じるのを待つ
        return readable_idle, stream.get_extra_info("is_readable")
    finally:
        await stream.aclose()


async def _liveness_values(close_after_s: float) -> list[bool]:
    """相手が何も送らない接続と，相手が閉じた接続の is_readable を (idle, closed) の順に返す．"""

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(close_after_s)
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        idle, closed = await _is_readable_pair(AnyIOBackend(), port)
    finally:
        server.close()
    return [idle, closed]


async def test_lean_is_readable_equals_stock_for_idle_and_closed_peers() -> None:
    stock = await _liveness_values(0.05)
    apply_pool_assign("lean")
    lean = await _liveness_values(0.05)
    assert stock == [False, True]
    assert lean == stock


@pytest.fixture
def live_node(tmp_path: Path, cfg: AtriumConfig) -> Iterator[tuple[str, Any]]:
    """localhost で uvicorn が動かす本物のノード 1 台（url と，同じアプリを ASGI で直接呼ぶための app）．"""
    import numpy as np

    rng = np.random.default_rng(3)
    write_faiss_shard(
        tmp_path, "pubmed-00", "pubmed", {"p0": rng.normal(size=(20, 8)).astype(np.float32)}
    )
    app = create_app(
        "node-a",
        {"pubmed-00": load_shard(tmp_path / "pubmed-00")},
        cfg,
        "http://ollama",
        httpx.AsyncClient(),
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started:
        thread.join(0.01)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}", app
    server.should_exit = True
    thread.join(5)


async def test_lean_responses_from_a_live_node_equal_the_direct_asgi_answers(
    live_node: tuple[str, Any],
) -> None:
    url, app = live_node
    apply_pool_assign("lean")
    rng = random.Random(4)
    bodies = [[rng.uniform(-1, 1) for _ in range(8)] for _ in range(24)]

    async def ask(client: httpx.AsyncClient, base: str, emb: list[float]) -> tuple[Any, Any]:
        probe = await client.post(
            f"{base}/v1/probe", json={"shard_ids": ["pubmed-00"], "embedding": emb}
        )
        docs = await client.post(
            f"{base}/v1/retrieve", json={"shard_id": "pubmed-00", "k": 3, "embedding": emb}
        )
        return probe.json()["scores"], docs.json()["docs"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://asgi"
    ) as direct:
        expected = [await ask(direct, "http://asgi", e) for e in bodies]
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=8)) as client:
        actual = await asyncio.gather(*(ask(client, url, e) for e in bodies))
    assert list(actual) == expected
