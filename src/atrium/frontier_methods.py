"""frontier-eval の方式のレジストリ（p0006 P2）．方式を足すときは，`FrontierData` から設定の一覧を返す関数を書き，`BUILDERS` に登録する．

- 既存の基準（ヘッダー込みの重心．p0006 §6.3 の既存の点の再現用）：flood_score・multi_centroid・centroid_probe・device_probe．
- F1a sample_summary：各デバイスが本文の埋め込みを S 個公開する（ReDDE・CSI 型の資源選択）．
- F1b term_stats：各デバイスが本文の語の文書頻度の上位 V 語を公開する（CORI 型）．
- F2 adaptive_cutoff：1 段目の得点の分布から，問ごとに probe の台数を決める（Rank-S・Taily 型の打ち切り）．
- F3 hierarchy：デバイスを g 個のグループに分け，グループの代表が束ねた要約で 2 段に選ぶ（DNS 型）．
- F4 overlay_walk：各デバイスは近傍 d 台の要約だけを持ち，質問を転送する（DRAG 型）．

要約（F1〜F4）は p0006 §3.2 により，文書の本文（ヘッダーを除く）から作る埋め込み・語の統計だけを使い，メールの付属情報は使わない．
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np

from atrium.arrays import F32Array, F64Array, I64Array
from atrium.frontier_core import (
    BoolMat,
    Config,
    FrontierData,
    ProbeDocs,
    Selection,
    sources_of_devices,
    top_k_mask,
)

logger = logging.getLogger(__name__)

# 本文の要約の重心の数（config.yaml の data.enronqa.n_centroids と同じ 32）
BODY_CENTROIDS = 32
# 受信箱ごとの出どころの特定に使うメールの数の上限（config の data.enronqa.owner_probe_per_inbox と同じ 10）
PROBE_PER_SOURCE = 10
# 標本の数の候補（F1a）・語の数の候補（F1b）
SAMPLE_SIZES = (4, 8, 16, 32, 64)
TERM_SIZES = (1000, 4000, 16000)
# 公開した標本の先頭 MAX_SAMPLE 個は，出どころの特定の対象から除く（標本そのものを当てさせない）
MAX_SAMPLE = max(SAMPLE_SIZES)
# ReDDE・Rank-S 型の票：上位 VOTE_TOP 個の標本が，順位 r で VOTE_BASE^(-r) 票を持ち主のデバイスへ入れる
VOTE_TOP = 20
VOTE_BASE = 2.0
# CORI の定数（Callan 2000 の既定：デフォルトの信念 0.4，df の平滑 50・150）
CORI_DEFAULT_BELIEF = 0.4
CORI_DF_BASE = 50.0
CORI_DF_SCALE = 150.0
MIN_TERM_LENGTH = 3
# 反復 2：語の数をさらに増やした term_stats と，語の統計と重心の併用（hybrid_summary）
LARGE_TERM_SIZE = 64000
HYBRID_TERM_SIZES = (16000, 64000)
HYBRID_WEIGHTS = (0.5, 1.0, 2.0)
# 質問ごと・デバイス間の標準化で，得点が一定のときに 0 割りしないための下限
Z_EPSILON = 1e-9
# 打ち切り（F2）の格子
GAP_DELTAS = (0.01, 0.02, 0.03, 0.05, 0.08)
SOFTMAX_TAUS = (0.01, 0.02, 0.05)
SOFTMAX_MASSES = (0.5, 0.7, 0.9)
ADAPTIVE_DMAX = (8, 16)
# 階層（F3）の格子
GROUP_FACTORS = (0.5, 1.0, 1.5, 2.0)
TOP_GROUPS = (1, 2, 3)
GROUP_DEVICES = (2, 4, 6, 8)
# 転送（F4）の格子
OVERLAY_DEGREES = (3, 5, 8)
OVERLAY_TTLS = (2, 4, 8, 12)
OVERLAY_WALKERS = (1, 3)
OVERLAY_SOFTMAX_TAU = 0.02
# 既存の基準の格子（p0006 §6.3 の既存の点を含む）
CENTROID_PROBE_KS = (2, 4, 8, 15, 30, 75)
DEVICE_PROBE_DS = (12, 24, 30)
D_GRID = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96)
FLOAT32_BYTES = 4
# 語の統計の 1 組（語の番号と文書頻度）を float32 の 2 個として数える
TERM_ENTRY_BYTES = 8


# ── 共通の部品 ────────────────────────────────────────────────────────────


def d_grid(n_dev: int) -> list[int]:
    """probe するデバイスの数 D の格子（デバイスの数まで）．"""
    return [d for d in D_GRID if d < n_dev] + [n_dev]


def aggregate_max(scores: F32Array, device_of_source: I64Array) -> F32Array:
    """(質問, 受信箱) の得点を，デバイスごとの最大にした (質問, デバイス) の行列．"""
    n_dev = int(device_of_source.max()) + 1
    return np.stack(
        [scores[:, device_of_source == dev].max(axis=1) for dev in range(n_dev)], axis=1
    ).astype(np.float32)


def multi_scores(queries: F32Array, centers: list[F32Array]) -> F32Array:
    """(質問, 受信箱) の得点 = 受信箱の中心との内積の最大（multi_centroid）．"""
    return np.stack([(queries @ c.T).max(axis=1) for c in centers], axis=1).astype(np.float32)


def source_centers(data: FrontierData, key: str) -> list[F32Array]:
    """受信箱ごとの重心．full はヘッダー込み（既存），body は本文の埋め込みの k-means．"""
    cache_key = f"centers_{key}"
    if cache_key not in data.cache:
        if key == "full":
            if data.full_centers is None:
                raise ValueError("this dataset has no full-header centroids")
            data.cache[cache_key] = data.full_centers
        else:
            from atrium.data_enronqa import kmeans_centroids

            if data.summary_emb is None:
                raise ValueError("this dataset has no body embeddings")
            data.cache[cache_key] = [
                kmeans_centroids(e, BODY_CENTROIDS, data.seed) for e in data.summary_emb
            ]
    centers: list[F32Array] = data.cache[cache_key]
    return centers


def first_stage(data: FrontierData, split: str, key: str) -> F32Array:
    """(質問, 受信箱) の 1 段目の得点（重心との内積の最大）．"""
    cache_key = f"first_{key}_{split}"
    if cache_key not in data.cache:
        data.cache[cache_key] = multi_scores(data.q_emb[split], source_centers(data, key))
    first: F32Array = data.cache[cache_key]
    return first


def device_first_stage(data: FrontierData, split: str, key: str) -> F32Array:
    """(質問, デバイス) の 1 段目の得点（デバイスの受信箱の最大）．"""
    return aggregate_max(first_stage(data, split, key), data.device_of_source)


def device_embeddings(data: FrontierData) -> list[F32Array]:
    """デバイスごとの本文の埋め込み（そのデバイスの受信箱を連結したもの）．"""
    if "device_emb" not in data.cache:
        if data.summary_emb is None:
            raise ValueError("this dataset has no body embeddings")
        data.cache["device_emb"] = [
            np.concatenate(
                [data.summary_emb[s] for s in np.flatnonzero(data.device_of_source == dev)]
            )
            for dev in range(data.n_devices)
        ]
    embs: list[F32Array] = data.cache["device_emb"]
    return embs


def device_order(data: FrontierData) -> list[I64Array]:
    """デバイスごとの文書の並べ替え（標本は先頭から S 個．S が増えても前の標本を含む）．"""
    if "device_order" not in data.cache:
        data.cache["device_order"] = [
            np.random.default_rng([data.seed, dev]).permutation(len(e)).astype(np.int64)
            for dev, e in enumerate(device_embeddings(data))
        ]
    orders: list[I64Array] = data.cache["device_order"]
    return orders


def top_devices(dscores: F32Array, d: int, device_of_source: I64Array) -> BoolMat:
    """(質問, デバイス) の得点の上位 d 台に載る受信箱の印．"""
    return sources_of_devices(top_k_mask(dscores, d), device_of_source)


def reach_selection(mask: BoolMat) -> Selection:
    """probe する受信箱の印だけの選択（追加の要求なし）．"""
    return Selection(candidates=mask)


# ── 既存の基準 ────────────────────────────────────────────────────────────


def build_flood(data: FrontierData) -> list[Config]:
    """flood_score：全デバイスに probe する．"""
    all_mask = np.ones((1, data.n_sources), dtype=bool)

    def select(split: str) -> Selection:
        n_q = data.second[split].shape[0]
        return Selection(candidates=np.repeat(all_mask, n_q, axis=0))

    return [Config("reference", "flood_score", {}, 0, select)]


def build_multi_centroid(data: FrontierData) -> list[Config]:
    """multi_centroid（ヘッダー込み．公開情報だけで選ぶ．probe なし）．"""
    centers = source_centers(data, "full")
    size = sum(c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in centers)

    def select(split: str) -> Selection:
        return Selection(candidates=None, direct=first_stage(data, split, "full"))

    return [Config("reference", "multi_centroid", {"C": BODY_CENTROIDS}, size, select)]


def build_centroid_probe(data: FrontierData) -> list[Config]:
    """centroid_probe：multi_centroid の上位 K 個の受信箱にだけ probe する．"""
    centers = source_centers(data, "full")
    size = sum(c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in centers)
    configs = []
    for k in CENTROID_PROBE_KS:
        if k >= data.n_sources:
            continue

        def select(split: str, k: int = k) -> Selection:
            return reach_selection(top_k_mask(first_stage(data, split, "full"), k))

        configs.append(Config("reference", "centroid_probe", {"K": k}, size, select))
    return configs


def build_device_probe(data: FrontierData) -> list[Config]:
    """device_probe：multi_centroid の得点をデバイスごとの最大にし，上位 D 台に probe する．"""
    centers = source_centers(data, "full")
    size = sum(c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in centers)
    configs = []
    for d in sorted({*DEVICE_PROBE_DS, *d_grid(data.n_devices)}):
        if d > data.n_devices:
            continue

        def select(split: str, d: int = d) -> Selection:
            return reach_selection(
                top_devices(device_first_stage(data, split, "full"), d, data.device_of_source)
            )

        configs.append(Config("reference", "device_probe", {"D": d}, size, select))
    return configs


def build_multi_centroid_body(data: FrontierData) -> list[Config]:
    """本文だけから作った multi_centroid（直接選ぶ版と，device_probe の 1 段目にする版の基準）．"""
    centers = source_centers(data, "body")
    size = sum(c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in centers)
    # 出どころの特定：メールの埋め込みと各受信箱の重心との内積の最大を，デバイスごとの最大にまとめる
    device_of_source = data.device_of_source

    def attribute(probes: ProbeDocs) -> F32Array:
        return aggregate_max(multi_scores(probes.emb, centers), device_of_source)

    def direct(split: str) -> Selection:
        return Selection(candidates=None, direct=first_stage(data, split, "body"))

    configs = [
        Config(
            "body_baseline", "multi_centroid_body", {"C": BODY_CENTROIDS}, size, direct, attribute
        )
    ]
    for d in d_grid(data.n_devices):

        def select(split: str, d: int = d) -> Selection:
            return reach_selection(
                top_devices(device_first_stage(data, split, "body"), d, data.device_of_source)
            )

        configs.append(
            Config("body_baseline", "device_probe_body", {"D": d}, size, select, attribute)
        )
    return configs


# ── F1a 標本による要約（ReDDE・CSI 型）─────────────────────────────────────


def samples_of(data: FrontierData, size: int) -> tuple[F32Array, I64Array]:
    """各デバイスが公開する標本（先頭 size 個）の埋め込みと，持ち主のデバイスの番号（デバイスごとに連続）．"""
    embs = device_embeddings(data)
    order = device_order(data)
    parts = [embs[dev][order[dev][:size]] for dev in range(data.n_devices)]
    owner = np.concatenate([np.full(len(p), dev, dtype=np.int64) for dev, p in enumerate(parts)])
    return np.concatenate(parts).astype(np.float32), owner


def sample_device_scores(
    queries: F32Array, samples: F32Array, owner: I64Array, n_dev: int, scorer: str
) -> F32Array:
    """質問と標本の内積から，デバイスの得点を作る．max は標本の最大，vote は上位の標本の順位による票（Rank-S 型）．"""
    sims = queries @ samples.T
    starts = np.flatnonzero(np.r_[True, owner[1:] != owner[:-1]])
    best = np.maximum.reduceat(sims, starts, axis=1).astype(np.float32)
    if scorer == "max":
        return best
    top = np.argsort(-sims, axis=1, kind="stable")[:, :VOTE_TOP]
    weights = VOTE_BASE ** -np.arange(top.shape[1], dtype=np.float64)
    votes = np.zeros((queries.shape[0], n_dev))
    rows = np.repeat(np.arange(queries.shape[0]), top.shape[1])
    np.add.at(votes, (rows, owner[top].ravel()), np.tile(weights, queries.shape[0]))
    # 票の無いデバイスの並びを，標本の最大で決める（同点の並びが番号になって結果が偏らないように）
    return (votes + 1e-6 * best).astype(np.float32)


def build_sample_summary(data: FrontierData) -> list[Config]:
    """F1a：標本の数 S × 得点（max・vote）× probe するデバイスの数 D．"""
    configs = []
    for size in SAMPLE_SIZES:
        samples, owner = samples_of(data, size)
        advert = len(samples) * samples.shape[1] * FLOAT32_BYTES
        for scorer in ("max", "vote"):

            def dscores(
                split: str,
                samples: F32Array = samples,
                owner: I64Array = owner,
                scorer: str = scorer,
                size: int = size,
            ) -> F32Array:
                key = f"samp_{size}_{scorer}_{split}"
                if key not in data.cache:
                    data.cache[key] = sample_device_scores(
                        data.q_emb[split], samples, owner, data.n_devices, scorer
                    )
                out: F32Array = data.cache[key]
                return out

            def attribute(
                probes: ProbeDocs, samples: F32Array = samples, owner: I64Array = owner
            ) -> F32Array:
                sims = probes.emb @ samples.T
                starts = np.flatnonzero(np.r_[True, owner[1:] != owner[:-1]])
                return np.maximum.reduceat(sims, starts, axis=1).astype(np.float32)

            for d in d_grid(data.n_devices):

                def select(
                    split: str, d: int = d, dscores: Callable[[str], F32Array] = dscores
                ) -> Selection:
                    return reach_selection(top_devices(dscores(split), d, data.device_of_source))

                configs.append(
                    Config(
                        "sample_summary",
                        "sample_summary",
                        {"S": size, "scorer": scorer, "D": d},
                        advert,
                        select,
                        attribute,
                    )
                )
    return configs


# ── F1b 語の統計による要約（CORI 型）───────────────────────────────────────


def filter_terms(tokens: list[str]) -> list[str]:
    """要約と質問に使う語（短い語・数字だけの語・英語の機能語を除く．atrium.data_enronqa.term_sketch と同じ）．"""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    return [
        t
        for t in tokens
        if len(t) >= MIN_TERM_LENGTH and not t.isdigit() and t not in ENGLISH_STOP_WORDS
    ]


def term_index(data: FrontierData) -> dict[str, Any]:
    """文書 × 語の 2 値行列（疎）と，文書の持ち主のデバイス・文書の語数・語彙を作る．"""
    if "term_index" in data.cache:
        cached: dict[str, Any] = data.cache["term_index"]
        return cached
    from scipy.sparse import csr_matrix

    if data.summary_tokens is None:
        raise ValueError("this dataset has no body tokens")
    vocab: dict[str, int] = {}
    rows: list[int] = []
    cols: list[int] = []
    owner: list[int] = []
    lengths: list[int] = []
    doc = 0
    for s, docs in enumerate(data.summary_tokens):
        for toks in docs:
            terms = filter_terms(toks)
            owner.append(int(data.device_of_source[s]))
            lengths.append(len(terms))
            for t in set(terms):
                rows.append(doc)
                cols.append(vocab.setdefault(t, len(vocab)))
            doc += 1
    matrix = csr_matrix(
        (np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(doc, len(vocab))
    )
    out = {
        "matrix": matrix,
        "vocab": vocab,
        "owner": np.asarray(owner, dtype=np.int64),
        "lengths": np.asarray(lengths, dtype=np.float64),
    }
    data.cache["term_index"] = out
    return out


def cori_weights(data: FrontierData, size: int) -> Any:
    """デバイスごとの，公開した語（文書頻度の上位 size 語）の T×I（CORI の信念の変動分）の疎行列 (デバイス, 語)．"""
    from scipy.sparse import csr_matrix

    idx = term_index(data)
    n_dev = data.n_devices
    owner_ind = csr_matrix(
        (
            np.ones(len(idx["owner"]), dtype=np.float32),
            (idx["owner"], np.arange(len(idx["owner"]))),
        ),
        shape=(n_dev, len(idx["owner"])),
    )
    df = (owner_ind @ idx["matrix"]).tocsr()
    cw = np.asarray(owner_ind @ idx["lengths"]).ravel()
    avg_cw = float(cw.mean())
    published_rows: list[int] = []
    published_cols: list[int] = []
    published_df: list[float] = []
    for dev in range(n_dev):
        row = df.getrow(dev)
        keep = np.argsort(-row.data, kind="stable")[:size]
        published_rows += [dev] * len(keep)
        published_cols += row.indices[keep].tolist()
        published_df += row.data[keep].tolist()
    pub = csr_matrix(
        (published_df, (published_rows, published_cols)), shape=df.shape, dtype=np.float64
    )
    # cf = その語を公開したデバイスの数
    cf = np.asarray((pub > 0).sum(axis=0)).ravel()
    t = pub.copy()
    row_of_entry = np.repeat(np.arange(n_dev), np.diff(pub.indptr))
    t.data = t.data / (t.data + CORI_DF_BASE + CORI_DF_SCALE * cw[row_of_entry] / avg_cw)
    i_weight = np.log((n_dev + 0.5) / np.maximum(cf, 1)) / np.log(n_dev + 1.0)
    weights = t.multiply(i_weight[None, :]).tocsr()
    return weights, cf


def cori_scores(
    vocab: dict[str, int],
    weights: Any,
    cf: np.ndarray[Any, Any],
    token_lists: list[list[str]],
) -> F32Array:
    """質問（または文書）の語から，デバイスの CORI の得点（公開された語の信念の平均）を作る．

    vocab は語 → 語の番号．質問者（atrium.routing.summary）も同じ関数で採点する（オフラインの評価と実機の選択を一致させる）．
    """
    from scipy.sparse import csr_matrix

    rows: list[int] = []
    cols: list[int] = []
    for qi, toks in enumerate(token_lists):
        for t in set(filter_terms(toks)):
            j = vocab.get(t)
            if j is not None and cf[j] > 0:
                rows.append(qi)
                cols.append(j)
    q = csr_matrix(
        (np.ones(len(rows), dtype=np.float64), (rows, cols)),
        shape=(len(token_lists), len(vocab)),
    )
    n_terms = np.asarray(q.sum(axis=1)).ravel()
    mean_var: F64Array = np.asarray((q @ weights.T).todense()) / np.maximum(n_terms, 1)[:, None]
    # 質問に使える語が無ければ，どのデバイスも同点（既定の信念）
    out: F32Array = (CORI_DEFAULT_BELIEF + (1 - CORI_DEFAULT_BELIEF) * mean_var).astype(np.float32)
    return out


def build_term_stats(data: FrontierData) -> list[Config]:
    """F1b：公開する語の数 V × probe するデバイスの数 D（CORI 型の順位づけ）．"""
    configs = []
    for size in TERM_SIZES:
        weights, cf = cori_weights(data, size)
        advert = int(size * TERM_ENTRY_BYTES * data.n_devices)

        def dscores(split: str, size: int = size, weights: Any = weights, cf: Any = cf) -> F32Array:
            key = f"cori_{size}_{split}"
            if key not in data.cache:
                data.cache[key] = cori_scores(
                    term_index(data)["vocab"], weights, cf, data.q_tokens[split]
                )
            out: F32Array = data.cache[key]
            return out

        def attribute(probes: ProbeDocs, weights: Any = weights, cf: Any = cf) -> F32Array:
            return cori_scores(term_index(data)["vocab"], weights, cf, probes.tokens)

        for d in d_grid(data.n_devices):

            def select(
                split: str, d: int = d, dscores: Callable[[str], F32Array] = dscores
            ) -> Selection:
                return reach_selection(top_devices(dscores(split), d, data.device_of_source))

            configs.append(
                Config("term_stats", "term_stats", {"V": size, "D": d}, advert, select, attribute)
            )
    return configs


# ── 反復 2：語の統計の拡張と，語の統計と重心の併用 ────────────────────────────


def build_term_stats_large(data: FrontierData) -> list[Config]:
    """F1b の語の数を V = 64,000 に増やした設定（同じ CORI の順位づけ）．"""
    weights, cf = cori_weights(data, LARGE_TERM_SIZE)
    advert = int(LARGE_TERM_SIZE * TERM_ENTRY_BYTES * data.n_devices)

    def dscores(split: str) -> F32Array:
        key = f"cori_{LARGE_TERM_SIZE}_{split}"
        if key not in data.cache:
            data.cache[key] = cori_scores(
                term_index(data)["vocab"], weights, cf, data.q_tokens[split]
            )
        out: F32Array = data.cache[key]
        return out

    def attribute(probes: ProbeDocs) -> F32Array:
        return cori_scores(term_index(data)["vocab"], weights, cf, probes.tokens)

    configs = []
    for d in d_grid(data.n_devices):

        def select(split: str, d: int = d) -> Selection:
            return reach_selection(top_devices(dscores(split), d, data.device_of_source))

        configs.append(
            Config(
                "term_stats",
                "term_stats",
                {"V": LARGE_TERM_SIZE, "D": d},
                advert,
                select,
                attribute,
            )
        )
    return configs


def zscore_rows(scores: F32Array) -> F32Array:
    """行ごと（質問ごと）にデバイス間で標準化する．"""
    centered = scores - scores.mean(axis=1, keepdims=True)
    out: F32Array = (centered / (scores.std(axis=1, keepdims=True) + Z_EPSILON)).astype(np.float32)
    return out


def build_hybrid_summary(data: FrontierData) -> list[Config]:
    """語の統計（CORI）と本文の重心（multi_centroid_body のデバイス最大）を，質問ごとに標準化して重み付きで足す．"""
    centers = source_centers(data, "body")
    centroid_bytes = sum(c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in centers)
    device_of_source = data.device_of_source
    configs = []
    for size in HYBRID_TERM_SIZES:
        weights, cf = cori_weights(data, size)
        advert = int(size * TERM_ENTRY_BYTES * data.n_devices) + centroid_bytes
        for w in HYBRID_WEIGHTS:

            def dscores(
                split: str, size: int = size, w: float = w, weights: Any = weights, cf: Any = cf
            ) -> F32Array:
                key = f"cori_{size}_{split}"
                if key not in data.cache:
                    data.cache[key] = cori_scores(
                        term_index(data)["vocab"], weights, cf, data.q_tokens[split]
                    )
                fused: F32Array = zscore_rows(data.cache[key]) + w * zscore_rows(
                    device_first_stage(data, split, "body")
                )
                return fused

            def attribute(
                probes: ProbeDocs, w: float = w, weights: Any = weights, cf: Any = cf
            ) -> F32Array:
                terms = zscore_rows(
                    cori_scores(term_index(data)["vocab"], weights, cf, probes.tokens)
                )
                emb = zscore_rows(
                    aggregate_max(multi_scores(probes.emb, centers), device_of_source)
                )
                fused: F32Array = terms + w * emb
                return fused

            for d in d_grid(data.n_devices):

                def select(
                    split: str, d: int = d, dscores: Callable[[str], F32Array] = dscores
                ) -> Selection:
                    return reach_selection(top_devices(dscores(split), d, data.device_of_source))

                configs.append(
                    Config(
                        "hybrid_summary",
                        "hybrid_summary",
                        {"V": size, "w": w, "D": d},
                        advert,
                        select,
                        attribute,
                    )
                )
    return configs


# ── F2 問ごとに台数を変える（Rank-S・Taily 型の打ち切り）─────────────────────


def adaptive_device_mask(
    dscores: F32Array, rule: str, param: float, dmax: int, tau: float = 0.0
) -> BoolMat:
    """(質問, デバイス) の得点から，問ごとに probe するデバイスの印を作る（最低 1 台，最大 dmax 台）．

    score_gap：1 位の得点との差が param 以内のデバイス．softmax_mass：softmax(得点 / tau) の質量の累積が param に届くまで．
    """
    order = np.argsort(-dscores, axis=1, kind="stable")[:, :dmax]
    sorted_scores = np.take_along_axis(dscores, order, axis=1).astype(np.float64)
    if rule == "score_gap":
        keep = sorted_scores >= sorted_scores[:, :1] - param
    else:
        z = np.exp((dscores - dscores.max(axis=1, keepdims=True)) / tau)
        probs = np.take_along_axis(z / z.sum(axis=1, keepdims=True), order, axis=1)
        before = np.cumsum(probs, axis=1) - probs
        keep = before < param
    keep[:, 0] = True
    mask = np.zeros(dscores.shape, dtype=bool)
    rows = np.repeat(np.arange(dscores.shape[0]), order.shape[1])
    mask[rows[keep.ravel()], order[keep]] = True
    return mask


def build_adaptive_cutoff(data: FrontierData) -> list[Config]:
    """F2：1 段目（本文の multi_centroid，標本 S = 64 の max）の上に打ち切りの規則を載せる．"""
    samples, owner = samples_of(data, MAX_SAMPLE)
    samp_advert = len(samples) * samples.shape[1] * FLOAT32_BYTES
    centers = source_centers(data, "body")
    mc_advert = sum(c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in centers)

    def mc(split: str) -> F32Array:
        return device_first_stage(data, split, "body")

    def sm(split: str) -> F32Array:
        key = f"samp_{MAX_SAMPLE}_max_{split}"
        if key not in data.cache:
            data.cache[key] = sample_device_scores(
                data.q_emb[split], samples, owner, data.n_devices, "max"
            )
        out: F32Array = data.cache[key]
        return out

    configs = []
    for base, scores_of, advert in (
        ("multi_centroid_body", mc, mc_advert),
        (f"sample_{MAX_SAMPLE}_max", sm, samp_advert),
    ):
        rules: list[tuple[str, float, float]] = [("score_gap", g, 0.0) for g in GAP_DELTAS] + [
            ("softmax_mass", p, tau) for tau in SOFTMAX_TAUS for p in SOFTMAX_MASSES
        ]
        for dmax in ADAPTIVE_DMAX:
            if dmax > data.n_devices:
                continue
            for rule, param, tau in rules:

                def select(
                    split: str,
                    scores_of: Callable[[str], F32Array] = scores_of,
                    rule: str = rule,
                    param: float = param,
                    tau: float = tau,
                    dmax: int = dmax,
                ) -> Selection:
                    mask = adaptive_device_mask(scores_of(split), rule, param, dmax, tau)
                    return reach_selection(sources_of_devices(mask, data.device_of_source))

                params: dict[str, Any] = {"base": base, "rule": rule, "param": param, "Dmax": dmax}
                if rule == "softmax_mass":
                    params["tau"] = tau
                configs.append(Config("adaptive_cutoff", "adaptive_cutoff", params, advert, select))
    return configs


# ── F3 階層化（DNS 型）─────────────────────────────────────────────────────


def make_groups(data: FrontierData, g: int, mode: str) -> I64Array:
    """デバイスを g 個のグループに分ける（random：ほぼ等しい大きさ．similarity：デバイスの平均埋め込みの k-means）．"""
    n_dev = data.n_devices
    if mode == "random":
        perm = np.random.default_rng([data.seed, g]).permutation(n_dev)
        groups = np.empty(n_dev, dtype=np.int64)
        groups[perm] = np.arange(n_dev) % g
        return groups
    import faiss

    means = np.stack([e.mean(axis=0) for e in device_embeddings(data)]).astype(np.float32)
    km = faiss.Kmeans(means.shape[1], g, niter=20, seed=data.seed)
    km.train(np.ascontiguousarray(means))
    assignment = km.index.search(np.ascontiguousarray(means), 1)[1].ravel().astype(np.int64)
    _, relabeled = np.unique(assignment, return_inverse=True)
    return np.asarray(relabeled, dtype=np.int64)


def build_hierarchy(data: FrontierData) -> list[Config]:
    """F3：質問者はグループの要約（グループの文書の k-means 重心）だけを持ち，選んだグループの代表が，所属デバイスの要約で順位づけして返す．

    要求数 = 代表への問い合わせ（G'）+ probe（D2）+ m．往復は 代表 → probe → 本照会 の 3 回．
    """
    from atrium.data_enronqa import kmeans_centroids

    embs = device_embeddings(data)
    configs = []
    for factor in GROUP_FACTORS:
        g = max(2, round(float(np.sqrt(data.n_devices)) * factor))
        if g >= data.n_devices:
            continue
        for mode in ("random", "similarity"):
            groups = make_groups(data, g, mode)
            n_groups = int(groups.max()) + 1
            centers = [
                kmeans_centroids(
                    np.concatenate([embs[dev] for dev in np.flatnonzero(groups == gi)]),
                    BODY_CENTROIDS,
                    data.seed,
                )
                for gi in range(n_groups)
            ]
            advert = sum(c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in centers)
            member = np.zeros((n_groups, data.n_devices), dtype=bool)
            member[groups, np.arange(data.n_devices)] = True
            for top_g in TOP_GROUPS:
                if top_g > n_groups:
                    continue
                for d2 in GROUP_DEVICES:

                    def select(
                        split: str,
                        centers: list[F32Array] = centers,
                        member: BoolMat = member,
                        top_g: int = top_g,
                        d2: int = d2,
                    ) -> Selection:
                        gscores = multi_scores(data.q_emb[split], centers)
                        chosen = top_k_mask(gscores, top_g)
                        allowed = (chosen.astype(np.int32) @ member.astype(np.int32)) > 0
                        dscores = np.where(
                            allowed, device_first_stage(data, split, "body"), -np.inf
                        ).astype(np.float32)
                        picked = top_k_mask(dscores, d2) & allowed
                        n_q = picked.shape[0]
                        return Selection(
                            candidates=sources_of_devices(picked, data.device_of_source),
                            extra_requests=np.full(n_q, float(top_g)),
                            extra_round_trips=np.ones(n_q),
                        )

                    configs.append(
                        Config(
                            "hierarchy",
                            "hierarchy",
                            {"g": g, "grouping": mode, "G": top_g, "D2": d2},
                            advert,
                            select,
                        )
                    )
    return configs


# ── F4 近傍だけを知る転送（DRAG 型）────────────────────────────────────────


def neighbor_table(n_dev: int, degree: int, seed: int) -> I64Array:
    """各デバイスの近傍（自分以外の無作為な degree 台．有向）．"""
    rng = np.random.default_rng([seed, n_dev, degree])
    table = np.empty((n_dev, degree), dtype=np.int64)
    for dev in range(n_dev):
        others = np.delete(np.arange(n_dev), dev)
        table[dev] = rng.choice(others, size=min(degree, len(others)), replace=False)
    return table


def walk_visited(
    dscores: F32Array, neighbors: I64Array, ttl: int, walkers: int, mode: str, seed: int
) -> tuple[BoolMat, I64Array]:
    """問ごとに，歩き手が訪れたデバイスの印と，最も長く歩いた歩き手の歩数．

    歩き手は無作為な入口のデバイスから始め，近傍の要約の得点（その質問に対するデバイスの得点）を見て，まだ訪れていない近傍へ進む．
    greedy は最高の近傍，tarw は得点の softmax で選ぶ（話題を考慮した無作為な歩き）．近傍が全て訪問済みなら止まる．
    """
    rng = np.random.default_rng(seed)
    n_q, n_dev = dscores.shape
    rows = np.arange(n_q)
    visited = np.zeros((n_q, n_dev), dtype=bool)
    longest: I64Array = np.zeros(n_q, dtype=np.int64)
    for _ in range(walkers):
        cur = rng.integers(0, n_dev, size=n_q)
        visited[rows, cur] = True
        alive = np.ones(n_q, dtype=bool)
        for step in range(1, ttl + 1):
            nb = neighbors[cur]
            sc = dscores[rows[:, None], nb].astype(np.float64)
            sc = np.where(visited[rows[:, None], nb], -np.inf, sc)
            movable = alive & np.isfinite(sc).any(axis=1)
            if mode == "greedy":
                pick = np.argmax(sc, axis=1)
            else:
                gumbel = -np.log(-np.log(rng.random(sc.shape)))
                pick = np.argmax(sc / OVERLAY_SOFTMAX_TAU + gumbel, axis=1)
            nxt = nb[rows, pick]
            visited[rows[movable], nxt[movable]] = True
            cur = np.asarray(np.where(movable, nxt, cur), dtype=np.int64)
            longest = np.asarray(
                np.where(movable, np.maximum(longest, step), longest), dtype=np.int64
            )
            alive = movable
    return visited, longest


def build_overlay_walk(data: FrontierData) -> list[Config]:
    """F4：近傍の数 d × 寿命 TTL × 歩き手の数 × 転送の規則（greedy・tarw）．近傍の要約は本文の multi_centroid のデバイスの得点．"""
    centers = source_centers(data, "body")
    advert_per_device = (
        sum(c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in centers) // data.n_devices
    )
    configs = []
    for degree in OVERLAY_DEGREES:
        if degree >= data.n_devices:
            continue
        table = neighbor_table(data.n_devices, degree, data.seed)
        for ttl in OVERLAY_TTLS:
            for walkers in OVERLAY_WALKERS:
                for mode in ("greedy", "tarw"):

                    def select(
                        split: str,
                        table: I64Array = table,
                        ttl: int = ttl,
                        walkers: int = walkers,
                        mode: str = mode,
                        degree: int = degree,
                    ) -> Selection:
                        dscores = device_first_stage(data, split, "body")
                        visited, longest = walk_visited(
                            dscores, table, ttl, walkers, mode, data.seed + degree * 1000 + ttl
                        )
                        return Selection(
                            candidates=sources_of_devices(visited, data.device_of_source),
                            extra_round_trips=longest.astype(np.float64),
                        )

                    configs.append(
                        Config(
                            "overlay_walk",
                            "overlay_walk",
                            {"d": degree, "ttl": ttl, "walkers": walkers, "rule": mode},
                            advert_per_device * degree,
                            select,
                        )
                    )
    return configs


BUILDERS: dict[str, Callable[[FrontierData], list[Config]]] = {
    "flood_score": build_flood,
    "multi_centroid": build_multi_centroid,
    "centroid_probe": build_centroid_probe,
    "device_probe": build_device_probe,
    "body_baseline": build_multi_centroid_body,
    "sample_summary": build_sample_summary,
    "term_stats": build_term_stats,
    "term_stats_large": build_term_stats_large,
    "hybrid_summary": build_hybrid_summary,
    "adaptive_cutoff": build_adaptive_cutoff,
    "hierarchy": build_hierarchy,
    "overlay_walk": build_overlay_walk,
}
# R4（既存の点の再現）に使う方式
REFERENCE_METHODS = ("flood_score", "multi_centroid", "centroid_probe", "device_probe")


def probe_docs(data: FrontierData) -> ProbeDocs | None:
    """出どころの特定に使う，正解でないメール（各受信箱から，正解のメールと公開した標本を除いて最大 10 通）．"""
    if "probe_docs" in data.cache:
        cached: ProbeDocs | None = data.cache["probe_docs"]
        return cached
    if data.summary_emb is None or data.summary_tokens is None:
        return None
    order = device_order(data)
    sampled: list[set[int]] = [set() for _ in data.names]
    for dev in range(data.n_devices):
        srcs = np.flatnonzero(data.device_of_source == dev)
        ends = np.cumsum([len(data.summary_emb[s]) for s in srcs])
        for pos in order[dev][:MAX_SAMPLE]:
            k = int(np.searchsorted(ends, pos, side="right"))
            sampled[int(srcs[k])].add(int(pos - (ends[k - 1] if k else 0)))
    rng = np.random.default_rng(data.seed)
    embs: list[F32Array] = []
    toks: list[list[str]] = []
    owners: list[int] = []
    for s in range(data.n_sources):
        banned = sampled[s] | (data.exclude_docs[s] if data.exclude_docs else set())
        pool = [i for i in range(len(data.summary_emb[s])) if i not in banned]
        for i in sorted(rng.permutation(len(pool))[: min(PROBE_PER_SOURCE, len(pool))]):
            embs.append(data.summary_emb[s][pool[int(i)]])
            toks.append(data.summary_tokens[s][pool[int(i)]])
            owners.append(s)
    out = ProbeDocs(np.stack(embs), toks, np.asarray(owners, dtype=np.int64))
    data.cache["probe_docs"] = out
    return out


def build_all(data: FrontierData, names: list[str]) -> list[Config]:
    """名前の順に方式を作る（未知の名前は ValueError）．"""
    unknown = [n for n in names if n not in BUILDERS]
    if unknown:
        raise ValueError(f"unknown methods {unknown}; available: {sorted(BUILDERS)}")
    configs: list[Config] = []
    for name in names:
        built = BUILDERS[name](data)
        logger.info("%s: %d configs", name, len(built))
        configs += built
    return configs
