"""語の統計・本文の重心による要約で上位 D 台を選ぶ二段の Router（p0006 P4．hybrid_probe・term_probe）．

オフラインの評価（atrium.frontier_eval の term_stats・hybrid_summary）と同じ関数で採点する．要約は `atrium build-summary` が
配置ごとに作った成果物（`<data_dir>/<dataset>/summary/`）で，質問者が読む．ノードの API と公開情報の構造は変えない．
実機の質問者は `candidates` で probe を送るデバイスを決め，`select_among` で probe の最高スコアの上位 m 個を選ぶ（device_probe と同じ流れ）．

- term_probe：CORI 型の語の統計の得点で上位 D 台．
- hybrid_probe：語の統計の得点と，本文の重心のデバイスごとの最大を，質問ごとにデバイス間で標準化して weight（重心側）で足した得点で上位 D 台．
成果物の公開情報（語の統計・重心）は，デバイスが質問者へ一度だけ公開するものとして，ここでは質問者が読み込む．
その送信の費用と待ち時間は実機で測っていない（質問ごとの要求数には含まれない）．
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from atrium.arrays import F32Array
from atrium.routing import RoutingQuery, SourceProfile
from atrium.routing.advert import top_m

SUMMARY_DIRNAME = "summary"
META_FILE = "meta.json"
VOCAB_FILE = "vocab.json"
WEIGHTS_FILE = "weights.npz"
CENTERS_FILE = "centers.npz"
FLOAT32_BYTES = 4
# 語の統計の 1 組（語の番号と文書頻度）を float32 の 2 個として数える（frontier_methods と同じ）
TERM_ENTRY_BYTES = 8
Mode = Literal["term", "hybrid"]


@dataclass(frozen=True)
class SummaryArtifact:
    """配置ごとの要約：デバイスごとの語の統計と，データ源ごとの本文の重心．"""

    groups: list[list[str]]
    vocab: dict[str, int]
    weights: Any
    cf: np.ndarray[Any, Any]
    centers: list[F32Array]
    term_size: int
    names: list[str]


def write_summary(
    out_dir: Path,
    names: list[str],
    groups: list[list[str]],
    vocab: dict[str, int],
    weights: Any,
    cf: np.ndarray[Any, Any],
    centers: list[F32Array],
    term_size: int,
) -> None:
    """要約の成果物を書く（デバイスの並びは groups の順．データ源の並びは names の順）．"""
    from scipy import sparse

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / META_FILE).write_text(
        json.dumps({"names": names, "groups": groups, "term_size": term_size}, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / VOCAB_FILE).write_text(
        json.dumps(sorted(vocab, key=lambda t: vocab[t]), ensure_ascii=False), encoding="utf-8"
    )
    sparse.save_npz(out_dir / WEIGHTS_FILE, weights.tocsr())
    np.savez(
        out_dir / CENTERS_FILE,
        cf=cf,
        flat=np.concatenate(centers).astype(np.float32),
        lens=np.asarray([len(c) for c in centers]),
    )


def load_summary(summary_dir: Path) -> SummaryArtifact:
    """要約の成果物を読む．"""
    from scipy import sparse

    meta = json.loads((summary_dir / META_FILE).read_text(encoding="utf-8"))
    terms = json.loads((summary_dir / VOCAB_FILE).read_text(encoding="utf-8"))
    centers_file = np.load(summary_dir / CENTERS_FILE)
    flat, lens = centers_file["flat"], centers_file["lens"]
    return SummaryArtifact(
        groups=meta["groups"],
        vocab={t: i for i, t in enumerate(terms)},
        weights=sparse.load_npz(summary_dir / WEIGHTS_FILE),
        cf=centers_file["cf"],
        centers=list(np.split(flat, np.cumsum(lens)[:-1])),
        term_size=int(meta["term_size"]),
        names=list(meta["names"]),
    )


class SummaryProbeRouter:
    """要約の得点の上位 D 台にだけ probe を送り，probe の最高スコアの上位 m 個に本照会する二段の方式．"""

    def __init__(
        self,
        mode: Mode,
        m: int,
        probe_d: int,
        artifact: SummaryArtifact,
        encoder: str,
        weight: float,
    ) -> None:
        """本照会するデータ源の数 m，probe を送るデバイスの数 D，要約，クエリ埋め込みの検索器名，併用の重みを持つ．"""
        if probe_d < 1:
            raise ValueError(f"probe_d ({probe_d}) must be at least 1")
        self.name = f"{mode}_probe"
        self.mode = mode
        self.m = m
        self.probe_d = probe_d
        self._artifact = artifact
        self._encoder = encoder
        self._weight = weight

    def _device_index(self, names: Sequence[str], device_of_source: Mapping[str, str]) -> list[int]:
        """データ源ごとのデバイスの番号（names の中で最初に現れた順．要約のデバイスの並びと同じでなければならない）．"""
        order: dict[str, int] = {}
        index = [order.setdefault(device_of_source[n], len(order)) for n in names]
        live_groups = [
            sorted(n for n, i in zip(names, index, strict=True) if i == d)
            for d in range(len(order))
        ]
        if live_groups != [sorted(g) for g in self._artifact.groups]:
            raise ValueError(
                "the summary was built for a different placement; rerun atrium build-summary"
            )
        return index

    def candidates(
        self,
        query: RoutingQuery,
        sources: Sequence[SourceProfile],
        device_of_source: Mapping[str, str],
    ) -> list[str]:
        """1 段目：要約の得点の上位 D 台のデバイスに載る全てのデータ源（probe を送る先）を names の順で返す．"""
        from atrium.frontier_methods import aggregate_max, cori_scores, multi_scores, zscore_rows

        names = [s.source for s in sources]
        if names != self._artifact.names:
            raise ValueError("the sources differ from those the summary was built for")
        index = np.asarray(self._device_index(names, device_of_source), dtype=np.int64)
        terms = cori_scores(
            self._artifact.vocab, self._artifact.weights, self._artifact.cf, [list(query.tokens)]
        )
        if self.mode == "term":
            scores = terms
        else:
            emb = np.asarray(query.embeddings[self._encoder], dtype=np.float32)[None, :]
            centroid = aggregate_max(multi_scores(emb, self._artifact.centers), index)
            scores = zscore_rows(terms) + self._weight * zscore_rows(centroid)
        top = np.argsort(-scores[0], kind="stable")[: self.probe_d]
        chosen = set(top.tolist())
        return [n for n, d in zip(names, index.tolist(), strict=True) if d in chosen]

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """デバイスの対応はルーターの外にあるので，質問者の candidates と select_among を使う．"""
        raise ValueError(f"{self.name} needs the device of each source; use candidates()")

    def select_among(
        self, candidates: Sequence[str], probe_scores: Mapping[str, float]
    ) -> list[str]:
        """求め済みの候補のうち，probe の最高スコアの上位 m 個を返す．"""
        scores = [probe_scores.get(c, float("-inf")) for c in candidates]
        return top_m(candidates, scores, self.m)

    def advert_bytes(self, sources: Sequence[SourceProfile]) -> int:
        """公開する要約の大きさ（float32 で数えた値．語の統計はデバイスごとに term_size 個の組）．"""
        terms = self._artifact.term_size * TERM_ENTRY_BYTES * len(self._artifact.groups)
        if self.mode == "term":
            return terms
        return terms + sum(c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in self._artifact.centers)
