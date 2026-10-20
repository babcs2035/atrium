"""BM25 と密ベクトルの RRF（p0007 A2・I3．`retrieval.fusion: rrf`）の共通の部品（numpy だけで動く）．

ノード（受信箱の中．`atrium.store.FaissShardStore`）・中央（`atrium.routing.central.CentralIndex`）・オフラインの評価
（`atrium.fusion_eval`）が同じ関数を使い，改良の中身を中央と P2P で揃える（plans/p0007 §3.2）．

- `Bm25Index`：式・定数・語への分け方は `atrium.advert_eval.score_flood_bm25`・`atrium.central_eval.bm25_top_k` と同じ
  （`atrium.routing.advert.tokenize` で分けた語・質問の語は重複を除く）．専門家のイメージには scipy が無いので，
  転置索引を numpy の配列で持つ．統計（IDF・平均長）は，渡した文書の集合だけから作る（受信箱ごと／全体で共有は呼び出し側が決める）．
  外部の IDF・長さの平均で採点する経路（`scores_with_idf`．p0007 A4 の bm25_probe．`keep_counts=True` の索引だけ）は，
  受信箱ごとの統計を使う `scores` とは別で，`scores` の挙動を変えない．
- `rrf_top_k`：密ベクトルの上位 depth 通と BM25 の（得点が正の）上位 depth 通の順位から RRF の得点を作り，上位 k 通の番号を返す．
"""

from __future__ import annotations

from array import array
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

import numpy as np

from atrium.arrays import F32Array, F64Array, I64Array

BM25_K1 = 1.2
BM25_B = 0.75
# BM25 の文書の長さを数えない極端に短いメールのための下限（0 除算を避ける）
MIN_DOC_LENGTH = 1
# RRF の定数（Cormack ら 2009 の既定値）
RRF_K = 60


class Bm25Index:
    """文書の集合の BM25 の転置索引（語ごとの（文書の番号，重み）の並び）．"""

    def __init__(self, doc_tokens: Iterable[Sequence[str]], keep_counts: bool = False) -> None:
        """文書ごとの語の列から索引を作る（語の列は 1 件ずつ消費するので，全件を同時にメモリに持たなくてよい）．

        keep_counts=True のときは，外部の IDF・長さの平均で採点するために，語の出現数と文書の長さも残す（メモリが増える）．
        """
        vocab: dict[str, int] = {}
        term_ids = array("i")
        doc_ids = array("i")
        counts = array("i")
        lengths: list[int] = []
        for doc, tokens in enumerate(doc_tokens):
            lengths.append(max(MIN_DOC_LENGTH, len(tokens)))
            for term, count in Counter(tokens).items():
                term_ids.append(vocab.setdefault(term, len(vocab)))
                doc_ids.append(doc)
                counts.append(count)
        self.n_docs = len(lengths)
        self._vocab = vocab
        terms = np.frombuffer(term_ids, dtype=np.int32).astype(np.int64)
        docs = np.frombuffer(doc_ids, dtype=np.int32).astype(np.int64)
        tf = np.frombuffer(counts, dtype=np.int32).astype(np.float64)
        doc_len = np.asarray(lengths, dtype=np.float64)
        # 語ごとの文書数は，(文書, 語) の組が一意なので語の出現の数え上げに等しい
        df = np.bincount(terms, minlength=len(vocab)).astype(np.float64)
        idf = np.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
        avgdl = float(doc_len.mean()) if self.n_docs else 1.0
        norm = BM25_K1 * (1 - BM25_B + BM25_B * doc_len[docs] / avgdl)
        weights = idf[terms] * tf * (BM25_K1 + 1) / (tf + norm)
        order = np.argsort(terms, kind="stable")
        self._docs: I64Array = docs[order]
        self._weights: F64Array = weights[order]
        self._starts = np.concatenate([[0], np.cumsum(df)]).astype(np.int64)
        # scores_with_idf 用（_docs と同じ並び）．外部の統計では語の重みを作り直すので，出現数と文書の長さが要る
        self._tf: F32Array | None = tf[order].astype(np.float32) if keep_counts else None
        self._doc_len: F64Array | None = doc_len if keep_counts else None

    def scores(self, query_tokens: Iterable[str]) -> F64Array:
        """質問の語（重複を除く）に対する，全文書の BM25 の得点（語が 1 つも当たらない文書は 0）．"""
        out = np.zeros(self.n_docs, dtype=np.float64)
        for term in set(query_tokens):
            j = self._vocab.get(term)
            if j is not None:
                span = slice(self._starts[j], self._starts[j + 1])
                out[self._docs[span]] += self._weights[span]
        return out

    def scores_with_idf(self, idf_of_term: Mapping[str, float], avg_doc_len: float) -> F64Array:
        """外部から与えた語ごとの IDF と文書の長さの平均で，全文書の BM25 の得点を返す（式・定数は `scores` と同じ）．

        受信箱ごとの統計ではなく，全体で共有した統計（p0007 A4 の bm25_probe）で採点するための経路．idf_of_term の語が
        質問の語（重複なし）で，索引に無い語は飛ばす．keep_counts=True で作った索引だけが使える．
        """
        if self._tf is None or self._doc_len is None:
            raise ValueError("this index was built without keep_counts=True")
        if avg_doc_len <= 0:
            raise ValueError(f"avg_doc_len ({avg_doc_len}) must be positive")
        out = np.zeros(self.n_docs, dtype=np.float64)
        for term, idf in idf_of_term.items():
            j = self._vocab.get(term)
            if j is None:
                continue
            span = slice(self._starts[j], self._starts[j + 1])
            docs = self._docs[span]
            tf = self._tf[span].astype(np.float64)
            norm = BM25_K1 * (1 - BM25_B + BM25_B * self._doc_len[docs] / avg_doc_len)
            out[docs] += idf * tf * (BM25_K1 + 1) / (tf + norm)
        return out


def ranked_indices(scores: F64Array, depth: int) -> I64Array:
    """得点の降順の上位 depth 個の番号（同点は番号の小さい方を先にする）．"""
    order = np.lexsort((np.arange(len(scores)), -scores))[:depth]
    return np.asarray(order, dtype=np.int64)


def rrf_top_k(dense: F64Array, bm25: F64Array, k: int, depth: int) -> list[int]:
    """密ベクトルの得点 dense と BM25 の得点 bm25（どちらも文書の番号に対応）から，RRF で統合した上位 k 通の番号を返す．

    RRF の得点 = Σ 1 / (RRF_K + 順位)（順位は 1 始まり．密ベクトルの上位 depth 通と，BM25 の得点が正の上位 depth 通の
    どちらかに入る文書だけが候補．片方にしか入らない文書はその項が 0）．同点は密ベクトルの得点の高い方，それも同点なら番号の小さい方．
    """
    dense_top = ranked_indices(dense, depth)
    bm25_top = ranked_indices(bm25, depth)
    # 得点 0 の文書（質問の語を 1 つも含まない）に順位を付けると，番号の順の無意味な票になる
    bm25_top = bm25_top[bm25[bm25_top] > 0]
    fused: dict[int, float] = {}
    for ranking in (dense_top, bm25_top):
        for rank, doc in enumerate(ranking.tolist(), start=1):
            fused[doc] = fused.get(doc, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(fused, key=lambda d: (-fused[d], -dense[d], d))[:k]
