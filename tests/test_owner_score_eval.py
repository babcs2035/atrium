"""owner-score-eval（d0009 B3）の仕様：事前登録の選び方・上位の得点の作り方・束ねた交差検証・同点の扱い・実機の選択との一致．"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np
import pytest

from atrium.arrays import I64Array
from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.frontier_methods import build_bm25_probe, hybrid_first_stage
from atrium.owner_score import LEARNED_FEATURES, LearnedRanker, masked_zscore
from atrium.owner_score_eval import (
    CANDIDATES,
    ScoreSpec,
    SplitInputs,
    bm25_topk,
    cross_validated_scores,
    dense_topk,
    hit_flags,
    paired_gain,
    segment_topk,
    select_by_rule,
    selection_of,
    spec_scores,
)
from atrium.routing.advert import top_m
from atrium.routing.summary import Bm25ProbeRouter
from tests.conftest import with_experiment
from tests.test_bm25_probe import (
    BM25_WEIGHT,
    ENCODER,
    _artifact,
    _requester_with_one_node_per_device,
)
from tests.test_frontier import DIM, _data

NAN = float("nan")


def _dev_hit3(default: float = 0.90, **overrides: float) -> dict[str, float]:
    """13 通りの dev の hit@3（default で埋め，overrides の名前だけ上書きする）．"""
    out = {s.name: default for s in CANDIDATES}
    out.update(overrides)
    return out


# ── 事前登録の選び方 ─────────────────────────────────────────────────────────


def test_the_candidate_with_the_best_dev_hit3_is_chosen_when_the_others_are_clearly_lower() -> None:
    chosen = select_by_rule(_dev_hit3(0.90, V_expsum_k5_wb3=0.95))
    assert chosen["chosen"] == "V_expsum_k5_wb3"
    assert chosen["best_dev_hit3"] == 0.95


def test_ties_within_0_002_prefer_v_over_l_then_smaller_k_then_wb_near_2() -> None:
    assert len(CANDIDATES) == 13
    # L が最大でも，差が 0.002 以内の V があれば V
    assert select_by_rule(_dev_hit3(0.90, L=0.930, V_sum_k5_wb1=0.929))["chosen"] == "V_sum_k5_wb1"
    # V の間では小さい k
    near = _dev_hit3(0.90, V_sum_k5_wb2=0.930, V_sum_k3_wb3=0.929)
    assert select_by_rule(near)["chosen"] == "V_sum_k3_wb3"
    # k が同じなら wb = 2 に近いもの
    near_wb = _dev_hit3(0.90, V_sum_k3_wb1=0.930, V_sum_k3_wb2=0.929, V_sum_k3_wb3=0.930)
    assert select_by_rule(near_wb)["chosen"] == "V_sum_k3_wb2"
    # 差が 0.002 を超えれば，優先順位ではなく最大
    far = _dev_hit3(0.90, L=0.930, V_sum_k3_wb2=0.9275)
    assert select_by_rule(far)["chosen"] == "L"


# ── 上位の得点 ───────────────────────────────────────────────────────────────


def test_segment_topk_returns_the_descending_top_k_of_every_inbox_and_pads_short_inboxes() -> None:
    matrix = np.array([[5.0, 1.0, 9.0, 4.0, 7.0, 2.0, 8.0]], dtype=np.float32)
    starts = np.array([0, 3, 5], dtype=np.int64)  # 受信箱の文書数 3・2・2
    top = segment_topk(matrix, starts, 3)
    assert top[0, 0].tolist() == [9.0, 5.0, 1.0]
    assert top[0, 1, :2].tolist() == [7.0, 4.0] and np.isnan(top[0, 1, 2])
    assert top[0, 2, :2].tolist() == [8.0, 2.0] and np.isnan(top[0, 2, 2])


def test_dense_topk_equals_the_sorted_inner_products_per_inbox() -> None:
    rng = np.random.default_rng(3)
    inboxes = [rng.normal(size=(n, DIM)).astype(np.float32) for n in (6, 4)]
    queries = rng.normal(size=(2, DIM)).astype(np.float32)
    top = dense_topk(queries, inboxes, 3)
    for j, emb in enumerate(inboxes):
        expected = -np.sort(-(queries @ emb.T), axis=1)[:, :3]
        assert np.allclose(top[:, j, :], expected)


# ── hit・差 ─────────────────────────────────────────────────────────────────


def test_hit_flags_count_ties_against_the_gold_and_miss_an_unanswered_gold() -> None:
    scores = np.array(
        [
            [3.0, 2.0, 2.0, 1.0],  # 正解 2：同点の 1 と自分で順位 3（同点は不利な側）
            [3.0, 2.0, 1.0, 0.5],  # 正解 3：順位 4
            [-np.inf, 5.0, 4.0, 3.0],  # 正解 0：答えなかった（−inf）
        ]
    )
    gold = np.array([2, 3, 0], dtype=np.int64)
    assert hit_flags(scores, gold, 3).tolist() == [1.0, 0.0, 0.0]
    assert hit_flags(scores, gold, 2).tolist() == [0.0, 0.0, 0.0]


def test_paired_gain_reports_the_difference_and_the_discordant_counts() -> None:
    spec = np.array([1.0, 1.0, 1.0, 0.0] * 50)
    base = np.array([1.0, 0.0, 0.0, 0.0] * 50)
    gain = paired_gain(spec, base)
    assert gain["gain"] == pytest.approx(0.5)
    assert (gain["n_only_spec"], gain["n_only_baseline"]) == (100, 0)
    assert gain["ci95"][0] > 0.4


# ── 入力の組み立て ───────────────────────────────────────────────────────────


def _inputs(n_q: int = 40, n_inbox: int = 12, seed: int = 4) -> SplitInputs:
    """答えた受信箱が質問ごとに違う合成の入力．正解の受信箱は上位の得点が高い（学習で拾える信号）．"""
    rng = np.random.default_rng(seed)
    gold: I64Array = rng.integers(0, n_inbox, size=n_q).astype(np.int64)
    dense = rng.normal(size=(n_q, n_inbox, 5))
    bm25 = rng.normal(size=(n_q, n_inbox, 5)) + 5
    dense[np.arange(n_q), gold] += 1.5
    bm25[np.arange(n_q), gold] += 2.0
    mask = rng.random((n_q, n_inbox)) > 0.25
    mask[np.arange(n_q), gold] = True
    dense = -np.sort(-dense, axis=2)
    bm25 = -np.sort(-bm25, axis=2)
    first = rng.normal(size=(n_q, n_inbox))
    base_fused = np.where(
        mask,
        masked_zscore(dense[:, :, 0], mask) + 2.0 * masked_zscore(bm25[:, :, 0], mask),
        -np.inf,
    )
    return SplitInputs(
        qids=[f"q{i}" for i in range(n_q)],
        gold=gold,
        mask=mask,
        dense_top=dense,
        bm25_top=bm25,
        first_stage=first,
        baseline_fused=base_fused.astype(np.float32),
    )


def test_k_one_sum_with_wb_two_selects_the_same_inboxes_as_the_baseline() -> None:
    inputs = _inputs()
    names = [f"inbox{i}" for i in range(12)]
    same = spec_scores(ScoreSpec("V", "sum", 1, 2.0), inputs)
    assert selection_of(names, same, 3) == selection_of(
        names, inputs.baseline_fused.astype(np.float64), 3
    )


def test_cross_validation_never_puts_the_same_gold_inbox_in_training_and_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import atrium.owner_score_eval as module

    inputs = _inputs(n_q=60)
    seen: list[tuple[set[int], set[int]]] = []
    real_fit = module.fit_ranker

    def spy(inp: SplitInputs, queries: I64Array) -> LearnedRanker:
        seen.append((set(inp.gold[queries].tolist()), set()))
        return real_fit(inp, queries)

    monkeypatch.setattr(module, "fit_ranker", spy)
    scores = cross_validated_scores(inputs)
    assert len(seen) == 5
    # 5 回の評価の質問の持ち主の和が全質問・各持ち主はちょうど 1 回の評価にだけ入る
    evaluated = [
        {g for g in set(inputs.gold.tolist()) if g not in train_owners} for train_owners, _ in seen
    ]
    assert sorted(g for owners in evaluated for g in owners) == sorted(set(inputs.gold.tolist()))
    # 評価した質問はどれも −inf でない得点を持つ（交差検証の外の質問は無い）
    assert np.isfinite(scores[inputs.mask]).all()


def test_learned_scores_use_the_weights_and_pick_up_the_signal_better_than_chance() -> None:
    inputs = _inputs(n_q=200)
    cv = cross_validated_scores(inputs)
    hit = hit_flags(cv, inputs.gold, 3).mean()
    chance = 3 / inputs.mask.sum(axis=1).mean()
    assert hit > 2 * chance
    only_dense = LearnedRanker(coef=(1.0, 0, 0, 0, 0, 0, 0), intercept=0.0)
    scores = spec_scores(ScoreSpec("L"), inputs, only_dense)
    expected = np.where(inputs.mask, masked_zscore(inputs.dense_top[:, :, 0], inputs.mask), -np.inf)
    assert np.allclose(scores[inputs.mask], expected[inputs.mask])
    assert len(LEARNED_FEATURES) == 7


# ── 実機の選択との一致（合成データ）──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("owner_score", "agg", "k"), [("vote", "sum", 3), ("vote", "expsum", 5), ("learned", "sum", 1)]
)
async def test_live_selection_equals_the_offline_selection_of_the_same_score(
    cfg: AtriumConfig, tmp_path: Path, owner_score: str, agg: str, k: int
) -> None:
    """ノードが返す上位の得点から質問者が選ぶ受信箱が，オフライン（owner_score_eval の上位の得点）の選択と一致する．"""
    data = _data()
    n_q = len(data.q_tokens["test"])
    q_emb = np.random.default_rng(7).normal(size=(n_q, DIM)).astype(np.float32)
    # 実機の密ベクトルの得点（fp16 に丸めた埋め込みとの内積）と同じ定義にする
    rounded = [e.astype(np.float16).astype(np.float32) for e in data.summary_emb or []]
    second = np.stack([(q_emb @ e.T).max(axis=1) for e in rounded], axis=1)
    data = dataclasses.replace(
        data, q_emb={"dev": q_emb, "test": q_emb}, second={"dev": second, "test": second}
    )
    depth = 2
    _, artifact = _artifact(tmp_path / "summary", data)
    ranker = LearnedRanker(coef=(0.3, 1.0, -0.2, 0.7, 0.1, 0.4, 0.5), intercept=0.0)
    router = Bm25ProbeRouter(
        3,
        depth,
        artifact,
        ENCODER,
        0.5,
        BM25_WEIGHT,
        owner_score,  # type: ignore[arg-type]
        agg,  # type: ignore[arg-type]
        k,
        ranker if owner_score == "learned" else None,
    )

    # オフライン：1 段目の上位 depth 台に載る受信箱の，上位の得点から作った得点
    (offline,) = [
        c
        for c in build_bm25_probe(data)
        if c.params == {"V": 64000, "w": 0.5, "D": depth, "idf": "published", "wb": 2.0}
    ]
    mask = offline.select("test").candidates
    assert mask is not None
    n_docs = np.asarray([len(e) for e in rounded], dtype=np.int64)
    inputs = SplitInputs(
        qids=[f"enron/q{q}" for q in range(n_q)],
        gold=np.zeros(n_q, dtype=np.int64),
        mask=mask,
        dense_top=dense_topk(q_emb, rounded, 5).astype(np.float64),
        bm25_top=bm25_topk(data, "test", 5, n_docs).astype(np.float64),
        first_stage=np.zeros((n_q, 6)),
        baseline_fused=np.zeros((n_q, 6), dtype=np.float32),
    )
    first_stage, _, _ = hybrid_first_stage(data, 64000, 0.5)
    inputs.first_stage[:] = first_stage("test")[:, data.device_of_source]
    spec = ScoreSpec("V", agg, k, BM25_WEIGHT) if owner_score == "vote" else ScoreSpec("L")  # type: ignore[arg-type]
    expected = [top_m(data.names, row.tolist(), 3) for row in spec_scores(spec, inputs, ranker)]

    questions = [
        Question(f"enron/q{q}", "enron", " ".join(data.q_tokens["test"][q]), {}, None)
        for q in range(n_q)
    ]
    requester = await _requester_with_one_node_per_device(
        with_experiment(cfg, dataset="enronqa", answer_mode="retrieval_only"),
        tmp_path / "shards",
        data,
        router,
        {q.qid: q_emb[i] for i, q in enumerate(questions)},
    )
    for i, question in enumerate(questions):
        record = await requester.process(question)
        assert record["error"] is None
        assert sorted(record["selected_sources"]) == sorted(expected[i]), f"question {i}"
