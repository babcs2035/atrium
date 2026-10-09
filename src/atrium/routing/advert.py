"""公開情報（Advert）を使うルーティング方式と，その採点の関数（p0004 §6.1 の RQ-A）．

各方式は，データ源が事前に公開した情報だけを使い，クエリとの採点の上位 m 個に問い合わせる．
採点の関数は，実機の質問者（`Router.select`）とオフラインの評価（`atrium advert-eval`）で共通に使う．

| 方式 | 公開するもの | 採点 |
|---|---|---|
| oracle | － | 正解の受信箱だけ（評価用の上限．実運用では使えない） |
| flood_score | なし | 1 段目に全データ源へクエリを送り，各データ源の最高の検索スコアを返してもらう |
| card_sim | Agent Card の説明文の埋め込み | クエリ埋め込みとの内積 |
| term_sketch | 多く使う語と，その語を含むメールの割合 | クエリの語と重なる語の割合の和 |
| centroid_sim | 埋め込みの平均（重心） | クエリ埋め込みとの内積 |
| multi_centroid | k-means の C 個の中心 | 中心との内積の最大 |
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import numpy as np

from atrium.arrays import F32Array
from atrium.manifest import Advert
from atrium.routing import RoutingQuery, SourceProfile

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """小文字の英数字の語に分ける（語のスケッチ・BM25・重複の検出・クエリの語で共通）．"""
    return _TOKEN.findall(text.lower())


def top_m(names: Sequence[str], scores: Sequence[float], m: int) -> list[str]:
    """採点の高い順に m 個を，元の順序（names の順）で返す（同点は names の先の方を採る）．"""
    order = sorted(range(len(names)), key=lambda i: (-scores[i], i))[:m]
    chosen = set(order)
    return [n for i, n in enumerate(names) if i in chosen]


def term_score(query_tokens: set[str], advert: Advert) -> float:
    """クエリの語と重なるスケッチの語の重み（その語を含むメールの割合）の和．"""
    return float(
        sum(w for t, w in zip(advert.terms, advert.term_weights, strict=True) if t in query_tokens)
    )


def centroid_score(query: F32Array, centroid: F32Array) -> float:
    """重心との内積．"""
    return float(np.dot(query, centroid))


def multi_centroid_score(query: F32Array, centroids: F32Array) -> float:
    """C 個の中心との内積の最大．"""
    return float(np.max(centroids @ query))


def card_score(query: F32Array, card_embedding: F32Array) -> float:
    """Agent Card の説明文の埋め込みとの内積．"""
    return float(np.dot(query, card_embedding))


def _require_advert(source: SourceProfile) -> Advert:
    if source.advert is None:
        raise ValueError(f"source {source.source!r} does not publish an advert")
    return source.advert


class _AdvertRouter:
    """公開情報の採点の上位 m 個に問い合わせる方式の共通部分．"""

    name = ""

    def __init__(self, m: int) -> None:
        """問い合わせるデータ源の数 m を持つ．"""
        self.m = m

    def score(self, query: RoutingQuery, source: SourceProfile) -> float:
        """データ源 1 個の採点（方式ごとに定める）．"""
        raise NotImplementedError

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """採点の上位 m 個を返す．"""
        return top_m([s.source for s in sources], [self.score(query, s) for s in sources], self.m)

    def advert_bytes(self, sources: Sequence[SourceProfile]) -> int:
        """この方式が使う公開情報の大きさの合計（JSON のバイト数）．"""
        raise NotImplementedError


def _embedding_of(query: RoutingQuery, source: SourceProfile) -> F32Array:
    return query.embeddings[source.encoder]


class CardSimRouter(_AdvertRouter):
    """Agent Card の説明文の埋め込みとの類似度．"""

    name = "card_sim"

    def score(self, query: RoutingQuery, source: SourceProfile) -> float:
        """説明文の埋め込みとの内積．"""
        advert = _require_advert(source)
        return card_score(
            _embedding_of(query, source), np.asarray(advert.card_embedding, np.float32)
        )

    def advert_bytes(self, sources: Sequence[SourceProfile]) -> int:
        """説明文の文章の大きさ（受け取った側が埋め込むので，文章だけを数える）．"""
        return sum(len(s.description.encode("utf-8")) for s in sources)


class TermSketchRouter(_AdvertRouter):
    """語のスケッチとクエリの語の重なり．"""

    name = "term_sketch"

    def score(self, query: RoutingQuery, source: SourceProfile) -> float:
        """クエリの語と重なる語の重みの和．"""
        return term_score(set(query.tokens), _require_advert(source))

    def advert_bytes(self, sources: Sequence[SourceProfile]) -> int:
        """語と重みの JSON の大きさ．"""
        return sum(
            len(_require_advert(s).model_dump_json(include={"terms", "term_weights"}).encode())
            for s in sources
        )


class CentroidSimRouter(_AdvertRouter):
    """重心との内積（p0003 の E2 の案と共通）．"""

    name = "centroid_sim"

    def score(self, query: RoutingQuery, source: SourceProfile) -> float:
        """重心との内積．"""
        return centroid_score(_embedding_of(query, source), source.centroid)

    def advert_bytes(self, sources: Sequence[SourceProfile]) -> int:
        """重心（fp32 の JSON）の大きさ．"""
        return sum(len(str(s.centroid.tolist()).encode()) for s in sources)


class MultiCentroidRouter(_AdvertRouter):
    """k-means の C 個の中心との内積の最大．"""

    name = "multi_centroid"

    def score(self, query: RoutingQuery, source: SourceProfile) -> float:
        """中心との内積の最大．"""
        advert = _require_advert(source)
        return multi_centroid_score(
            _embedding_of(query, source), np.asarray(advert.centroids, np.float32)
        )

    def advert_bytes(self, sources: Sequence[SourceProfile]) -> int:
        """中心の JSON の大きさ．"""
        return sum(
            len(_require_advert(s).model_dump_json(include={"centroids"}).encode()) for s in sources
        )


class FloodScoreRouter:
    """1 段目に全データ源へクエリを送り，各データ源の最高の検索スコアの上位 m 個に本照会する．

    事前に公開する情報は無いが，質問（のクエリ埋め込み）は全データ源に届く．
    最高スコアは質問者が 1 段目（`/v1/probe`）で集め，RoutingQuery.probe_scores として渡す．
    """

    name = "flood_score"
    needs_probe = True

    def __init__(self, m: int) -> None:
        """本照会するデータ源の数 m を持つ．"""
        self.m = m

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """最高スコアの上位 m 個を返す．"""
        if query.probe_scores is None:
            raise ValueError("flood_score requires probe scores from the first stage")
        scores = [query.probe_scores.get(s.source, float("-inf")) for s in sources]
        return top_m([s.source for s in sources], scores, self.m)

    def advert_bytes(self, sources: Sequence[SourceProfile]) -> int:
        """事前に公開する情報は無い．"""
        return 0


class OracleRouter:
    """正解のデータ源だけに問い合わせる（評価用の上限．実運用では使えない）．"""

    name = "oracle"

    def __init__(self, labels: Mapping[str, Sequence[str]]) -> None:
        """質問 ID → 関連ありのデータ源の表を持つ．"""
        self._labels = labels

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """関連ありのデータ源を返す（ラベルの無い質問は空）．"""
        relevant = set(self._labels.get(query.query_id, []))
        return [s.source for s in sources if s.source in relevant]

    def advert_bytes(self, sources: Sequence[SourceProfile]) -> int:
        """公開情報を使わない．"""
        return 0
