"""frontier-eval の核（p0006 P2-0）：データに依らない評価の入力・方式の型・指標．

ルーティングの方式は，1 問あたりの要求数（デバイス単位）・往復の回数・見つけやすさ・露出の 4 軸で測り，
方式×設定ごとに 1 行（frontier の 1 点）にする．方式は `frontier_methods` のレジストリに足すだけで加わる．

- 質問者の最終の選択は，どの方式でも「probe したデバイスの受信箱の 2 段目の得点（各デバイスの検索器の最大得点）の上位 m 個」
  （要約だけで選ぶ方式は 1 段目の得点の上位 m 個）である．要求数 = probe したデバイス数 + 方式固有の追加 + m（本照会）．
- hit は，既存の評価と同じ規則（正解の受信箱より得点が高いか同点の受信箱の数を順位とする．同点は不利な側）で数える．
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from atrium.advert_eval import bootstrap_ci, owner_auc
from atrium.arrays import F32Array, F64Array, I64Array

BoolMat = np.ndarray[Any, np.dtype[np.bool_]]
SPLITS = ("dev", "test")
# 順位の行列を作るときに質問を分ける単位（質問 × 受信箱 × 受信箱の比較の行列がメモリに収まるようにする）
RANK_CHUNK = 128
# 質問を受け取るデバイスの要求数の p95
REQUEST_PERCENTILE = 95


@dataclass(frozen=True)
class ProbeDocs:
    """出どころの特定（§7.1）に使う，正解でないメール．行ごとに埋め込み・語・持ち主の受信箱の番号．"""

    emb: F32Array
    tokens: list[list[str]]
    owner_source: I64Array


@dataclass
class FrontierData:
    """1 つのデータ・1 つの配置の評価の入力．"""

    dataset: str
    placement: str
    names: list[str]
    device_of_source: I64Array
    m: int
    target_hit: float
    second: dict[str, F32Array]
    relevant: dict[str, BoolMat]
    q_emb: dict[str, F32Array]
    q_tokens: dict[str, list[list[str]]]
    subsets: dict[str, dict[str, BoolMat]]
    seed: int
    # 要約の元（本文から作る埋め込み・語）．持たないデータでは None（その要約を使う方式は作れない）
    summary_emb: list[F32Array] | None = None
    summary_tokens: list[list[list[str]]] | None = None
    # 既存の方式（multi_centroid・centroid_probe・device_probe）の 1 段目：ヘッダー込みの重心
    full_centers: list[F32Array] | None = None
    exclude_docs: list[set[int]] = field(default_factory=list)
    cache: dict[str, Any] = field(default_factory=dict)

    @property
    def n_devices(self) -> int:
        """デバイスの数．"""
        return int(self.device_of_source.max()) + 1

    @property
    def n_sources(self) -> int:
        """受信箱（データ源）の数．"""
        return len(self.names)


@dataclass(frozen=True)
class Selection:
    """方式が 1 つの split の全質問に対して決めた，probe する受信箱と追加の要求．

    candidates が None のときは，direct（1 段目の得点）の上位 m 個をそのまま選ぶ（probe をしない）．
    """

    candidates: BoolMat | None
    direct: F32Array | None = None
    extra_requests: F64Array | None = None
    extra_round_trips: F64Array | None = None


@dataclass(frozen=True)
class Config:
    """方式の 1 つの設定（frontier の 1 点）．"""

    family: str
    method: str
    params: dict[str, Any]
    advert_bytes: int
    select: Callable[[str], Selection]
    # 公開情報から出どころを当てる採点 (P, n_devices)．公開情報が無い・測らない方式は None
    attribute: Callable[[ProbeDocs], F32Array] | None = None


def devices_mask(source_mask: BoolMat, device_of_source: I64Array) -> BoolMat:
    """(質問, 受信箱) の印から，(質問, デバイス) の印（そのデバイスの受信箱のどれかが印なら真）を作る．"""
    n_dev = int(device_of_source.max()) + 1
    out = np.zeros((source_mask.shape[0], n_dev), dtype=bool)
    for dev in range(n_dev):
        out[:, dev] = source_mask[:, device_of_source == dev].any(axis=1)
    return out


def sources_of_devices(device_mask: BoolMat, device_of_source: I64Array) -> BoolMat:
    """(質問, デバイス) の印から，そのデバイスの受信箱全部の (質問, 受信箱) の印を作る．"""
    out: BoolMat = device_mask[:, device_of_source]
    return out


def top_k_mask(scores: F32Array, k: int) -> BoolMat:
    """各行の上位 k 列の印（同点は列の番号の小さい方を先にする）．"""
    order = np.argsort(-scores, axis=1, kind="stable")[:, :k]
    mask = np.zeros(scores.shape, dtype=bool)
    np.put_along_axis(mask, order, True, axis=1)
    return mask


def pessimistic_ranks(scores: F32Array) -> I64Array:
    """各列の順位（その列より得点が高いか同点の列の数．自分を含む．同点は不利な側に数える）．"""
    out = np.empty(scores.shape, dtype=np.int64)
    for start in range(0, scores.shape[0], RANK_CHUNK):
        chunk = scores[start : start + RANK_CHUNK]
        out[start : start + RANK_CHUNK] = (chunk[:, None, :] >= chunk[:, :, None]).sum(axis=2)
    return out


@dataclass(frozen=True)
class PerQuery:
    """1 つの設定・1 つの split の，質問ごとの結果．"""

    hit1: F64Array
    hit_m: F64Array
    recall_m: F64Array
    requests: F64Array
    round_trips: F64Array


def evaluate_selection(data: FrontierData, sel: Selection, split: str) -> PerQuery:
    """選択を，質問ごとの hit・再現率・要求数・往復の回数にする．"""
    m = data.m
    if sel.candidates is None:
        if sel.direct is None:
            raise ValueError("a selection needs candidates or direct scores")
        scores = sel.direct
        probed = np.zeros(scores.shape[0])
        base_trips = 1.0
    else:
        scores = np.where(sel.candidates, data.second[split], -np.inf).astype(np.float32)
        probed = devices_mask(sel.candidates, data.device_of_source).sum(axis=1).astype(np.float64)
        base_trips = 2.0
    n_q = scores.shape[0]
    extra = np.zeros(n_q) if sel.extra_requests is None else sel.extra_requests
    extra_trips = np.zeros(n_q) if sel.extra_round_trips is None else sel.extra_round_trips
    ranks = pessimistic_ranks(scores)
    finite = np.isfinite(scores)
    rel = data.relevant[split]
    n_rel = np.maximum(1, rel.sum(axis=1))
    in_m = finite & (ranks <= m) & rel
    return PerQuery(
        hit1=np.asarray((finite & (ranks <= 1) & rel).any(axis=1), dtype=np.float64),
        hit_m=np.asarray(in_m.any(axis=1), dtype=np.float64),
        recall_m=np.asarray(in_m.sum(axis=1) / n_rel, dtype=np.float64),
        requests=probed + extra + m,
        round_trips=base_trips + extra_trips,
    )


def attribution_scores(data: FrontierData, cfg: Config, probes: ProbeDocs | None) -> dict[str, Any]:
    """公開情報だけから，正解でないメールの出どころのデバイスを当てる（top-1 の正解率と AUC．§7.1）．測らない方式は None．"""
    if cfg.attribute is None or probes is None:
        return {"attribution_top1": None, "attribution_auc": None}
    scores = cfg.attribute(probes)
    owner = data.device_of_source[probes.owner_source]
    return {
        "attribution_top1": float(np.mean(np.argmax(scores, axis=1) == owner)),
        "attribution_auc": owner_auc(scores, owner.tolist()),
    }


def summarize(values: Sequence[float] | F64Array) -> dict[str, float]:
    """平均と p95 と最大．"""
    arr = np.asarray(values, dtype=np.float64)
    return {
        "mean": float(arr.mean()),
        "p95": float(np.percentile(arr, REQUEST_PERCENTILE)),
        "max": float(arr.max()),
    }


def make_row(
    data: FrontierData,
    cfg: Config,
    split: str,
    per_query: PerQuery,
    probes: ProbeDocs | None,
    with_ci: bool,
) -> list[dict[str, Any]]:
    """frontier.json の行（subset ごとに 1 行）．"""
    base: dict[str, Any] = {
        "dataset": data.dataset,
        "placement": data.placement,
        "family": cfg.family,
        "method": cfg.method,
        "params": cfg.params,
        "split": split,
        "advert_bytes": cfg.advert_bytes,
        **(attribution_scores(data, cfg, probes) if split == "test" else {}),
    }
    rows = []
    for subset, mask in {"all": np.ones(len(per_query.hit1), bool), **data.subsets[split]}.items():
        if not mask.any():
            continue
        row = {
            **base,
            "subset": subset,
            "n": int(mask.sum()),
            "requests_per_query": summarize(per_query.requests[mask]),
            "round_trips": {
                "mean": float(per_query.round_trips[mask].mean()),
                "max": float(per_query.round_trips[mask].max()),
            },
            "hit@1": float(per_query.hit1[mask].mean()),
            f"hit@{data.m}": float(per_query.hit_m[mask].mean()),
            f"source_recall@{data.m}": float(per_query.recall_m[mask].mean()),
        }
        if with_ci:
            row["hit@1_ci95"] = bootstrap_ci(per_query.hit1[mask], data.seed)
            row[f"hit@{data.m}_ci95"] = bootstrap_ci(per_query.hit_m[mask], data.seed)
        rows.append(row)
    return rows
