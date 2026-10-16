"""EnronQA のデータ準備（メールの整形・公開情報・重複の検出・負例の抜き出し）の仕様．"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from atrium.data_enronqa import (
    card_text,
    clean_email,
    find_cross_inbox_duplicates,
    kmeans_centroids,
    load_gold_docs,
    term_sketch,
)
from atrium.paths import dataset_paths
from atrium.train_router import SourceInfo, build_matrix

RAW = (
    "Subject: FW: Ameren\n"
    "Sender: stephanie.panus@enron.com\n"
    "Recipients: ['a@enron.com']\n"
    "File: phanis-s/sent_items/4.\n"
    "=====================================\n"
    "Please forward this message."
)


def test_clean_email_drops_the_file_line_that_names_the_inbox() -> None:
    subject, text = clean_email(RAW)
    assert subject == "FW: Ameren"
    assert "phanis-s" not in text
    assert "Sender: stephanie.panus@enron.com" in text
    assert text.endswith("Please forward this message.")


def test_card_lists_the_most_frequent_subjects_without_reply_prefixes() -> None:
    card = card_text(["RE: Gas deal", "Gas deal", "FW: Gas deal", "Lunch"], n=1)
    assert card == "Email inbox. Frequent subjects: Gas deal"


def test_term_sketch_uses_only_local_document_frequencies_and_skips_stop_words() -> None:
    terms, weights = term_sketch(["the gas price", "gas pipeline", "price of gas 2001"], size=2)
    assert terms == ["gas", "price"]
    assert weights == [1.0, 2 / 3]


def test_kmeans_returns_all_rows_when_the_inbox_is_small() -> None:
    emb = np.eye(3, dtype=np.float32)
    assert np.array_equal(kmeans_centroids(emb, n=8, seed=0), emb)


def test_cross_inbox_duplicates_ignore_copies_within_the_same_inbox() -> None:
    same = {"meeting", "at", "noon", "in", "room", "five"}
    token_sets = [same, set(same), set(same), {"unrelated", "words", "only"}]
    inboxes = ["a", "a", "b", "b"]
    assert find_cross_inbox_duplicates(token_sets, inboxes, threshold=0.9, seed=0) == {0, 1, 2}


def test_ragroute_negative_sampling_keeps_every_positive_pair() -> None:
    sources = [SourceInfo(f"s{i}", "enc", np.zeros(2, np.float32)) for i in range(10)]
    emb = {"enc": np.ones((1, 2), np.float32)}
    x, y = build_matrix(
        ["q"],
        {"q": 0},
        emb,
        sources,
        {"q": ["s3"]},
        pad_dim=2,
        negatives=4,
        rng=np.random.default_rng(0),
    )
    assert x.shape[0] == 5
    assert y.sum() == 1.0


def test_triangular2_scale_matches_torch_and_does_not_overflow_on_long_runs() -> None:
    from atrium.train_router import triangular2_scale

    assert [triangular2_scale(c) for c in (1, 2, 3)] == [1.0, 0.5, 0.25]
    assert triangular2_scale(5000) == 2.0**-1000


def test_load_gold_docs_returns_the_gold_email_of_each_question(tmp_path: Path) -> None:
    paths = dataset_paths(tmp_path, "enronqa")
    paths.questions.parent.mkdir(parents=True)
    gold = [
        {"qid": "q0", "inbox": "a", "path": "a/1"},
        {"qid": "q1", "inbox": "b", "path": "b/9"},
        {"qid": "q2", "inbox": "a", "path": "a/2"},
    ]
    (paths.questions.parent / "gold.jsonl").write_text("\n".join(json.dumps(g) for g in gold))
    for inbox, mails in {"a": ["a/1", "a/2"], "b": ["b/9"]}.items():
        chunk = paths.corpus(inbox) / "chunk"
        chunk.mkdir(parents=True)
        rows = [{"id": m, "title": f"t {m}", "content": f"body {m}"} for m in mails]
        (chunk / f"{inbox}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))

    docs = load_gold_docs(paths, ["q1", "q0"])

    assert list(docs) == ["q1", "q0"]
    assert (docs["q1"].source, docs["q1"].doc.doc_id, docs["q1"].doc.content) == (
        "b",
        "b/9",
        "body b/9",
    )
    assert docs["q0"].doc.title == "t a/1"
