"""ルーティング（問い合わせ先のデータ源の選択）．

新しい方式（E2 以降の RQ1 の手法など）は `Router` プロトコルを満たすクラスを作り，
`make_router()` に名前を登録する．入力はクエリと各データ源の自己紹介（SourceProfile）だけであり，
中央で全データ源のラベルを見て学習した情報を使うかどうかは方式の側で明示する．
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from atrium.arrays import F32Array
from atrium.config import AtriumConfig
from atrium.manifest import combine_centroids
from atrium.protocol import ShardProfile


@dataclass(frozen=True)
class RoutingQuery:
    """ルーターへの入力となるクエリ．embeddings は検索器名 → クエリ埋め込み．"""

    query_id: str
    question: str
    embeddings: dict[str, F32Array]


@dataclass(frozen=True)
class SourceProfile:
    """データ源の自己紹介．各ノードの /v1/profile をデータ源単位にまとめたもの．"""

    source: str
    description: str
    encoder: str
    centroid: F32Array
    n_docs: int
    shard_ids: tuple[str, ...]


class Router(Protocol):
    """ルーティング方式の共通インタフェース．"""

    name: str

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """問い合わせるデータ源の名前を返す．"""
        ...


def build_source_profiles(
    shards: Sequence[ShardProfile], source_order: Sequence[str]
) -> list[SourceProfile]:
    """シャードの自己紹介をデータ源ごとにまとめ，source_order の順に並べる．"""
    profiles: list[SourceProfile] = []
    for source in source_order:
        members = [s for s in shards if s.source == source]
        if not members:
            continue
        profiles.append(
            SourceProfile(
                source=source,
                description=members[0].description,
                encoder=members[0].encoder,
                centroid=combine_centroids(
                    [m.n_docs for m in members], [m.centroid for m in members]
                ),
                n_docs=sum(m.n_docs for m in members),
                shard_ids=tuple(sorted(m.shard_id for m in members)),
            )
        )
    return profiles


class AllRouter:
    """全てのデータ源に問い合わせる（RAGRoute の all）．"""

    name = "all"

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """全データ源を返す．"""
        return [s.source for s in sources]


class NoneRouter:
    """どのデータ源にも問い合わせない（RAGRoute の none）．"""

    name = "none"

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """空の一覧を返す．"""
        return []


class RandomRouter:
    """データ源を k 個無作為に選ぶ（RAGRoute の random）．

    質問 ID と種から乱数を作るので，同じ設定なら並列度や実行順によらず同じ選択になる．
    """

    name = "random"

    def __init__(self, k: int, seed: int) -> None:
        """選ぶ数と乱数の種を持つ．"""
        self._k = k
        self._seed = seed

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """k 個（データ源が少なければ全て）を選ぶ．"""
        names = [s.source for s in sources]
        rng = random.Random(f"{self._seed}:{query.query_id}")
        chosen = set(rng.sample(names, min(self._k, len(names))))
        return [n for n in names if n in chosen]


def make_router(cfg: AtriumConfig, router_dir: Path) -> Router:
    """config.yaml の experiment.routing に対応するルーターを作る．"""
    dataset = cfg.experiment.dataset
    name = cfg.experiment.routing
    if name == "all":
        return AllRouter()
    if name == "none":
        return NoneRouter()
    if name == "random":
        return RandomRouter(cfg.routing.random_k[dataset], cfg.experiment.seed)
    # torch を必要とするので，使うときだけ読み込む
    from atrium.routing.ragroute import RagrouteRouter

    return RagrouteRouter(router_dir, cfg.data.sources_of(dataset), cfg.routing.ragroute_threshold)
