"""中央集約（p0007 I4．routing=central_local）：質問者が全受信箱の 1 つの索引を持ち，ノードに問い合わせずに上位 k 通を取る．

索引は `atrium.central_eval` と同じ作り方である．全受信箱の埋め込み（fp16 を fp32 へ広げたもの）を data.enronqa.sources の順に
1 つの行列へ連結し，質問の埋め込みとの内積の上位 k 行を取る（同点は行の番号の小さい方を先にする．`central_eval.top_k_indices`）．
C1（全受信箱から上位 k 通を集める実機の実行）と文書 ID の集合が一致することは `atrium central-local-eval` で確かめる．

使い方：config.yaml で `experiment.routing: central_local`・`answer_mode: snippet_return` にする．質問者のコンテナの
`<data_dir>/enronqa/corpus/<受信箱>/{chunk,emb}/` を読むので，deploy が質問者へこれらを配る（scripts/remote/deploy.sh）．
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from atrium.arrays import F32Array
from atrium.central_eval import top_k_indices
from atrium.config import AtriumConfig
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


class CentralIndex:
    """全受信箱のメールを 1 つにまとめた密ベクトルの索引．"""

    def __init__(
        self,
        doc_ids: list[str],
        titles: list[str],
        contents: list[str],
        emb: F32Array,
        load: CentralIndexLoad,
    ) -> None:
        """文書の ID・題・本文と，その行の順の埋め込み（fp32）を持つ．"""
        if not (len(doc_ids) == len(titles) == len(contents) == emb.shape[0]):
            raise ValueError("documents and embeddings must have the same number of rows")
        self.doc_ids = doc_ids
        self._titles = titles
        self._contents = contents
        self.emb = emb
        self.load = load

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
        return cls(doc_ids, titles, contents, emb, load)

    def search_ids(self, embedding: F32Array, k: int) -> list[int]:
        """質問の埋め込みとの内積の上位 k 行の番号（得点の高い順．同点は番号の小さい方を先にする）．"""
        scores = (self.emb @ np.asarray(embedding, dtype=np.float32))[None, :]
        return [int(i) for i in top_k_indices(scores, k)[0]]

    def search(self, embedding: F32Array, k: int) -> list[RetrievedDoc]:
        """上位 k 通の文書を得点の降順で返す（得点は内積）．"""
        query = np.asarray(embedding, dtype=np.float32)
        return [
            RetrievedDoc(
                self.doc_ids[i], self._titles[i], self._contents[i], float(self.emb[i] @ query)
            )
            for i in self.search_ids(query, k)
        ]
