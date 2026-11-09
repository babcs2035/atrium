"""質問者の生存確認（d0009 B1．`experiment.peer_health`）：止まったと判定したノードを `Requester.visible_urls` から外し，生存が戻れば戻す．

使い方（failure-run の中で使う．`atrium.failure_run.run_failure_command` が組み立てる）:
    monitor = PeerHealthMonitor(client, requester, url_of_host, cfg.experiment.peer_health)
    await monitor.start()   # 最初の確認で全ノードが生存のときだけ戻る（止まっていれば RuntimeError）
    ...                     # monitor.excluded_hosts / monitor.events を読む
    await monitor.stop()

ノードごとに独立に，間隔 interval_s・1 回の打ち切り timeout_s で `GET /v1/alive`（離脱中は 503，コンテナが止まれば接続の失敗）を確かめる．
連続 fail_threshold 回の失敗で外し，連続 recover_threshold 回の成功で戻す（Envoy の能動の health check の
unhealthy_threshold・healthy_threshold と同じ考え方）．外すのは止まったと判定したノードそのものだけで，
`requester.py` の経路は変えず，メンバー表（visible_urls．churn と同じ）を更新するだけにする．
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from atrium.config import PeerHealthConfig
from atrium.requester import Requester

logger = logging.getLogger(__name__)

EVENT_DROP = "drop"
EVENT_RESTORE = "restore"


@dataclass(frozen=True)
class PeerEvent:
    """外した・戻した事象．at は time.perf_counter の値（failure-run の時刻 0 からの秒は呼び出し側が引く）．"""

    host: str
    kind: str
    at: float


@dataclass
class PeerHealthTracker:
    """ノードごとの連続の失敗・成功の数から，外す・戻すを決める（通信を持たない）．"""

    cfg: PeerHealthConfig
    excluded_hosts: set[str] = field(default_factory=set)
    _fails: dict[str, int] = field(default_factory=dict)
    _oks: dict[str, int] = field(default_factory=dict)

    def observe(self, host: str, alive: bool, at: float) -> PeerEvent | None:
        """確認 1 回の結果を入れ，外した・戻したときだけ事象を返す．"""
        if alive:
            self._fails[host] = 0
            self._oks[host] = self._oks.get(host, 0) + 1
            if host in self.excluded_hosts and self._oks[host] >= self.cfg.recover_threshold:
                self.excluded_hosts.discard(host)
                return PeerEvent(host, EVENT_RESTORE, at)
            return None
        self._oks[host] = 0
        self._fails[host] = self._fails.get(host, 0) + 1
        if host not in self.excluded_hosts and self._fails[host] >= self.cfg.fail_threshold:
            self.excluded_hosts.add(host)
            return PeerEvent(host, EVENT_DROP, at)
        return None


async def check_alive(client: httpx.AsyncClient, url: str, timeout_s: float) -> bool:
    """`/v1/alive` が 200 を返すか（200 以外・接続の失敗・打ち切りは失敗）．応答の無いノードでも timeout_s で返るよう httpx と asyncio の両方で打ち切る．"""
    try:
        response = await asyncio.wait_for(
            client.get(f"{url}/v1/alive", timeout=timeout_s), timeout_s
        )
    except (httpx.HTTPError, TimeoutError):
        return False
    return response.status_code == 200


class PeerHealthMonitor:
    """全ノードの生存を定期に確かめ，外したノードの URL を質問者の visible_urls から除く非同期の監視．"""

    def __init__(
        self,
        client: httpx.AsyncClient,
        requester: Requester,
        url_of_host: Mapping[str, str],
        cfg: PeerHealthConfig,
    ) -> None:
        """質問者・ホスト → ノードの URL・生存確認の条件．"""
        self._client = client
        self._requester = requester
        self._url_of_host = dict(url_of_host)
        self._cfg = cfg
        self._tracker = PeerHealthTracker(cfg)
        self._tasks: list[asyncio.Task[None]] = []
        self.events: list[PeerEvent] = []

    @property
    def excluded_hosts(self) -> frozenset[str]:
        """今，外しているホスト．"""
        return frozenset(self._tracker.excluded_hosts)

    def _apply(self, host: str, alive: bool) -> None:
        """確認の結果を入れ，外す・戻すがあれば事象を残して visible_urls を更新する．"""
        event = self._tracker.observe(host, alive, time.perf_counter())
        if event is None:
            return
        self.events.append(event)
        logger.info("peer health: %s %s", event.kind, event.host)
        self._requester.visible_urls = frozenset(
            url for h, url in self._url_of_host.items() if h not in self._tracker.excluded_hosts
        )

    async def start(self) -> None:
        """最初の確認で全ノードが生存のときだけ，ノードごとの確認の繰り返しを始める（止まったノードがあれば RuntimeError）．"""
        first = await asyncio.gather(
            *(check_alive(self._client, u, self._cfg.timeout_s) for u in self._url_of_host.values())
        )
        down = sorted(h for h, ok in zip(self._url_of_host, first, strict=True) if not ok)
        if down:
            raise RuntimeError(f"peer health: nodes not alive at the start: {down}")
        # visible_urls を全ノードにして以降の更新を差分にする（None のままだと最初の外しで全ノードの集合を作れない）
        self._requester.visible_urls = frozenset(self._url_of_host.values())
        self._tasks = [asyncio.create_task(self._watch(h)) for h in self._url_of_host]

    async def _watch(self, host: str) -> None:
        """1 ノードを interval_s ごとに確かめ続ける（確認の所要時間を間隔に含める）．"""
        url = self._url_of_host[host]
        while True:
            begun = time.perf_counter()
            self._apply(host, await check_alive(self._client, url, self._cfg.timeout_s))
            await asyncio.sleep(max(0.0, self._cfg.interval_s - (time.perf_counter() - begun)))

    async def stop(self) -> None:
        """確認の繰り返しを止める．visible_urls は元の None に戻す．"""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._requester.visible_urls = None
