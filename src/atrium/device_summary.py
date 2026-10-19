"""p0007 I7：デバイスが自分のメールだけから公開情報（hybrid_probe の要約）を作る部分と，質問者が集めた公開情報から要約の成果物を組み立てる部分．

`atrium.routing.summary`（hybrid_probe・term_probe の質問者）は，`atrium build-summary` が全受信箱から一括して作った成果物（`<data_dir>/enronqa/summary/`）を読む．
途中で加わるデバイスを実機で扱うため，同じ要約を次の 2 段に分ける（出力は同じ定義）．

1. `summarize_device`（デバイスの CPU で動く．ノードの `/v1/admin/summary`）：そのデバイスの受信箱の本文だけから，語の文書頻度の上位 term_size 語・
   フィルタ後の語数の合計（CORI の長さ）・受信箱ごとの本文の重心（k-means）を作る．本文は区切り線より後（ヘッダーは使わない．p0007 §3.3）で，
   本文が空のメールは除く．埋め込みはコーパスと同じ符号化器の本文だけの埋め込み（fp16 に丸める．`body_advert_eval.embed_bodies` と同じ）．
2. `assemble_artifact`（質問者）：全デバイスの公開情報から，CORI の重み（IDF 相当の cf・長さの平均がデバイスの集合に依る）を計算し直して
   `SummaryArtifact` を作る．`frontier_methods.cori_weights` と同じ式なので，同じ公開情報なら同じ成果物になる（tests/test_device_summary.py）．
   デバイスが加わると cf・デバイス数が変わるので，既存のデバイスの重みも作り直す（公開情報そのものは再送しない）．
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from atrium.arrays import F32Array
from atrium.data_enronqa import HEADER_SEPARATOR, MIN_TERM_LENGTH, kmeans_centroids
from atrium.routing.advert import tokenize
from atrium.routing.summary import SummaryArtifact

# frontier_methods の CORI の定数と同じ（tests/test_device_summary.py が一致を確かめる）
CORI_DF_BASE = 50.0
CORI_DF_SCALE = 150.0
# 本文の重心の数（frontier_methods.BODY_CENTROIDS と同じ）
BODY_CENTROIDS = 32

Embed = Callable[[Sequence[str]], F32Array]


@dataclass(frozen=True)
class DeviceSummary:
    """デバイスが公開する要約と，作るのにかかった時間（秒）．"""

    terms: list[str]
    dfs: list[int]
    # フィルタ後の語数の合計（CORI の長さ cw）と，要約に使ったメールの数（本文が空でないもの）
    total_terms: int
    n_docs: int
    # 受信箱 → 本文の重心（行が重心）
    centers: dict[str, F32Array]
    tokenize_s: float
    embed_s: float
    kmeans_s: float


def filter_terms(tokens: Sequence[str]) -> list[str]:
    """要約と質問に使う語（短い語・数字だけの語・英語の機能語を除く．`frontier_methods.filter_terms` と同じ）．"""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    return [
        t
        for t in tokens
        if len(t) >= MIN_TERM_LENGTH and not t.isdigit() and t not in ENGLISH_STOP_WORDS
    ]


def body_of(content: str) -> str:
    """メールの本文（区切り線より後．区切り線が無ければ全体）．"""
    head, sep, body = content.partition(HEADER_SEPARATOR)
    return body if sep else head


def summarize_device(
    inboxes: Mapping[str, Sequence[str]],
    embed: Embed,
    term_size: int,
    n_centroids: int,
    seed: int,
) -> DeviceSummary:
    """デバイスの受信箱（受信箱 → メールの content の一覧）から公開情報を作る．

    語の統計はデバイス全体（受信箱をまとめた）の文書頻度の上位 term_size 語，重心は受信箱ごと（本文が空でないメールの埋め込みの k-means）．
    """
    start = time.perf_counter()
    df: Counter[str] = Counter()
    total_terms = 0
    n_docs = 0
    bodies_of: dict[str, list[str]] = {}
    for inbox, contents in inboxes.items():
        bodies = [b for b in (body_of(c) for c in contents) if b.strip()]
        bodies_of[inbox] = bodies
        for body in bodies:
            terms = filter_terms(tokenize(body))
            total_terms += len(terms)
            df.update(set(terms))
            n_docs += 1
    top = sorted(df.items(), key=lambda kv: (-kv[1], kv[0]))[:term_size]
    tokenize_s = time.perf_counter() - start

    start = time.perf_counter()
    flat = [b for inbox in inboxes for b in bodies_of[inbox]]
    emb = (
        embed(flat).astype(np.float16).astype(np.float32) if flat else np.zeros((0, 0), np.float32)
    )
    embed_s = time.perf_counter() - start

    start = time.perf_counter()
    centers: dict[str, F32Array] = {}
    offset = 0
    for inbox in inboxes:
        n = len(bodies_of[inbox])
        centers[inbox] = kmeans_centroids(emb[offset : offset + n], n_centroids, seed)
        offset += n
    kmeans_s = time.perf_counter() - start
    return DeviceSummary(
        terms=[t for t, _ in top],
        dfs=[c for _, c in top],
        total_terms=total_terms,
        n_docs=n_docs,
        centers=centers,
        tokenize_s=tokenize_s,
        embed_s=embed_s,
        kmeans_s=kmeans_s,
    )


def assemble_artifact(
    names: Sequence[str],
    device_of_source: Mapping[str, str],
    summaries: Mapping[str, DeviceSummary],
    term_size: int,
) -> SummaryArtifact:
    """メンバー（names．データ源の並び）の公開情報から，質問者が使う要約の成果物を作る．

    device_of_source はデータ源 → それを持つデバイス，summaries はデバイス → 公開情報．デバイスの番号は names の中で最初に現れた順
    （`SummaryProbeRouter._device_index` と同じ）．重みの式は `frontier_methods.cori_weights` と同じ：
    t = df / (df + 50 + 150 · cw / 平均の cw)，I = log((N + 0.5) / cf) / log(N + 1)，重み = t · I．
    """
    from scipy.sparse import csr_matrix

    order: dict[str, int] = {}
    for name in names:
        order.setdefault(device_of_source[name], len(order))
    devices = sorted(order, key=lambda d: order[d])
    n_dev = len(devices)
    vocab: dict[str, int] = {}
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    for dev, device in enumerate(devices):
        summary = summaries[device]
        for term, count in zip(summary.terms, summary.dfs, strict=True):
            rows.append(dev)
            cols.append(vocab.setdefault(term, len(vocab)))
            data.append(float(count))
    pub = csr_matrix((data, (rows, cols)), shape=(n_dev, len(vocab)), dtype=np.float64)
    cw = np.asarray([summaries[d].total_terms for d in devices], dtype=np.float64)
    avg_cw = float(cw.mean())
    cf = np.asarray((pub > 0).sum(axis=0)).ravel()
    t = pub.copy()
    row_of_entry = np.repeat(np.arange(n_dev), np.diff(pub.indptr))
    t.data = t.data / (t.data + CORI_DF_BASE + CORI_DF_SCALE * cw[row_of_entry] / avg_cw)
    i_weight = np.log((n_dev + 0.5) / np.maximum(cf, 1)) / np.log(n_dev + 1.0)
    weights = t.multiply(i_weight[None, :]).tocsr()
    centers = [
        np.asarray(summaries[device_of_source[n]].centers[n], dtype=np.float32) for n in names
    ]
    groups = [[n for n in names if device_of_source[n] == d] for d in devices]
    return SummaryArtifact(
        groups=groups,
        vocab=vocab,
        weights=weights,
        cf=cf,
        centers=centers,
        term_size=term_size,
        names=list(names),
    )


def published_bytes(summary: DeviceSummary) -> int:
    """公開する要約の大きさ（`SummaryProbeRouter.advert_bytes` と同じ数え方：語の組 8 byte，重心は float32）．"""
    from atrium.routing.summary import FLOAT32_BYTES, TERM_ENTRY_BYTES

    return len(summary.terms) * TERM_ENTRY_BYTES + sum(
        c.shape[0] * c.shape[1] * FLOAT32_BYTES for c in summary.centers.values()
    )
