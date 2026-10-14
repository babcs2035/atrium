"""公開情報を本文から作り直す評価（atrium.body_advert_eval）の仕様：本文だけの受信箱，対にした差，判定の規則，退行の確認．"""

from __future__ import annotations

import numpy as np
import pytest

from atrium.advert_eval import Inbox
from atrium.arrays import F32Array
from atrium.body_advert_eval import (
    embed_bodies,
    judge_body_only,
    judge_h_b1_term_sketch,
    judge_h_b3,
    make_body_inboxes,
    paired_bootstrap_diff,
    surname_in_public_info,
)
from atrium.data_enronqa import HEADER_SEPARATOR


class FakeEncoder:
    """本文の長さを 1 次元目に入れるだけの偽の埋め込み器（呼ばれた文章を記録する）．"""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode_texts(self, texts: list[str]) -> F32Array:
        self.seen.extend(texts)
        return np.asarray([[float(len(t)), 1.0] for t in texts], dtype=np.float32)


def email(subject: str, body: str, sender: str = "a.b@enron.com") -> str:
    return f"Subject: {subject}\nSender: {sender}\n{HEADER_SEPARATOR}\n{body}"


def make_inbox(name: str, texts: list[str]) -> Inbox:
    return Inbox(
        name=name,
        paths=[f"{name}/{i}" for i in range(len(texts))],
        titles=[f"title {i}" for i in range(len(texts))],
        texts=texts,
        emb=np.ones((len(texts), 2), np.float32),
    )


def test_bodies_exclude_the_header_and_empty_bodies_are_counted_and_dropped() -> None:
    inbox = make_inbox(
        "lay-k", [email("S1", "hello world"), email("S2", "   "), "no separator here"]
    )
    encoder = FakeEncoder()
    embeddings, keep, counts = embed_bodies([inbox], encoder)
    assert keep == [[0, 2]]
    assert counts == {"n_emails": 3, "n_no_separator": 1, "n_empty_body": 1}
    assert embeddings[0].shape == (2, 2)
    assert all("Subject" not in t and "Sender" not in t for t in encoder.seen[:1])
    assert encoder.seen[1] == "no separator here"


def test_body_embeddings_are_rounded_through_float16() -> None:
    class Precise(FakeEncoder):
        def encode_texts(self, texts: list[str]) -> F32Array:
            return np.full((len(texts), 1), 0.1234567, dtype=np.float32)

    embeddings, _, _ = embed_bodies([make_inbox("lay-k", [email("S", "body")])], Precise())
    assert embeddings[0][0, 0] == np.float32(np.float16(0.1234567))
    assert embeddings[0][0, 0] != np.float32(0.1234567)


def test_body_inbox_keeps_titles_and_holds_only_bodies_and_their_embeddings() -> None:
    inbox = make_inbox("lay-k", [email("S1", "one"), email("S2", ""), email("S3", "three")])
    embeddings, keep, _ = embed_bodies([inbox], FakeEncoder())
    (body_inbox,) = make_body_inboxes([inbox], embeddings, keep)
    assert [t.strip() for t in body_inbox.texts] == ["one", "three"]
    assert body_inbox.titles == inbox.titles
    assert body_inbox.emb.shape[0] == len(body_inbox.texts)


def test_embedding_row_mismatch_is_an_error() -> None:
    inbox = make_inbox("lay-k", [email("S1", "one")])
    with pytest.raises(ValueError, match="embeddings"):
        make_body_inboxes([inbox], [np.zeros((2, 2), np.float32)], [[0]])


def test_paired_bootstrap_of_identical_arrays_is_zero_and_unclear() -> None:
    values = np.array([1.0, 0.0, 1.0, 1.0, 0.0] * 20)
    result = paired_bootstrap_diff(values, values, seed=1)
    assert result["diff"] == 0.0
    assert result["ci95"] == (0.0, 0.0)
    assert result["verdict"] == "unclear"


def test_paired_bootstrap_classifies_a_consistent_drop_as_lower_and_a_gain_as_higher() -> None:
    ones, zeros = np.ones(200), np.zeros(200)
    assert paired_bootstrap_diff(zeros, ones, seed=1)["verdict"] == "lower"
    assert paired_bootstrap_diff(ones, zeros, seed=1)["verdict"] == "higher"


def test_paired_bootstrap_is_reproducible_for_a_seed() -> None:
    rng = np.random.default_rng(0)
    a, b = rng.integers(0, 2, 300).astype(float), rng.integers(0, 2, 300).astype(float)
    assert paired_bootstrap_diff(a, b, 7) == paired_bootstrap_diff(a, b, 7)


def test_paired_bootstrap_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        paired_bootstrap_diff(np.ones(3), np.ones(4), seed=1)


def diff(lo: float, hi: float) -> dict[str, object]:
    return {"diff": (lo + hi) / 2, "ci95": (lo, hi), "verdict": "x"}


@pytest.mark.parametrize(
    ("hit_ci", "owner_ci", "verdict"),
    [
        ((-0.02, 0.01), (-0.05, -0.01), "adopt"),
        ((-0.02, 0.01), (-0.05, 0.01), "partial"),
        ((-0.05, -0.02), (-0.05, -0.01), "partial"),
        ((-0.05, -0.02), (-0.05, 0.01), "reject"),
    ],
)
def test_conclusion_needs_non_inferior_hit_and_lower_owner_top1(
    hit_ci: tuple[float, float], owner_ci: tuple[float, float], verdict: str
) -> None:
    assert judge_body_only(diff(*hit_ci), diff(*owner_ci))["verdict"] == verdict


def test_h_b1_term_sketch_needs_overall_drop_and_unclear_outside_group_a() -> None:
    assert judge_h_b1_term_sketch({"verdict": "lower"}, {"verdict": "unclear"})
    assert not judge_h_b1_term_sketch({"verdict": "lower"}, {"verdict": "lower"})
    assert not judge_h_b1_term_sketch({"verdict": "unclear"}, {"verdict": "unclear"})


def test_h_b3_needs_positive_rank_correlation_for_both_hit_and_owner() -> None:
    both = {"spearman_size_hit": 1.0, "spearman_size_owner": 0.9}
    only_hit = {"spearman_size_hit": 1.0, "spearman_size_owner": -0.1}
    assert judge_h_b3({"a": both, "b": both})["verdict"] == "supported"
    assert judge_h_b3({"a": both, "b": only_hit})["verdict"] == "partial"
    assert judge_h_b3({"a": only_hit, "b": only_hit})["verdict"] == "refuted"


def test_owner_surname_counts_in_term_sketch_and_card() -> None:
    texts = [email("Re: lay report", "lay says hello") for _ in range(4)]
    inbox = make_inbox("lay-k", texts)
    inbox = Inbox(inbox.name, inbox.paths, ["lay report"] * 4, inbox.texts, inbox.emb)
    counts = surname_in_public_info([inbox], sketch_size=10, card_subjects=5)
    assert counts == {"term_sketch_top20": 1, "term_sketch_all": 1, "card_sim": 1}
