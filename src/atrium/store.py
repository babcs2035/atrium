"""専門家ノードが持つシャードの格納と検索．

シャードのディレクトリ構成（中継点で作り，deploy がそのまま専門家へ配る）:

    <shard_id>/shard.json             ShardSpec
    <shard_id>/chunk/<name>.jsonl     断片の本文（kind="faiss"．MedRAG の chunk と同じ書式）
    <shard_id>/emb/<name>.f16.npy     断片の埋め込み（kind="faiss"．行は chunk の行と 1 対 1）
    <shard_id>/results.jsonl          {"qid", "hits": [[doc_id, score], ...]}（kind="search_results"）
    <shard_id>/docs.jsonl             {"_id", "title", "text"}（kind="search_results"）

retrieval.fusion=rrf（p0007 A2）のときは，起動時に全断片の本文を 1 回読んで BM25 の転置索引（`atrium.hybrid.Bm25Index`．
そのシャードだけの統計）を作り，検索で BM25 と密ベクトルの RRF の上位を返す（本文そのものは載せない）．

routing=bm25_probe（p0007 A4）のノードは，上の RRF 用とは別に，メールの本文（区切り線より後．ヘッダーは使わない．p0007 §3.3）だけの
転置索引を作り，質問者から渡された語ごとの IDF と長さの平均（全体で共有した統計）で，受信箱の中の BM25 の最大を返す（`probe_bm25`）．

本文は全件をメモリに載せず，行の先頭のバイト位置だけを持って必要な行を読み出す
（PubMed のシャードは本文だけで数 GB あり，L480 の 16 GB に索引と同居させるため）．

p0007 I5（新しいメールへの追従）のため，kind="faiss" のシャードは起動後に次の操作を受け付ける（既定の挙動は変えない）．

- `hide`：指定した文書を検索の結果から外す（行を消さず，検索で飛ばす．ファイルのオフセットの対応を壊さないため）．
- `add`：新しいメール（と，デバイスが作った埋め込み）を索引の末尾に足す．本文はメモリに持つ（ディスクのシャードは変えない）．
  fusion=rrf のときは BM25 の索引を全文書から作り直す（足した時間に含める）．
- `export`：指定した文書（外した文書を含む）の本文を返す．中央への転送の測定で使う．
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import faiss
import numpy as np

from atrium.arrays import F32Array, F64Array, I64Array
from atrium.config import FusionName
from atrium.hybrid import Bm25Index, rrf_scores, rrf_top_k
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


@dataclass(frozen=True)
class MailDoc:
    """起動後に足すメール 1 通（本文の埋め込みは足すときに別に渡す）．"""

    doc_id: str
    title: str
    content: str


@dataclass(frozen=True)
class AddTiming:
    """メールの追加（索引への追加と，fusion=rrf の BM25 の作り直し）の所要時間（秒）．"""

    add_s: float
    bm25_s: float


class ShardStore(Protocol):
    """シャードの検索の共通インタフェース．"""

    spec: ShardSpec

    def search(
        self,
        *,
        k: int,
        embedding: list[float] | None,
        query_id: str | None,
        query_text: str | None = None,
    ) -> list[RetrievedDoc]:
        """上位 k 件の断片をスコアの降順で返す（query_text は fusion=rrf の BM25 だけが使う．無ければ密ベクトルのみ）．"""
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

    def __init__(
        self,
        shard_dir: Path,
        spec: ShardSpec,
        fusion: FusionName = "none",
        fusion_depth: int = 50,
        body_bm25_probe: bool = False,
    ) -> None:
        """シャードを読み込み，索引を組み立てる（fusion=rrf なら BM25 の索引も，body_bm25_probe なら本文だけの probe 用の索引も作る）．"""
        self.spec = spec
        self._fusion = fusion
        self._fusion_depth = fusion_depth
        self._bm25: Bm25Index | None = None
        self._probe_bm25: Bm25Index | None = None
        self._body_bm25_probe = body_bm25_probe
        self.bm25_build_s = 0.0
        self._dir = shard_dir
        self._lock = threading.Lock()
        # p0007 I5：起動後に足したメール（行の番号は _base_rows から続く）と，検索から外した行
        self._extra: list[dict[str, str]] = []
        self._hidden: set[int] = set()
        self._rows_of_id: dict[str, list[int]] | None = None
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
        self._base_rows = int(self._index.ntotal)
        if fusion == "rrf" or body_bm25_probe:
            self._build_bm25()
        logger.info(
            "loaded shard %s (%d docs, fusion=%s, body_bm25_probe=%s, bm25 build %.2f s)",
            spec.shard_id,
            self._index.ntotal,
            fusion,
            body_bm25_probe,
            self.bm25_build_s,
        )

    def _contents(self) -> Iterator[str]:
        """全断片（足したメールを含む）の `content` を行の順に読む（索引の行の番号と同じ並び）．"""
        for chunk in self.spec.files:
            with (self._dir / "chunk" / f"{chunk.name}.jsonl").open("rb") as f:
                for line in f:
                    if line.strip():
                        yield json.loads(line)["content"]
        for doc in self._extra:
            yield doc["content"]

    def _build_bm25(self) -> None:
        """全断片の本文を先頭から読み，BM25 の索引を作る（本文は索引に残さない）．

        fusion=rrf の索引は `content` 全体・そのシャードだけの統計（A2．従来どおり）．body_bm25_probe の索引は本文だけで，
        外部の統計で採点するために出現数と文書の長さも残す．所要時間は両方の合計を bm25_build_s に入れる．
        """
        from atrium.device_summary import body_of
        from atrium.routing.advert import tokenize

        start = time.perf_counter()
        if self._fusion == "rrf":
            self._bm25 = Bm25Index(tokenize(content) for content in self._contents())
            if self._bm25.n_docs != self._index.ntotal:
                raise ValueError(
                    f"bm25 docs {self._bm25.n_docs} != index docs {self._index.ntotal}"
                )
        if self._body_bm25_probe:
            self._probe_bm25 = Bm25Index(
                (tokenize(body_of(content)) for content in self._contents()), keep_counts=True
            )
            if self._probe_bm25.n_docs != self._index.ntotal:
                raise ValueError(
                    f"probe bm25 docs {self._probe_bm25.n_docs} != index docs {self._index.ntotal}"
                )
        self.bm25_build_s = time.perf_counter() - start

    def probe_bm25(self, idf_of_term: Mapping[str, float], avg_doc_len: float) -> float:
        """質問者から渡された語ごとの IDF・長さの平均（全体で共有した統計）で，受信箱の中の本文の BM25 の最大を返す（外した文書は除く）．

        質問の語が 1 つも当たらなければ 0．body_bm25_probe=True のシャードだけが使える．
        """
        if self._probe_bm25 is None:
            raise ValueError("this shard was loaded without the body BM25 probe index")
        scores = self._probe_bm25.scores_with_idf(idf_of_term, avg_doc_len)
        with self._lock:
            hidden = np.fromiter(self._hidden, dtype=np.int64, count=len(self._hidden))
        if hidden.size:
            scores[hidden] = 0.0
        return float(scores.max()) if scores.size else 0.0

    def _read_doc(self, global_idx: int) -> dict[str, str]:
        if global_idx >= self._base_rows:
            return self._extra[global_idx - self._base_rows]
        file_no = int(np.searchsorted(self._file_starts, global_idx, side="right")) - 1
        line_no = global_idx - int(self._file_starts[file_no])
        path = self._dir / "chunk" / f"{self.spec.files[file_no].name}.jsonl"
        with path.open("rb") as f:
            f.seek(int(self._offsets[file_no][line_no]))
            doc: dict[str, str] = json.loads(f.readline())
        return doc

    def search(
        self,
        *,
        k: int,
        embedding: list[float] | None,
        query_id: str | None,
        query_text: str | None = None,
    ) -> list[RetrievedDoc]:
        """クエリ埋め込みとの内積の上位 k 件を返す（fusion=rrf なら，BM25 との RRF で選んだ上位 k 件．score は内積のまま）．"""
        if embedding is None:
            raise ValueError("faiss shard requires a query embedding")
        if len(embedding) != self.spec.dim:
            raise ValueError(f"embedding dim {len(embedding)} != {self.spec.dim}")
        query = np.asarray(embedding, dtype=np.float32).reshape(1, -1)
        # query_text が無い検索（ルーティングの probe）は，fusion=rrf でも密ベクトルのみ（ルーティングは変えない）
        if self._bm25 is not None and query_text is not None:
            return self._search_rrf(k, query, query_text)
        # FAISS の検索自体はスレッド安全だが，OpenMP のスレッドを奪い合うと遅くなるので直列にする
        with self._lock:
            # 外した行は結果から飛ばすので，その分だけ多めに取る（外した行が無ければ従来どおり k 件）
            scores, ids = self._index.search(query, min(k + len(self._hidden), self._index.ntotal))
        docs: list[RetrievedDoc] = []
        for score, idx in zip(scores[0], ids[0], strict=True):
            if idx < 0 or int(idx) in self._hidden:
                continue
            if len(docs) == k:
                break
            raw = self._read_doc(int(idx))
            docs.append(
                RetrievedDoc(
                    doc_id=raw["id"], title=raw["title"], content=raw["content"], score=float(score)
                )
            )
        return docs

    def _dense_and_bm25(self, query: F32Array, query_text: str) -> tuple[F64Array, F64Array]:
        """全断片の内積と，その受信箱だけの統計の BM25 の得点（どちらも索引の行の番号に対応）．"""
        from atrium.routing.advert import tokenize

        if self._bm25 is None:
            raise ValueError("fusion=rrf requires the BM25 index")
        n = self._index.ntotal
        with self._lock:
            scores, ids = self._index.search(query, n)
        dense = np.zeros(n, dtype=np.float64)
        dense[ids[0]] = scores[0]
        return dense, self._bm25.scores(tokenize(query_text))

    def fusion_maxima(self, embedding: list[float], query_text: str) -> tuple[float, float]:
        """p0007 A5：受信箱の中の（BM25 の最大，RRF の得点の最大）．外した文書は除く．fusion=rrf のシャードだけが使える．

        検索（`_search_rrf`）と同じ得点の作り方で，owner_answer の候補を選ぶ特徴量にする（検索の結果は変えない）．
        """
        query = np.asarray(embedding, dtype=np.float32).reshape(1, -1)
        dense, bm25_scores = self._dense_and_bm25(query, query_text)
        if self._hidden:
            visible = np.setdiff1d(np.arange(len(dense)), np.fromiter(self._hidden, dtype=np.int64))
            dense, bm25_scores = dense[visible], bm25_scores[visible]
        if dense.size == 0:
            return 0.0, 0.0
        fused = rrf_scores(dense, bm25_scores, self._fusion_depth)
        return float(bm25_scores.max()), max(fused.values(), default=0.0)

    def _search_rrf(self, k: int, query: F32Array, query_text: str) -> list[RetrievedDoc]:
        """全断片の内積と BM25 の得点から RRF で上位 k 件を選ぶ．score は内積（受信箱をまたぐ統合の比べ方を変えない）．

        文脈の並びは従来どおり内積の降順にする（RRF が変えるのは，どの断片が上位 k 件に入るかだけ）．
        """
        dense, bm25_scores = self._dense_and_bm25(query, query_text)
        n = len(dense)
        if self._hidden:
            # 外した行は順位づけの対象から除く（残した行の番号へ対応づけ直す）
            visible = np.setdiff1d(np.arange(n), np.fromiter(self._hidden, dtype=np.int64))
            picked = [
                int(visible[i])
                for i in rrf_top_k(dense[visible], bm25_scores[visible], k, self._fusion_depth)
            ]
        else:
            picked = rrf_top_k(dense, bm25_scores, k, self._fusion_depth)
        picked.sort(key=lambda i: (-dense[i], i))
        docs: list[RetrievedDoc] = []
        for idx in picked:
            raw = self._read_doc(idx)
            docs.append(
                RetrievedDoc(
                    doc_id=raw["id"],
                    title=raw["title"],
                    content=raw["content"],
                    score=float(dense[idx]),
                )
            )
        return docs

    # ── p0007 I5：起動後のメールの非表示・追加・取り出し ─────────────────────────────

    @property
    def n_visible(self) -> int:
        """検索の対象になる文書の数（外した行を除く）．"""
        return int(self._index.ntotal) - len(self._hidden)

    def _row_map(self) -> dict[str, list[int]]:
        """文書 ID → 行の番号（同じ ID を足し直しても全ての行を外せるよう，リストで持つ）．最初に必要になったとき 1 回だけ全断片を読む．"""
        if self._rows_of_id is None:
            rows: dict[str, list[int]] = {}
            row = 0
            for chunk in self.spec.files:
                with (self._dir / "chunk" / f"{chunk.name}.jsonl").open("rb") as f:
                    for line in f:
                        if line.strip():
                            rows.setdefault(json.loads(line)["id"], []).append(row)
                            row += 1
            for doc in self._extra:
                rows.setdefault(doc["id"], []).append(row)
                row += 1
            self._rows_of_id = rows
        return self._rows_of_id

    def hide(self, doc_ids: Sequence[str]) -> int:
        """文書を検索の結果から外す（足し直した同じ ID の行も外す）．新しく外れた行の数を返す．未知の ID は誤りとして KeyError にする．"""
        rows = self._row_map()
        unknown = [d for d in doc_ids if d not in rows]
        if unknown:
            raise KeyError(f"unknown doc ids: {unknown[:3]}")
        with self._lock:
            before = len(self._hidden)
            for doc_id in doc_ids:
                self._hidden.update(rows[doc_id])
            return len(self._hidden) - before

    def add(self, docs: Sequence[MailDoc], embeddings: F32Array) -> AddTiming:
        """新しいメールを索引の末尾に足す（embeddings は docs と同じ並びの行列）．足した行は検索の対象になる．

        同じ ID の古い行（外してある行）は外したままにし，新しい行が代わりに検索される．
        """
        if embeddings.shape != (len(docs), self.spec.dim):
            raise ValueError(
                f"embeddings shape {embeddings.shape} != ({len(docs)}, {self.spec.dim})"
            )
        start = time.perf_counter()
        rows = self._row_map()
        with self._lock:
            first = int(self._index.ntotal)
            self._index.add(np.ascontiguousarray(embeddings, dtype=np.float32))
            for offset, doc in enumerate(docs):
                self._extra.append({"id": doc.doc_id, "title": doc.title, "content": doc.content})
                rows.setdefault(doc.doc_id, []).append(first + offset)
        add_s = time.perf_counter() - start
        bm25_s = 0.0
        if self._fusion == "rrf" or self._body_bm25_probe:
            self._build_bm25()
            bm25_s = self.bm25_build_s
        return AddTiming(add_s=add_s, bm25_s=bm25_s)

    def export(self, doc_ids: Sequence[str] | None) -> list[dict[str, str]]:
        """文書（外した文書も含む）の {id, title, content} を返す．doc_ids が None なら検索の対象の全文書．

        同じ ID が複数の行にあるときは，最後に足した行（最新）を返す．未知の ID は KeyError．
        """
        if doc_ids is None:
            return [self._read_doc(i) for i in range(self._index.ntotal) if i not in self._hidden]
        rows = self._row_map()
        unknown = [d for d in doc_ids if d not in rows]
        if unknown:
            raise KeyError(f"unknown doc ids: {unknown[:3]}")
        return [self._read_doc(rows[d][-1]) for d in doc_ids]


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
        self,
        *,
        k: int,
        embedding: list[float] | None,
        query_id: str | None,
        query_text: str | None = None,
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


def load_shard(
    shard_dir: Path,
    fusion: FusionName = "none",
    fusion_depth: int = 50,
    body_bm25_probe: bool = False,
) -> ShardStore:
    """shard.json の kind に応じてシャードを読み込む（fusion・body_bm25_probe は faiss のシャードだけで有効．他の kind では指定を拒否する）．"""
    spec = ShardSpec.model_validate_json((shard_dir / SHARD_SPEC_FILENAME).read_text("utf-8"))
    if spec.kind == "faiss":
        return FaissShardStore(shard_dir, spec, fusion, fusion_depth, body_bm25_probe)
    if fusion != "none":
        raise ValueError(f"retrieval.fusion={fusion} supports only faiss shards, not {spec.kind}")
    if body_bm25_probe:
        raise ValueError(f"routing=bm25_probe supports only faiss shards, not {spec.kind}")
    return SearchResultsShardStore(shard_dir, spec)
