"""起動後のメールの非表示・追加・取り出し（p0007 I5．atrium.store.FaissShardStore）の仕様．"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from atrium.config import FusionName
from atrium.store import FaissShardStore, MailDoc, load_shard
from tests.conftest import write_faiss_shard

DIM = 8
NEW_DOC = "new/mail"


def _unit(rng: np.random.Generator, n: int) -> np.ndarray:
    x = rng.normal(size=(n, DIM)).astype(np.float32)
    return x / np.linalg.norm(x, axis=1, keepdims=True)


@pytest.fixture
def shard(tmp_path: Path) -> tuple[Path, np.ndarray]:
    rng = np.random.default_rng(0)
    emb = _unit(rng, 6)
    write_faiss_shard(tmp_path, "inbox", "inbox", {"f0": emb})
    return tmp_path / "inbox", emb


def _load(shard_dir: Path, fusion: FusionName = "none") -> FaissShardStore:
    store = load_shard(shard_dir, fusion)
    assert isinstance(store, FaissShardStore)
    return store


def _ids(
    store: FaissShardStore, query: np.ndarray, k: int = 3, text: str | None = None
) -> list[str]:
    docs = store.search(k=k, embedding=query.tolist(), query_id=None, query_text=text)
    return [d.doc_id for d in docs]


@pytest.mark.parametrize("fusion", ["none", "rrf"])
def test_hidden_document_is_not_returned_and_the_next_one_fills_the_slot(
    shard: tuple[Path, np.ndarray], fusion: FusionName
) -> None:
    shard_dir, emb = shard
    store = _load(shard_dir, fusion)
    text = "inbox/f0/2"
    assert _ids(store, emb[2], text=text)[0] == "inbox/f0/2"
    assert store.hide(["inbox/f0/2"]) == 1
    after = _ids(store, emb[2], text=text)
    assert "inbox/f0/2" not in after
    assert len(after) == 3
    assert store.n_visible == 5


def test_hiding_nothing_leaves_the_search_unchanged(shard: tuple[Path, np.ndarray]) -> None:
    shard_dir, emb = shard
    store = _load(shard_dir)
    before = _ids(store, emb[1], k=4)
    store.hide([])
    assert _ids(store, emb[1], k=4) == before


def test_hiding_an_unknown_document_is_rejected(shard: tuple[Path, np.ndarray]) -> None:
    store = _load(shard[0])
    with pytest.raises(KeyError, match="unknown doc ids"):
        store.hide(["no/such"])


@pytest.mark.parametrize("fusion", ["none", "rrf"])
def test_added_mail_is_found_by_its_own_embedding_and_reports_timings(
    shard: tuple[Path, np.ndarray], fusion: FusionName
) -> None:
    shard_dir, _ = shard
    store = _load(shard_dir, fusion)
    new = _unit(np.random.default_rng(5), 1)
    timing = store.add([MailDoc(NEW_DOC, "subject", "fresh unique token zebra")], new)
    found = store.search(k=1, embedding=new[0].tolist(), query_id=None, query_text="zebra token")
    assert [d.doc_id for d in found] == [NEW_DOC]
    assert found[0].content == "fresh unique token zebra"
    assert timing.add_s >= 0.0
    assert (timing.bm25_s > 0.0) == (fusion == "rrf")
    assert store.n_visible == 7


def test_hiding_an_id_that_was_added_again_hides_every_row_with_that_id(
    shard: tuple[Path, np.ndarray],
) -> None:
    """再実行の状態（前の実行が足したメール）を残さないため，同じ ID の行は全て外れる．"""
    shard_dir, emb = shard
    store = _load(shard_dir)
    doc_id = "inbox/f0/1"
    store.hide([doc_id])
    store.add([MailDoc(doc_id, "t", "again")], emb[1:2])
    assert _ids(store, emb[1], k=1) == [doc_id]
    assert store.hide([doc_id]) == 1  # 足し直した行が新しく外れる
    assert doc_id not in _ids(store, emb[1], k=3)


def test_export_returns_hidden_documents_and_the_newest_row_for_a_repeated_id(
    shard: tuple[Path, np.ndarray],
) -> None:
    shard_dir, emb = shard
    store = _load(shard_dir)
    store.hide(["inbox/f0/3"])
    exported = store.export(["inbox/f0/3"])
    assert exported[0]["content"] == "inbox/f0/3"
    store.add([MailDoc("inbox/f0/3", "t", "rewritten")], emb[3:4])
    assert store.export(["inbox/f0/3"])[0]["content"] == "rewritten"
    assert {d["id"] for d in store.export(None)} == {
        "inbox/f0/0",
        "inbox/f0/1",
        "inbox/f0/2",
        "inbox/f0/4",
        "inbox/f0/5",
        "inbox/f0/3",
    }
    with pytest.raises(KeyError):
        store.export(["no/such"])


def test_add_rejects_embeddings_with_the_wrong_shape(shard: tuple[Path, np.ndarray]) -> None:
    store = _load(shard[0])
    with pytest.raises(ValueError, match="embeddings shape"):
        store.add([MailDoc("x", "t", "c")], np.zeros((2, DIM), dtype=np.float32))
