"""failure-run・join-run のテストが共有する，ノードの生存の確認．"""

from __future__ import annotations

import httpx

from atrium.failure_run import node_alive
from atrium.manifest import Placement


async def alive_hosts(client: httpx.AsyncClient, placement: Placement) -> list[str]:
    """配置のノードのうち，生存の確認（/v1/profile）に答えるホストの一覧（ホスト名の順）．"""
    return sorted([n.host for n in placement.nodes if await node_alive(client, n.url)])
