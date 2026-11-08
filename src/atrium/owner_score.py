"""bm25_probe の probe の 2 段目で受信箱を並べる得点の作り方（d0009 B3．Iteration 24．質問者の requester とオフラインの owner_score_eval が共有する）．

入力は，受信箱ごとの密ベクトルの内積・BM25 の上位 K 個の得点（降順．文書が K 個に満たなければ NaN で埋める）と，probe に答えた受信箱の印．
どの関数も (質問, 受信箱, …) の配列を受け取り，質問ごとに独立に計算する（質問者は質問 1 つ分の配列を渡す）．

- fused_max（従来）：z(max dense) + wb × z(max bm25)．`atrium.routing.summary.Bm25ProbeRouter.fused_scores` が計算する（ここには含めない）．
- vote（Macdonald & Ounis 2006 の CombSUM・expCombSUM に当たる）：1 問の中で答えた全受信箱の上位 k 個の得点をまとめて標準化（平均・母標準偏差）し，
  受信箱ごとに Σ z（sum）または Σ exp(z)（expsum）を取って受信箱の得点 agg とし，答えた受信箱の中で標準化して z(agg dense) + wb × z(agg bm25)．
- learned：7 個の特徴量（答えた受信箱の中で標準化した max dense・max bm25・sum@5 dense・sum@5 bm25・expsum@5 dense・expsum@5 bm25・
  1 段目の要約の得点）の線形和．重みは dev の質問だけからロジスティック回帰で学習し，質問者だけが持つ（JSON）．
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt

from atrium.arrays import F64Array

AggName = Literal["sum", "expsum"]
# 標準化の分母に足す下限（atrium.frontier_methods.Z_EPSILON と同じ値．あちらは重い import を伴うので複製する）
Z_EPSILON = 1e-9
BoolArray = npt.NDArray[np.bool_]
# learned の特徴量の上位文書の数（sum@5・expsum@5）
LEARNED_K = 5
LEARNED_FEATURES = (
    "max_dense",
    "max_bm25",
    "sum5_dense",
    "sum5_bm25",
    "expsum5_dense",
    "expsum5_bm25",
    "first_stage",
)


def masked_zscore(values: F64Array, mask: BoolArray) -> F64Array:
    """質問ごとに，mask の列（probe に答えた受信箱）の中だけで標準化する（平均・母標準偏差．分母に Z_EPSILON を足す）．mask 外の値は 0．"""
    n = np.maximum(mask.sum(axis=1, keepdims=True), 1)
    kept = np.where(mask, values, 0.0)
    mean = kept.sum(axis=1, keepdims=True) / n
    var = (np.where(mask, values - mean, 0.0) ** 2).sum(axis=1, keepdims=True) / n
    out: F64Array = np.where(mask, (values - mean) / (np.sqrt(var) + Z_EPSILON), 0.0)
    return out


def vote_aggregate(top: F64Array, mask: BoolArray, agg: AggName, k: int) -> F64Array:
    """(質問, 受信箱, K) の上位の得点から，(質問, 受信箱) の受信箱の得点 agg を作る（上位 k 個を使う．NaN と mask 外は数えない）．

    標準化の平均・母標準偏差は，1 問の中で答えた全受信箱の上位 k 個の得点をまとめて求める．
    """
    part = top[:, :, :k]
    valid = np.isfinite(part) & mask[:, :, None]
    count = np.maximum(valid.sum(axis=(1, 2)), 1)
    mean = np.where(valid, part, 0.0).sum(axis=(1, 2)) / count
    var = (np.where(valid, part - mean[:, None, None], 0.0) ** 2).sum(axis=(1, 2)) / count
    z = (part - mean[:, None, None]) / (np.sqrt(var) + Z_EPSILON)[:, None, None]
    voted = z if agg == "sum" else np.exp(z)
    out: F64Array = np.where(valid, voted, 0.0).sum(axis=2)
    return out


def vote_scores(
    dense_top: F64Array, bm25_top: F64Array, mask: BoolArray, agg: AggName, k: int, wb: float
) -> F64Array:
    """vote の得点 z(agg dense) + wb × z(agg bm25)（答えた受信箱だけ．それ以外は −inf）．"""
    dense = masked_zscore(vote_aggregate(dense_top, mask, agg, k), mask)
    bm25 = masked_zscore(vote_aggregate(bm25_top, mask, agg, k), mask)
    out: F64Array = np.where(mask, dense + wb * bm25, -np.inf)
    return out


def learned_features(
    dense_top: F64Array, bm25_top: F64Array, mask: BoolArray, first_stage: F64Array
) -> F64Array:
    """learned の特徴量 (質問, 受信箱, 7)．LEARNED_FEATURES の順で，どれも答えた受信箱の中で標準化する．first_stage は受信箱が載るデバイスの 1 段目の得点．"""
    columns = [
        dense_top[:, :, 0],
        bm25_top[:, :, 0],
        vote_aggregate(dense_top, mask, "sum", LEARNED_K),
        vote_aggregate(bm25_top, mask, "sum", LEARNED_K),
        vote_aggregate(dense_top, mask, "expsum", LEARNED_K),
        vote_aggregate(bm25_top, mask, "expsum", LEARNED_K),
        first_stage,
    ]
    out: F64Array = np.stack([masked_zscore(c, mask) for c in columns], axis=2)
    return out


@dataclass(frozen=True)
class LearnedRanker:
    """learned の重み（特徴量の順は LEARNED_FEATURES．切片は順位に影響しないが記録する）．"""

    coef: tuple[float, ...]
    intercept: float

    def scores(self, features: F64Array, mask: BoolArray) -> F64Array:
        """(質問, 受信箱, 7) の特徴量から (質問, 受信箱) の得点を作る（答えた受信箱だけ．それ以外は −inf）．"""
        linear: F64Array = features @ np.asarray(self.coef, dtype=np.float64)
        out: F64Array = np.where(mask, linear, -np.inf)
        return out

    def write(self, path: Path, meta: dict[str, object]) -> None:
        """重みを JSON に書く（meta は学習の条件の記録）．"""
        payload = {
            "features": list(LEARNED_FEATURES),
            "coef": list(self.coef),
            "intercept": self.intercept,
            **meta,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def load_ranker(path: Path) -> LearnedRanker:
    """`LearnedRanker.write` が書いた JSON を読む（特徴量の並びが違えば拒否する）．"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["features"] != list(LEARNED_FEATURES):
        raise ValueError(f"{path}: features {payload['features']} != {list(LEARNED_FEATURES)}")
    return LearnedRanker(
        coef=tuple(float(c) for c in payload["coef"]), intercept=float(payload["intercept"])
    )
