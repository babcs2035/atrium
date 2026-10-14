"""experiment.http_pool（質問者の HTTP 接続の持ち方）で，要求がどの接続の池に振り分けられるかの仕様を確かめる．"""

from __future__ import annotations

import sys

import httpx
import pytest

from atrium.config import AtriumConfig
from atrium.experiment import MAX_CONNECTIONS, MAX_CONNECTIONS_PER_NODE, build_http_client
from atrium.manifest import NodeAssignment, Placement

NODE_URLS = ["http://192.168.13.100:8000", "http://192.168.13.101:8000"]
OLLAMA_URL = "http://localhost:11434/api/chat"


def _placement() -> Placement:
    return Placement(
        dataset="enronqa",
        nodes=[
            NodeAssignment(host=f"h{i}", url=url, shard_ids=[f"s{i}"])
            for i, url in enumerate(NODE_URLS)
        ],
    )


def _pool_limit(transport: httpx.AsyncBaseTransport) -> int | None:
    assert isinstance(transport, httpx.AsyncHTTPTransport)
    return transport._pool._max_connections  # noqa: SLF001  httpx は上限を公開していない


def test_default_http_pool_is_shared(cfg: AtriumConfig) -> None:
    assert cfg.experiment.http_pool == "shared"


@pytest.mark.asyncio
async def test_shared_uses_one_pool_with_the_original_limit() -> None:
    async with build_http_client("shared", _placement()) as client:
        pools = {id(client._transport_for_url(httpx.URL(u))) for u in [*NODE_URLS, OLLAMA_URL]}  # noqa: SLF001
        assert len(pools) == 1
        assert _pool_limit(client._transport) == MAX_CONNECTIONS  # noqa: SLF001


@pytest.mark.asyncio
async def test_shared_unlimited_uses_one_pool_without_limit() -> None:
    async with build_http_client("shared_unlimited", _placement()) as client:
        pools = {id(client._transport_for_url(httpx.URL(u))) for u in [*NODE_URLS, OLLAMA_URL]}  # noqa: SLF001
        assert len(pools) == 1
        # httpcore は上限なし（None）を sys.maxsize として持つ
        assert _pool_limit(client._transport) == sys.maxsize  # noqa: SLF001


@pytest.mark.asyncio
async def test_per_node_routes_each_node_origin_to_its_own_pool() -> None:
    async with build_http_client("per_node", _placement()) as client:
        a, b = (client._transport_for_url(httpx.URL(f"{u}/v1/probe")) for u in NODE_URLS)  # noqa: SLF001
        assert a is not b
        assert _pool_limit(a) == _pool_limit(b) == MAX_CONNECTIONS_PER_NODE
        # 同じノードへの別のパスは同じ池
        again = client._transport_for_url(httpx.URL(f"{NODE_URLS[0]}/v1/retrieve"))  # noqa: SLF001
        assert again is a


@pytest.mark.asyncio
async def test_per_node_sends_unmounted_destinations_to_the_default_pool() -> None:
    async with build_http_client("per_node", _placement()) as client:
        default = client._transport_for_url(httpx.URL(OLLAMA_URL))  # noqa: SLF001
        assert default is client._transport  # noqa: SLF001
        assert _pool_limit(default) == MAX_CONNECTIONS
