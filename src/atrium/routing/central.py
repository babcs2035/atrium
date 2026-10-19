"""中央集約（p0007 I4．routing=central_local）：質問者が全受信箱の 1 つの索引を持ち，ノードに問い合わせずに上位 k 通を取る．

索引は `atrium.central_eval` と同じ作り方である．全受信箱の埋め込み（fp16 を fp32 へ広げたもの）を data.enronqa.sources の順に
1 つの行列へ連結し，質問の埋め込みとの内積の上位 k 行を取る（同点は行の番号の小さい方を先にする．`central_eval.top_k_indices`）．
C1（全受信箱から上位 k 通を集める実機の実行）と文書 ID の集合が一致することは `atrium central-local-eval` で確かめる．

retrieval.fusion=rrf（p0007 A2．C1-RRF）のときは，全メールで統計を共有した BM25 の索引（`atrium.hybrid.Bm25Index`）も作り，
質問の文字列との BM25 と密ベクトルの RRF で上位 k 通を選ぶ（ノードの受信箱の中の改良と同じ式．score は内積のまま，並びは内積の降順）．

使い方：config.yaml で `experiment.routing: central_local`・`answer_mode: snippet_return` にする．質問者のコンテナの
`<data_dir>/enronqa/corpus/<受信箱>/{chunk,emb}/` を読むので，deploy が質問者へこれらを配る（scripts/remote/deploy.sh）．
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from atrium.arrays import F32Array
from atrium.central_eval import top_k_indices
from atrium.config import AtriumConfig
from atrium.hybrid import Bm25Index, rrf_top_k
from atrium.paths import DatasetPaths
from atrium.routing import RoutingQuery, SourceProfile
from atrium.store import RetrievedDoc

logger = logging.getLogger(__name__)

# 文書 ID は「<受信箱>/<フォルダ>/<番号>.」で，先頭の区切りまでが受信箱の名前（atrium.exposure_eval と同じ）
INBOX_SEPARATOR = "/"


class CentralLocalRouter:
    """問い合わせ先を選ばない（索引は質問者が持つので，どのノードにも問い合わせない）．"""

    name = "central_local"

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """空の一覧を返す．"""
        return []


def inbox_of_doc(doc_id: str) -> str:
    """文書 ID から，その文書が属する受信箱の名前を返す．"""
    return doc_id.split(INBOX_SEPARATOR, 1)[0]


@dataclass(frozen=True)
class CentralIndexLoad:
    """索引を作る（読み込む）費用．質問者の起動のたびに払う（p0007 §6.5 E1 の中央の再起動の時間の測定に使う）．"""

    n_docs: int
    n_inboxes: int
    read_docs_s: float
    read_emb_s: float
    total_s: float
    # fusion=rrf の BM25 の索引を作る秒数（total_s に含む．fusion=none は 0）
    bm25_s: float = 0.0


class CentralIndex:
    """全受信箱のメールを 1 つにまとめた密ベクトルの索引．"""

    def __init__(
        self,
        doc_ids: list[str],
        titles: list[str],
        contents: list[str],
        emb: F32Array,
        load: CentralIndexLoad,
        fusion_depth: int | None = None,
    ) -> None:
        """文書の ID・題・本文と，その行の順の埋め込み（fp32）を持つ．fusion_depth を指定すると，本文から BM25 の索引を作り RRF で検索する．"""
        if not (len(doc_ids) == len(titles) == len(contents) == emb.shape[0]):
            raise ValueError("documents and embeddings must have the same number of rows")
        self.doc_ids = doc_ids
        self._titles = titles
        self._contents = contents
        self.emb = emb
        self.load = load
        self._fusion_depth = fusion_depth
        self._bm25: Bm25Index | None = None
        if fusion_depth is not None:
            from atrium.routing.advert import tokenize

            start = time.perf_counter()
            self._bm25 = Bm25Index(tokenize(text) for text in contents)
            bm25_s = time.perf_counter() - start
            self.load = replace(load, bm25_s=bm25_s, total_s=load.total_s + bm25_s)

    @classmethod
    def from_corpus(cls, cfg: AtriumConfig, paths: DatasetPaths) -> CentralIndex:
        """データディレクトリの全受信箱を読んで索引を作る（`central_eval.evaluate` と同じ順・同じ型）．"""
        from atrium.data_enronqa import emb_path, read_inbox

        sources = cfg.data.require_enronqa().sources
        start = time.perf_counter()
        doc_ids: list[str] = []
        titles: list[str] = []
        contents: list[str] = []
        docs_s = emb_s = 0.0
        parts: list[F32Array] = []
        for name in sources:
            t0 = time.perf_counter()
            docs = read_inbox(paths, name)
            doc_ids += [d["id"] for d in docs]
            titles += [d["title"] for d in docs]
            contents += [d["content"] for d in docs]
            t1 = time.perf_counter()
            parts.append(np.load(emb_path(paths, name)).astype(np.float32))
            docs_s += t1 - t0
            emb_s += time.perf_counter() - t1
        emb = np.concatenate(parts)
        load = CentralIndexLoad(
            n_docs=len(doc_ids),
            n_inboxes=len(sources),
            read_docs_s=docs_s,
            read_emb_s=emb_s,
            total_s=time.perf_counter() - start,
        )
        logger.info(
            "central index: %d emails from %d inboxes in %.1f s",
            load.n_docs,
            load.n_inboxes,
            load.total_s,
        )
        fusion_depth = cfg.retrieval.fusion_depth if cfg.retrieval.fusion == "rrf" else None
        return cls(doc_ids, titles, contents, emb, load, fusion_depth)

    def search_ids(self, embedding: F32Array, k: int, query_text: str | None = None) -> list[int]:
        """上位 k 行の番号（得点の高い順．同点は番号の小さい方を先にする）．

        密ベクトルのみなら質問の埋め込みとの内積の順．BM25 の索引があれば query_text が必須で，RRF で選んだ k 行を内積の降順に並べる．
        """
        dense = self.emb @ np.asarray(embedding, dtype=np.float32)
        if self._bm25 is None:
            return [int(i) for i in top_k_indices(dense[None, :], k)[0]]
        from atrium.routing.advert import tokenize

        if query_text is None or self._fusion_depth is None:
            raise ValueError("fusion=rrf requires the query text")
        dense64 = dense.astype(np.float64)
        picked = rrf_top_k(dense64, self._bm25.scores(tokenize(query_text)), k, self._fusion_depth)
        return sorted(picked, key=lambda i: (-dense64[i], i))

    def search(
        self, embedding: F32Array, k: int, query_text: str | None = None
    ) -> list[RetrievedDoc]:
        """上位 k 通の文書を得点の降順で返す（得点は内積）．"""
        query = np.asarray(embedding, dtype=np.float32)
        return [
            RetrievedDoc(
                self.doc_ids[i], self._titles[i], self._contents[i], float(self.emb[i] @ query)
            )
            for i in self.search_ids(query, k, query_text)
        ]
