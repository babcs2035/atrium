"""probe の 2 段目の得点（vote・learned．d0009 B3）の仕様：標準化・投票の計算・学習の重みの読み書き・設定の検査・probe の応答．"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from atrium.arrays import F64Array
from atrium.config import AtriumConfig
from atrium.owner_score import (
    LEARNED_FEATURES,
    LearnedRanker,
    learned_features,
    load_ranker,
    masked_zscore,
    vote_aggregate,
    vote_scores,
)
from atrium.routing.summary import Bm25ProbeRouter
from tests.conftest import with_experiment
from tests.test_bm25_probe import (
    BM25_WEIGHT,
    ENCODER,
    _artifact_without_files,
    _mail,
    _node_client,
    _write_mails,
)
from tests.test_frontier import DIM

NAN = float("nan")


def _mask(*rows: list[bool]) -> np.ndarray:
    return np.asarray(rows, dtype=bool)


def test_masked_zscore_standardises_only_the_answered_columns() -> None:
    values = np.array([[1.0, 2.0, 3.0, 99.0]])
    z = masked_zscore(values, _mask([True, True, True, False]))
    assert z[0, :3].tolist() == pytest.approx([-1.2247449, 0.0, 1.2247449])
    assert z[0, 3] == 0.0


def test_vote_with_k_one_and_sum_ranks_like_the_maximum_based_score() -> None:
    """k = 1 の sum は，最大の得点を標準化したものと同じ並び（今の CombMAX と同じ選択になる）．"""
    rng = np.random.default_rng(1)
    dense = -np.sort(-rng.normal(size=(5, 8, 5)), axis=2)
    bm25 = -np.sort(-rng.normal(size=(5, 8, 5)) * 3 + 10, axis=2)
    mask = rng.random((5, 8)) > 0.2
    expected = np.where(
        mask,
        masked_zscore(dense[:, :, 0], mask) + 2.0 * masked_zscore(bm25[:, :, 0], mask),
        -np.inf,
    )
    got = vote_scores(dense, bm25, mask, "sum", 1, 2.0)
    finite = mask & np.isfinite(got)
    assert np.allclose(got[finite], expected[finite], atol=1e-6)
    assert (got[~mask] == -np.inf).all()


def test_vote_standardises_the_top_k_scores_of_all_answered_inboxes_together() -> None:
    """標準化の平均・母標準偏差は，1 問の中で答えた全受信箱の上位 k 個をまとめて求める（受信箱ごとではない）．"""
    top = np.array([[[3.0, 1.0], [2.0, 0.0]]])  # 1 問・2 受信箱・上位 2 個
    mask = _mask([True, True])
    mean, std = 1.5, np.sqrt(1.25)
    z = (top - mean) / (std + 1e-9)
    assert vote_aggregate(top, mask, "sum", 2)[0].tolist() == pytest.approx(
        z.sum(axis=2)[0].tolist()
    )
    assert vote_aggregate(top, mask, "expsum", 2)[0].tolist() == pytest.approx(
        np.exp(z).sum(axis=2)[0].tolist()
    )
    # k = 1 なら各受信箱の最大 3.0・2.0 だけで標準化する
    z1 = (np.array([3.0, 2.0]) - 2.5) / (0.5 + 1e-9)
    assert vote_aggregate(top, mask, "sum", 1)[0].tolist() == pytest.approx(z1.tolist())


def test_vote_ignores_missing_scores_and_unanswered_inboxes() -> None:
    """文書が k 個に満たない受信箱の NaN と，答えなかった受信箱は，標準化にも合計にも入らない．"""
    top = np.array([[[3.0, NAN], [2.0, 1.0], [100.0, 90.0]]])
    mask = _mask([True, True, False])
    agg = vote_aggregate(top, mask, "sum", 2)[0]
    pooled = np.array([3.0, 2.0, 1.0])
    z = (pooled - pooled.mean()) / (pooled.std() + 1e-9)
    assert agg[0] == pytest.approx(z[0])
    assert agg[1] == pytest.approx(z[1] + z[2])
    assert agg[2] == 0.0


def test_learned_features_are_standardised_among_the_answered_inboxes_in_a_fixed_order() -> None:
    rng = np.random.default_rng(2)
    dense = -np.sort(-rng.normal(size=(2, 6, 5)), axis=2)
    bm25 = -np.sort(-rng.normal(size=(2, 6, 5)), axis=2)
    mask = np.ones((2, 6), dtype=bool)
    first = rng.normal(size=(2, 6))
    feats = learned_features(dense, bm25, mask, first)
    assert feats.shape == (2, 6, len(LEARNED_FEATURES))
    assert np.allclose(feats.mean(axis=1), 0.0, atol=1e-9)
    # 最後の特徴量は 1 段目の要約の得点の標準化
    assert np.allclose(feats[:, :, -1], masked_zscore(first, mask))
    # 最初の特徴量は密ベクトルの最大の標準化
    assert np.allclose(feats[:, :, 0], masked_zscore(dense[:, :, 0], mask))


def test_the_ranker_json_round_trips_and_rejects_a_different_feature_order(tmp_path: Path) -> None:
    ranker = LearnedRanker(coef=tuple(float(i) for i in range(7)), intercept=-3.0)
    path = tmp_path / "owner_ranker.json"
    ranker.write(path, {"trained_on": "dev"})
    assert load_ranker(path) == ranker
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["features"] = list(reversed(payload["features"]))
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="features"):
        load_ranker(path)


def test_ranker_scores_are_minus_infinity_for_unanswered_inboxes() -> None:
    feats: F64Array = np.ones((1, 3, 7))
    scores = LearnedRanker(coef=(1.0,) * 7, intercept=0.0).scores(feats, _mask([True, False, True]))
    assert scores[0].tolist() == [7.0, -np.inf, 7.0]


# ── 設定 ─────────────────────────────────────────────────────────────────────


def test_the_default_config_keeps_the_current_owner_score(cfg: AtriumConfig) -> None:
    assert cfg.routing.owner_score == "fused_max"
    assert cfg.routing.owner_score_k == 1
    assert cfg.routing.owner_ranker_path is None


def test_a_non_default_owner_score_requires_bm25_probe(cfg: AtriumConfig) -> None:
    routing = cfg.routing.model_copy(update={"owner_score": "vote"})
    with pytest.raises(ValueError, match="requires experiment.routing=bm25_probe"):
        AtriumConfig.model_validate({**cfg.model_dump(), "routing": routing.model_dump()})
    ok = with_experiment(cfg, dataset="enronqa", routing="bm25_probe", answer_mode="owner_answer")
    AtriumConfig.model_validate({**ok.model_dump(), "routing": routing.model_dump()})


def test_learned_owner_score_requires_the_ranker_file_name(cfg: AtriumConfig) -> None:
    ok = with_experiment(cfg, dataset="enronqa", routing="bm25_probe", answer_mode="owner_answer")
    routing = cfg.routing.model_copy(update={"owner_score": "learned"})
    with pytest.raises(ValueError, match="owner_ranker_path"):
        AtriumConfig.model_validate({**ok.model_dump(), "routing": routing.model_dump()})


# ── Router の vote・learned ───────────────────────────────────────────────────


def test_router_vote_picks_the_inbox_with_many_good_documents_not_one_lucky_maximum() -> None:
    """a は 1 通だけ非常に高く（最大が高い），b は上位 3 通がどれも高い．最大なら a，k = 3 の sum の投票なら b．"""
    artifact = _artifact_without_files()
    dense = {"a": 0.9, "b": 0.8, "c": 0.1}
    bm25 = {"a": 9.0, "b": 8.0, "c": 1.0}
    dense_topk = {"a": [0.9, 0.1, 0.1], "b": [0.8, 0.79, 0.78], "c": [0.1, 0.1, 0.1]}
    bm25_topk = {"a": [9.0, 1.0, 1.0], "b": [8.0, 7.9, 7.8], "c": [1.0, 1.0, 1.0]}
    peak = Bm25ProbeRouter(1, 2, artifact, ENCODER, 0.5, BM25_WEIGHT)
    assert peak.topk is None
    assert peak.select_among(["a", "b", "c"], dense, bm25) == ["a"]
    vote = Bm25ProbeRouter(1, 2, artifact, ENCODER, 0.5, BM25_WEIGHT, "vote", "sum", 3)
    assert vote.topk == 3
    assert vote.select_among(["a", "b", "c"], dense, bm25, dense_topk, bm25_topk) == ["b"]


def test_router_vote_needs_the_top_k_scores() -> None:
    vote = Bm25ProbeRouter(
        1, 2, _artifact_without_files(), ENCODER, 0.5, BM25_WEIGHT, "vote", "sum", 3
    )
    with pytest.raises(ValueError, match="top-k"):
        vote.select_among(["a"], {"a": 1.0}, {"a": 1.0})


def test_router_learned_needs_the_ranker_and_uses_the_first_stage_score() -> None:
    artifact = _artifact_without_files()
    with pytest.raises(ValueError, match="ranker"):
        Bm25ProbeRouter(1, 2, artifact, ENCODER, 0.5, BM25_WEIGHT, "learned")
    only_first_stage = LearnedRanker(coef=(0, 0, 0, 0, 0, 0, 1.0), intercept=0.0)
    router = Bm25ProbeRouter(
        1, 2, artifact, ENCODER, 0.5, BM25_WEIGHT, "learned", ranker=only_first_stage
    )
    assert router.topk == 5
    top = {c: [0.5] * 5 for c in "abc"}
    chosen = router.select_among(
        ["a", "b", "c"],
        {"a": 0.5, "b": 0.5, "c": 0.5},
        {"a": 1.0, "b": 1.0, "c": 1.0},
        top,
        top,
        {"a": 0.1, "b": 0.9, "c": 0.5},
    )
    assert chosen == ["b"]


# ── node の probe の応答 ─────────────────────────────────────────────────────


async def test_probe_without_topk_keeps_the_response_body_of_the_previous_version(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    shard_dir = _write_mails(tmp_path, "inbox", [_mail("a", "alpha beta"), _mail("b", "gamma")])
    body = {
        "shard_ids": ["inbox"],
        "embedding": [0.1] * DIM,
        "bm25": {"idf": {"alpha": 2.0}, "avg_doc_len": 2.0},
    }
    async with _node_client(cfg, shard_dir, True) as client:
        plain = (await client.post("/v1/probe", json=body)).json()
    assert sorted(plain) == ["bm25", "duration_s", "node_id", "scores"]


async def test_probe_with_topk_returns_descending_scores_whose_first_value_is_the_maximum(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    mails = [_mail("a", "alpha beta"), _mail("b", "alpha alpha"), _mail("c", "gamma")]
    shard_dir = _write_mails(tmp_path, "inbox", mails)
    body = {
        "shard_ids": ["inbox"],
        "embedding": [0.1] * DIM,
        "bm25": {"idf": {"alpha": 2.0}, "avg_doc_len": 2.0},
    }
    async with _node_client(cfg, shard_dir, True) as client:
        plain = (await client.post("/v1/probe", json=body)).json()
        topk = (await client.post("/v1/probe", json={**body, "topk": 2})).json()
        wide = (await client.post("/v1/probe", json={**body, "topk": 5})).json()
        no_bm25 = await client.post(
            "/v1/probe", json={**{k: body[k] for k in ("shard_ids", "embedding")}, "topk": 2}
        )
    assert topk["scores"] == plain["scores"] and topk["bm25"] == plain["bm25"]
    dense, bm25 = topk["dense_topk"]["inbox"], topk["bm25_topk"]["inbox"]
    assert len(dense) == len(bm25) == 2
    assert dense == sorted(dense, reverse=True) and bm25 == sorted(bm25, reverse=True)
    assert dense[0] == plain["scores"]["inbox"] and bm25[0] == plain["bm25"]["inbox"]
    # 文書が 3 通しかない受信箱は，k = 5 でも 3 個
    assert len(wide["dense_topk"]["inbox"]) == len(wide["bm25_topk"]["inbox"]) == 3
    assert no_bm25.status_code == 400
