"""専門家ノードが持つシャードの格納と検索．

シャードのディレクトリ構成（中継点で作り，deploy がそのまま専門家へ配る）:

    <shard_id>/shard.json             ShardSpec
    <shard_id>/chunk/<name>.jsonl     断片の本文（kind="faiss"．MedRAG の chunk と同じ書式）
    <shard_id>/emb/<name>.f16.npy     断片の埋め込み（kind="faiss"．行は chunk の行と 1 対 1）
    <shard_id>/results.jsonl          {"qid", "hits": [[doc_id, score], ...]}（kind="search_results"）
    <shard_id>/docs.jsonl             {"_id", "title", "text"}（kind="search_results"）

本文は全件をメモリに載せず，行の先頭のバイト位置だけを持って必要な行を読み出す
（PubMed のシャードは本文だけで数 GB あり，L480 の 16 GB に索引と同居させるため）．
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import faiss
import numpy as np

from atrium.arrays import I64Array
from atrium.manifest import SHARD_SPEC_FILENAME, ShardSpec

logger = logging.getLogger(__name__)

# 索引へ追加するときに fp16 → fp32 へ変換する行数（変換用の一時メモリを 300 MB 程度に抑える）
ADD_BATCH_ROWS = 100_000


@dataclass(frozen=True)
class RetrievedDoc:
    """検索で得た断片 1 件．"""

    doc_id: str
    title: str
    content: str
    score: float


class ShardStore(Protocol):
    """シャードの検索の共通インタフェース．"""

    spec: ShardSpec

    def search(
        self, *, k: int, embedding: list[float] | None, query_id: str | None
    ) -> list[RetrievedDoc]:
        """上位 k 件の断片をスコアの降順で返す．"""
        ...


def _line_offsets(path: Path) -> I64Array:
    """jsonl の各行の先頭のバイト位置を返す（結果は隣に .offsets.npy としてキャッシュする）．"""
    cache = path.with_suffix(".offsets.npy")
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        cached: I64Array = np.load(cache)
        return cached
    offsets: list[int] = []
    pos = 0
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                offsets.append(pos)
            pos += len(line)
    arr = np.array(offsets, dtype=np.int64)
    np.save(cache, arr)
    return arr


class FaissShardStore:
    """MedRAG 型のシャード．fp16 の FAISS 平坦索引で内積検索する．"""

    def __init__(self, shard_dir: Path, spec: ShardSpec) -> None:
        """シャードを読み込み，索引を組み立てる．"""
        self.spec = spec
        self._dir = shard_dir
        self._lock = threading.Lock()
        self._index = faiss.IndexScalarQuantizer(
            spec.dim, faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT
        )
        # 索引の通し番号 → (ファイル番号, 行番号) を引くための累積行数
        self._file_starts = np.zeros(len(spec.files) + 1, dtype=np.int64)
        self._offsets: list[I64Array] = []
        for i, chunk in enumerate(spec.files):
            emb = np.load(shard_dir / "emb" / f"{chunk.name}.f16.npy", mmap_mode="r")
            if emb.shape != (chunk.n_docs, spec.dim):
                raise ValueError(
                    f"{chunk.name}: embedding shape {emb.shape} != ({chunk.n_docs}, {spec.dim})"
                )
            for start in range(0, emb.shape[0], ADD_BATCH_ROWS):
                self._index.add(
                    np.ascontiguousarray(emb[start : start + ADD_BATCH_ROWS], "float32")
                )
            offsets = _line_offsets(shard_dir / "chunk" / f"{chunk.name}.jsonl")
            if len(offsets) != chunk.n_docs:
                raise ValueError(f"{chunk.name}: {len(offsets)} lines != {chunk.n_docs} docs")
            self._offsets.append(offsets)
            self._file_starts[i + 1] = self._file_starts[i] + chunk.n_docs
        logger.info("loaded shard %s (%d docs)", spec.shard_id, self._index.ntotal)

    def _read_doc(self, global_idx: int) -> dict[str, str]:
        file_no = int(np.searchsorted(self._file_starts, global_idx, side="right")) - 1
        line_no = global_idx - int(self._file_starts[file_no])
        path = self._dir / "chunk" / f"{self.spec.files[file_no].name}.jsonl"
        with path.open("rb") as f:
            f.seek(int(self._offsets[file_no][line_no]))
            doc: dict[str, str] = json.loads(f.readline())
        return doc

    def search(
        self, *, k: int, embedding: list[float] | None, query_id: str | None
    ) -> list[RetrievedDoc]:
        """クエリ埋め込みとの内積の上位 k 件を返す．"""
        if embedding is None:
            raise ValueError("faiss shard requires a query embedding")
        if len(embedding) != self.spec.dim:
            raise ValueError(f"embedding dim {len(embedding)} != {self.spec.dim}")
        query = np.asarray(embedding, dtype=np.float32).reshape(1, -1)
        # FAISS の検索自体はスレッド安全だが，OpenMP のスレッドを奪い合うと遅くなるので直列にする
        with self._lock:
            scores, ids = self._index.search(query, k)
        docs: list[RetrievedDoc] = []
        for score, idx in zip(scores[0], ids[0], strict=True):
            if idx < 0:
                continue
            raw = self._read_doc(int(idx))
            docs.append(
                RetrievedDoc(
                    doc_id=raw["id"], title=raw["title"], content=raw["content"], score=float(score)
                )
            )
        return docs


class SearchResultsShardStore:
    """FeB4RAG 型のシャード．配布された検索結果をそのまま返す．"""

    def __init__(self, shard_dir: Path, spec: ShardSpec) -> None:
        """検索結果と，そこに現れる文書の本文を読み込む（いずれも数十 MB 程度）．"""
        self.spec = spec
        self._hits: dict[str, list[tuple[str, float]]] = {}
        with (shard_dir / "results.jsonl").open(encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                self._hits[str(row["qid"])] = [(str(d), float(s)) for d, s in row["hits"]]
        self._docs: dict[str, tuple[str, str]] = {}
        with (shard_dir / "docs.jsonl").open(encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                self._docs[str(row["_id"])] = (row.get("title") or "", row.get("text") or "")
        logger.info("loaded shard %s (%d queries)", spec.shard_id, len(self._hits))

    def search(
        self, *, k: int, embedding: list[float] | None, query_id: str | None
    ) -> list[RetrievedDoc]:
        """要求 ID に対する配布済みの検索結果の上位 k 件を返す．"""
        if query_id is None:
            raise ValueError("search_results shard requires a query_id")
        if query_id not in self._hits:
            raise KeyError(f"unknown query_id {query_id!r}")
        docs: list[RetrievedDoc] = []
        for doc_id, score in self._hits[query_id][:k]:
            title, text = self._docs.get(doc_id, ("", ""))
            docs.append(RetrievedDoc(doc_id=doc_id, title=title, content=text, score=score))
        return docs


def load_shard(shard_dir: Path) -> ShardStore:
    """shard.json の kind に応じてシャードを読み込む．"""
    spec = ShardSpec.model_validate_json((shard_dir / SHARD_SPEC_FILENAME).read_text("utf-8"))
    if spec.kind == "faiss":
        return FaissShardStore(shard_dir, spec)
    return SearchResultsShardStore(shard_dir, spec)
