"""中央の索引の，起動後の追加・取り消し・一部の受信箱／文書を外した構築（p0007 I5・I7．atrium.routing.central）の仕様．"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path

import numpy as np
import pytest

from atrium.config import AtriumConfig
from atrium.paths import dataset_paths
from atrium.routing.central import MIN_GROWTH_ROWS, CentralIndex
from tests.test_central_local import DIM, DOCS_PER_INBOX, INBOXES, central_cfg, write_corpus


def _unit(seed: int, n: int) -> np.ndarray:
    x = np.random.default_rng(seed).normal(size=(n, DIM)).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _build(
    cfg: AtriumConfig,
    root: Path,
    sources: Sequence[str] | None = None,
    exclude_doc_ids: Collection[str] = (),
) -> CentralIndex:
    paths = dataset_paths(root, "enronqa")
    return CentralIndex.from_corpus(central_cfg(cfg), paths, sources, exclude_doc_ids)


def test_excluded_documents_are_not_in_the_index(cfg: AtriumConfig, tmp_path: Path) -> None:
    write_corpus(tmp_path)
    excluded = {"alice/inbox/2.", "bob/inbox/0."}
    index = _build(cfg, tmp_path, exclude_doc_ids=excluded)
    assert index.n_docs == len(INBOXES) * DOCS_PER_INBOX - 2
    assert excluded.isdisjoint(index.doc_ids)
    assert index.emb.shape == (index.n_docs, DIM)


def test_index_can_be_built_from_some_inboxes_only(cfg: AtriumConfig, tmp_path: Path) -> None:
    write_corpus(tmp_path)
    index = _build(cfg, tmp_path, sources=["alice", "carol"])
    assert {d.split("/")[0] for d in index.doc_ids} == {"alice", "carol"}
    assert index.load.n_inboxes == 2


def test_appended_mail_is_searchable_and_truncate_restores_the_index(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    write_corpus(tmp_path)
    index = _build(cfg, tmp_path)
    query = _unit(1, 1)[0]
    before = index.search_ids(query, 5)
    n_before = index.n_docs
    spent_s = index.append(["new/1."], ["t"], ["fresh body"], query[None, :] * 10)
    assert spent_s >= 0.0
    assert index.n_docs == n_before + 1
    assert index.doc_ids[index.search_ids(query, 1)[0]] == "new/1."
    index.truncate(n_before)
    assert index.n_docs == n_before
    assert "new/1." not in index.doc_ids
    assert index.search_ids(query, 5) == before


def test_append_beyond_the_reserved_area_grows_the_buffer_without_losing_rows(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    write_corpus(tmp_path)
    index = _build(cfg, tmp_path)
    first = index.emb[0].copy()
    n = MIN_GROWTH_ROWS + 50
    index.append([f"x/{i}." for i in range(n)], ["t"] * n, ["c"] * n, _unit(2, n))
    assert index.n_docs == len(INBOXES) * DOCS_PER_INBOX + n
    assert np.array_equal(index.emb[0], first)


def test_appending_an_existing_document_id_is_rejected(cfg: AtriumConfig, tmp_path: Path) -> None:
    write_corpus(tmp_path)
    index = _build(cfg, tmp_path)
    with pytest.raises(ValueError, match="already in the index"):
        index.append([index.doc_ids[0]], ["t"], ["c"], _unit(3, 1))


def test_rrf_index_rebuilds_bm25_on_append_and_finds_the_mail_by_its_words(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    write_corpus(tmp_path)
    rrf = central_cfg(cfg)
    rrf = rrf.model_copy(update={"retrieval": rrf.retrieval.model_copy(update={"fusion": "rrf"})})
    index = CentralIndex.from_corpus(rrf, dataset_paths(tmp_path, "enronqa"))
    index.append(["new/1."], ["t"], ["zebra quagga unique"], _unit(4, 1))
    assert index.bm25_s > 0.0
    found = index.search_ids(_unit(5, 1)[0], 3, "zebra quagga")
    assert "new/1." in [index.doc_ids[i] for i in found]
