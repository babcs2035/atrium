"""デバイスが作る公開情報と，質問者が組み立てる要約の成果物（p0007 I7．atrium.device_summary）の仕様．

組み立てた成果物は，オフラインの一括の計算（frontier_methods.cori_weights・source_centers）と同じ値になる．
"""

from __future__ import annotations

import numpy as np
import pytest

from atrium import frontier_methods
from atrium.data_enronqa import HEADER_SEPARATOR
from atrium.device_summary import (
    BODY_CENTROIDS,
    CORI_DF_BASE,
    CORI_DF_SCALE,
    DeviceSummary,
    assemble_artifact,
    body_of,
    filter_terms,
    published_bytes,
    summarize_device,
)
from atrium.frontier_core import FrontierData
from atrium.routing.advert import tokenize
from tests.live_fixtures import HashEmbedder

HEADER = "From: someone\nSubject: s\n" + HEADER_SEPARATOR
# 受信箱 → メール（content）．device_of_source でデバイスにまとめる
INBOXES = {
    "alice": [
        f"{HEADER}\nbudget meeting schedule {i} tomorrow with the finance team about budget"
        for i in range(5)
    ]
    + [f"{HEADER}\n   "],  # 本文が空のメール
    "bob": [f"{HEADER}\nenergy trading desk position {i} report gas pipeline" for i in range(4)],
    "carol": [f"{HEADER}\nlegal contract draft {i} review counsel agreement" for i in range(6)],
    "dave": [f"{HEADER}\nbudget energy legal mixture {i} shared words" for i in range(3)],
}
DEVICE_OF_SOURCE = {"alice": "dev-a", "bob": "dev-a", "carol": "dev-b", "dave": "dev-c"}
TERM_SIZE = 1000
SEED = 12


def _summaries() -> dict[str, DeviceSummary]:
    embedder = HashEmbedder()
    out: dict[str, DeviceSummary] = {}
    for device in sorted(set(DEVICE_OF_SOURCE.values())):
        mine = {i: c for i, c in INBOXES.items() if DEVICE_OF_SOURCE[i] == device}
        out[device] = summarize_device(mine, embedder.encode_texts, TERM_SIZE, BODY_CENTROIDS, SEED)
    return out


def test_constants_and_term_filter_match_the_offline_implementation() -> None:
    assert CORI_DF_BASE == frontier_methods.CORI_DF_BASE
    assert CORI_DF_SCALE == frontier_methods.CORI_DF_SCALE
    assert BODY_CENTROIDS == frontier_methods.BODY_CENTROIDS
    sample = tokenize("The 2024 budget and the energy desk: ab legal agreements, 123 of them")
    assert filter_terms(sample) == frontier_methods.filter_terms(sample)


def test_body_excludes_the_header_and_empty_bodies_are_not_summarized() -> None:
    assert body_of(f"{HEADER}\nhello") == "\nhello"
    assert body_of("no separator here") == "no separator here"
    summary = _summaries()["dev-a"]
    assert summary.n_docs == 5 + 4  # 本文が空の 1 通は数えない
    assert "from" not in summary.terms  # ヘッダーの語は公開しない
    assert summary.terms[0] == "budget" and summary.dfs[0] == 5


def test_published_terms_are_the_top_document_frequencies_limited_to_term_size() -> None:
    embedder = HashEmbedder()
    summary = summarize_device({"alice": INBOXES["alice"]}, embedder.encode_texts, 3, 4, SEED)
    assert len(summary.terms) == 3
    assert summary.dfs == sorted(summary.dfs, reverse=True)
    assert summary.centers["alice"].shape == (4, 8)  # 本文のあるメール 5 通を 4 個の重心にまとめる


def test_assembled_artifact_equals_the_offline_cori_weights_and_centers() -> None:
    summaries = _summaries()
    names = ["alice", "bob", "carol", "dave"]
    artifact = assemble_artifact(names, DEVICE_OF_SOURCE, summaries, TERM_SIZE)

    # オフライン：全メールの語（本文）から一括で計算する（デバイスの番号は names の中で最初に現れた順）
    order = {d: i for i, d in enumerate(dict.fromkeys(DEVICE_OF_SOURCE[n] for n in names))}
    embedder = HashEmbedder()
    bodies = {n: [b for b in (body_of(c) for c in INBOXES[n]) if b.strip()] for n in names}
    data = FrontierData(
        dataset="enronqa",
        placement="test",
        names=names,
        device_of_source=np.asarray([order[DEVICE_OF_SOURCE[n]] for n in names]),
        m=2,
        target_hit=0.0,
        second={},
        relevant={},
        q_emb={},
        q_tokens={},
        subsets={},
        seed=SEED,
        summary_emb=[
            embedder.encode_texts(bodies[n]).astype(np.float16).astype(np.float32) for n in names
        ],
        summary_tokens=[[tokenize(b) for b in bodies[n]] for n in names],
    )
    weights, cf = frontier_methods.cori_weights(data, TERM_SIZE)
    offline_vocab = frontier_methods.term_index(data)["vocab"]
    assert artifact.groups == [["alice", "bob"], ["carol"], ["dave"]]
    for term, j_live in artifact.vocab.items():
        j_off = offline_vocab[term]
        assert artifact.cf[j_live] == cf[j_off]
        for dev in range(len(order)):
            assert artifact.weights[dev, j_live] == pytest.approx(weights[dev, j_off])
    # 公開されなかった語（どのデバイスにも出ない）は，オフラインでも cf = 0
    assert {t for t, j in offline_vocab.items() if cf[j] > 0} == set(artifact.vocab)
    for live, off in zip(
        artifact.centers, frontier_methods.source_centers(data, "body"), strict=True
    ):
        assert np.allclose(live, off)


def test_a_joining_device_changes_the_weights_of_the_existing_devices() -> None:
    """デバイス数と cf が変わるので，既存のデバイスの重みも作り直される（公開情報は再送しない）．"""
    summaries = _summaries()
    before = assemble_artifact(["alice", "bob", "carol"], DEVICE_OF_SOURCE, summaries, TERM_SIZE)
    after = assemble_artifact(
        ["alice", "bob", "carol", "dave"], DEVICE_OF_SOURCE, summaries, TERM_SIZE
    )
    j_before = before.vocab["budget"]
    j_after = after.vocab["budget"]
    assert before.weights[0, j_before] != pytest.approx(after.weights[0, j_after])
    assert after.cf[j_after] == before.cf[j_before] + 1  # dave も budget を公開した


def test_published_size_counts_term_pairs_and_float32_centers() -> None:
    summary = _summaries()["dev-b"]
    centers = sum(c.shape[0] * c.shape[1] * 4 for c in summary.centers.values())
    assert published_bytes(summary) == len(summary.terms) * 8 + centers
