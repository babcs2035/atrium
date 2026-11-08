"""probe の 2 段目の得点の作り方（vote・learned）のオフラインの評価（d0009 B3．Iteration 24．`atrium owner-score-eval`）．

実機のノードは使わない．メールの本文・埋め込みのある環境（gpu4 の artifacts/ など）で動かす．質問ごと・受信箱ごとの
密ベクトルの内積・BM25（全体で共有した published の IDF）の上位 K = 5 個の得点を求め（`frontier_methods` の BM25 の索引と同じ重み），
atrium.owner_score の得点を 13 通り（V 12 通り・L 1 通り）と基準（今の CombMAX）で評価する．

1. dev だけで 13 通りの hit@3 を出し，事前登録の規則で 1 つ選ぶ（G-D：選んだものが基準より dev で +0.005 以上）．H・H2 は見ない．
2. G-D を通ったときだけ，選んだ得点と基準を H（holdout）で比べる（G-H：hit@3 の差 ≥ +0.010 かつ対応ありのブートストラップの 95% 区間の下端 > 0）．
3. G-R：基準の選択が offline_selection.json の `bm25_probe_m3`（test）・Iteration 22 の P1 の候補（H）と一致すること．
4. 実機の照合（G-S）に使う，H ∪ H2 の選択と得点（`offline_selection_q.json`・`offline_scores_q.npz`）を書く．

選び方（dev の hit@3 が最大のもの．最大との差が 0.002 以内なら V を L より，小さい k を，wb = 2 に近いものを優先）は
`select_by_rule`．統計は既存の実装（`atrium.verdict.paired_bootstrap_ci`）を使う．
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from atrium.advert_eval import QuerySet, score_flood_dense
from atrium.arrays import F32Array, F64Array, I64Array
from atrium.config import AtriumConfig
from atrium.data_enronqa import QUESTION_FILES
from atrium.device_probe_eval import device_index_of_inbox
from atrium.frontier_core import BoolMat, FrontierData, attribution_scores
from atrium.frontier_eval import EnronqaBase
from atrium.frontier_methods import (
    bm25_idf,
    bm25_index,
    bm25_scores,
    build_bm25_probe,
    hybrid_first_stage,
    probe_docs,
    top_devices,
    zscore_masked,
)
from atrium.manifest import Placement
from atrium.owner_score import (
    LEARNED_FEATURES,
    LEARNED_K,
    AggName,
    LearnedRanker,
    learned_features,
    vote_scores,
)
from atrium.paths import DatasetPaths
from atrium.routing.advert import top_m
from atrium.verdict import paired_bootstrap_ci

logger = logging.getLogger(__name__)

# 上位の得点を何個まで持つか（V の k ∈ {3, 5}・L の sum@5 の最大）
TOP_K = LEARNED_K
V_AGGS: tuple[AggName, ...] = ("sum", "expsum")
V_KS = (3, 5)
V_WBS = (1.0, 2.0, 3.0)
BASELINE_WB = 2.0
# 密ベクトル・BM25 の得点の行列を一度に作る質問の数
QUERY_CHUNK = 256
# 事前登録の選び方：最大との差がこれ以内なら費用・説明のしやすさで優先順位を付ける
TIE_TOLERANCE = 0.002
# G-D：選んだものが基準より dev の hit@3 で上がっていなければならない幅
G_D_MIN_GAIN = 0.005
# G-H：H の hit@3 の増分の下限
G_H_MIN_GAIN = 0.010
HIT_K = 3
# L：ロジスティック回帰（L2）の C・種・反復の上限・交差検証の分割数
LR_C = 1.0
LR_SEED = 12
LR_MAX_ITER = 1000
CV_FOLDS = 5
# G-S の同点を許す幅（選んだ受信箱のオフラインの得点が，m 番目に高い得点からこの幅以内）
TIE_ABS_TOL = 1e-4
# G-P：基準（bm25_probe・D = 24・published・wb = 2）の公開情報からの持ち主の推定（小数 3 桁）と公開情報のバイト数（journal Iteration 24 の事前登録）
G_P_TOP1 = 0.726
G_P_AUC = 0.975
G_P_ADVERT_BYTES = 29_727_744
# G-R の対象：test の offline_selection.json の鍵
OFFLINE_SELECTION_KEY = "bm25_probe_m3"
OWNER_M = 3
SPLIT_DEV = "dev"


@dataclass(frozen=True)
class ScoreSpec:
    """得点の作り方 1 通り．family は V（投票）・L（学習）・B（基準．今の CombMAX）．"""

    family: str
    agg: AggName | None = None
    k: int | None = None
    wb: float | None = None

    @property
    def name(self) -> str:
        """結果の鍵になる名前．"""
        if self.family == "V":
            return f"V_{self.agg}_k{self.k}_wb{self.wb:g}"
        return "L" if self.family == "L" else "B_fused_max"


CANDIDATES: list[ScoreSpec] = [
    ScoreSpec("V", agg, k, wb) for agg in V_AGGS for k in V_KS for wb in V_WBS
] + [ScoreSpec("L")]
BASELINE = ScoreSpec("B")


@dataclass
class SplitInputs:
    """1 つの質問集合の評価の入力（質問 × 受信箱）．"""

    qids: list[str]
    gold: I64Array
    mask: BoolMat
    dense_top: F64Array
    bm25_top: F64Array
    first_stage: F64Array
    # 既存の実装（frontier_methods．float32）の基準の得点．実機の基準の得点と同じ関数
    baseline_fused: F32Array


def load_extra_query_set(cfg: AtriumConfig, paths: DatasetPaths, split: str) -> QuerySet:
    """holdout・holdout2 の質問と，そのクエリ埋め込み（queries/<split>/）・正解の受信箱の番号を読む．"""
    from atrium.benchmarks import load_questions

    e = cfg.data.require_enronqa()
    questions = load_questions(paths.questions.parent / QUESTION_FILES[split])
    ids: list[str] = json.loads(paths.split_query_ids(split).read_text(encoding="utf-8"))
    row = {q: i for i, q in enumerate(ids)}
    matrix = np.load(paths.split_query_embeddings(split, e.encoder), mmap_mode="r")
    index = {name: i for i, name in enumerate(e.sources)}
    return QuerySet(
        qids=[q.qid for q in questions],
        texts=[q.question for q in questions],
        emb=np.asarray(matrix[[row[q.qid] for q in questions]], np.float32),
        gold=[index[q.bank] for q in questions],
    )


def segment_topk(matrix: F32Array, starts: I64Array, k: int) -> F32Array:
    """(質問, 文書) の得点を，starts で区切った受信箱ごとに降順の上位 k 個にする (質問, 受信箱, k)．文書が k 個に満たない受信箱は NaN で埋める．"""
    n_docs = matrix.shape[1]
    bounds = [*starts.tolist(), n_docs]
    out = np.full((matrix.shape[0], len(starts), k), np.nan, dtype=np.float32)
    for j in range(len(starts)):
        seg = matrix[:, bounds[j] : bounds[j + 1]]
        width = min(k, seg.shape[1])
        part = np.partition(seg, seg.shape[1] - width, axis=1)[:, seg.shape[1] - width :]
        out[:, j, :width] = -np.sort(-part, axis=1)
    return out


def dense_topk(queries: F32Array, inbox_emb: list[F32Array], k: int) -> F32Array:
    """質問ごと・受信箱ごとの密ベクトルの内積の上位 k 個 (質問, 受信箱, k)（ノードの /v1/probe の dense_topk と同じ定義）．"""
    emb = np.concatenate(inbox_emb)
    starts = np.cumsum([0, *(len(e) for e in inbox_emb[:-1])]).astype(np.int64)
    parts = [
        segment_topk(queries[lo : lo + QUERY_CHUNK] @ emb.T, starts, k)
        for lo in range(0, len(queries), QUERY_CHUNK)
    ]
    return np.concatenate(parts)


def bm25_topk(data: FrontierData, split: str, k: int, n_docs_of_inbox: I64Array) -> F32Array:
    """質問ごと・受信箱ごとの本文の BM25（published の IDF）の上位 k 個 (質問, 受信箱, k)．

    ノードは全文書に得点（質問の語が無ければ 0）を付けて上位を返すので，本文の語が無く索引から外れた文書は 0 点として数える
    （受信箱の文書数が k 個に満たなければ NaN）．
    """
    from scipy.sparse import csr_matrix

    idx = bm25_index(data)
    weights_t = idx["base"].multiply(bm25_idf(data, "published")[None, :]).tocsr().T.tocsr()
    vocab = idx["vocab"]
    tokens = data.q_tokens[split]
    parts = []
    for lo in range(0, len(tokens), QUERY_CHUNK):
        part = tokens[lo : lo + QUERY_CHUNK]
        rows = [i for i, toks in enumerate(part) for t in set(toks) if t in vocab]
        cols = [vocab[t] for toks in part for t in set(toks) if t in vocab]
        q = csr_matrix(
            (np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(len(part), len(vocab))
        )
        parts.append(segment_topk((q @ weights_t).toarray(), idx["starts"], k))
    out = np.concatenate(parts)
    # 索引に無い文書（本文が空）の分を 0 点で補う（密ベクトルの索引の文書数まで）
    for j, n in enumerate(n_docs_of_inbox.tolist()):
        missing = np.isnan(out[:, j, :]) & (np.arange(k)[None, :] < n)
        out[:, j, :][missing] = 0.0
    return out


def build_split_inputs(
    base: EnronqaBase,
    data: FrontierData,
    split: str,
    device_of_source: I64Array,
    depth: int,
    summary_weight: float,
    cache: Path | None,
) -> SplitInputs:
    """質問集合 1 つの評価の入力を作る（上位の得点は cache があれば再利用する）．"""
    qset = base.sets[split]
    dscores, _, _ = hybrid_first_stage(data, 64000, summary_weight)
    hybrid = dscores(split)
    mask = top_devices(hybrid, depth, device_of_source)
    first_stage = hybrid[:, device_of_source].astype(np.float64)
    n_docs = np.asarray([len(ib.emb) for ib in base.inboxes], dtype=np.int64)
    if cache is not None and cache.exists():
        stored = np.load(cache, allow_pickle=False)
        if list(stored["qids"]) != qset.qids:
            raise ValueError(f"{cache} was built for different questions")
        dense, bm25 = stored["dense"], stored["bm25"]
    else:
        dense = dense_topk(qset.emb, [ib.emb for ib in base.inboxes], TOP_K)
        bm25 = bm25_topk(data, split, TOP_K, n_docs)
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez(cache, qids=np.asarray(qset.qids), dense=dense, bm25=bm25)
    baseline = zscore_masked(data.second[split], mask) + BASELINE_WB * zscore_masked(
        bm25_scores(data, split, "published"), mask
    )
    return SplitInputs(
        qids=list(qset.qids),
        gold=np.asarray(qset.gold, dtype=np.int64),
        mask=mask,
        dense_top=dense.astype(np.float64),
        bm25_top=bm25.astype(np.float64),
        first_stage=first_stage,
        baseline_fused=np.where(mask, baseline, -np.inf).astype(np.float32),
    )


def hit_flags(scores: F64Array, gold: I64Array, k: int) -> F64Array:
    """正解の受信箱の順位（それより得点が高いか同点の受信箱の数．同点は不利な側）が k 以内か（1.0・0.0）．得点が −inf の正解は外れ．"""
    gold_score = scores[np.arange(len(gold)), gold]
    rank = (scores >= gold_score[:, None]).sum(axis=1)
    flags: F64Array = ((rank <= k) & np.isfinite(gold_score)).astype(np.float64)
    return flags


def spec_scores(
    spec: ScoreSpec, inputs: SplitInputs, ranker: LearnedRanker | None = None
) -> F64Array:
    """得点の作り方 1 通りの (質問, 受信箱) の得点（probe に答えた受信箱だけ．それ以外は −inf）．"""
    if spec.family == "V":
        assert spec.agg is not None and spec.k is not None and spec.wb is not None
        return vote_scores(
            inputs.dense_top, inputs.bm25_top, inputs.mask, spec.agg, spec.k, spec.wb
        )
    if spec.family == "L":
        if ranker is None:
            raise ValueError("the learned score needs a ranker")
        feats = learned_features(inputs.dense_top, inputs.bm25_top, inputs.mask, inputs.first_stage)
        return ranker.scores(feats, inputs.mask)
    return inputs.baseline_fused.astype(np.float64)


# ── L（学習）──────────────────────────────────────────────────────────────


def training_rows(inputs: SplitInputs, queries: I64Array) -> tuple[F64Array, F64Array]:
    """質問の部分集合の，答えた受信箱ごとの特徴量の行と，持ち主か否か（1・0）．"""
    feats = learned_features(
        inputs.dense_top[queries],
        inputs.bm25_top[queries],
        inputs.mask[queries],
        inputs.first_stage[queries],
    )
    mask = inputs.mask[queries]
    label = np.zeros(mask.shape, dtype=bool)
    label[np.arange(len(queries)), inputs.gold[queries]] = True
    return feats[mask], label[mask].astype(np.float64)


def fit_ranker(inputs: SplitInputs, queries: I64Array) -> LearnedRanker:
    """質問の部分集合でロジスティック回帰（L2．C = LR_C．種 LR_SEED）を学習して重みを返す．"""
    from sklearn.linear_model import LogisticRegression

    x, y = training_rows(inputs, queries)
    model = LogisticRegression(C=LR_C, random_state=LR_SEED, max_iter=LR_MAX_ITER)
    model.fit(x, y)
    return LearnedRanker(
        coef=tuple(float(c) for c in model.coef_[0]), intercept=float(model.intercept_[0])
    )


def cross_validated_scores(inputs: SplitInputs) -> F64Array:
    """dev の L の得点を，受信箱で束ねた 5 分割の交差検証で求める（同じ持ち主の受信箱の質問は学習と評価に跨がらない）．"""
    from sklearn.model_selection import GroupKFold

    n = len(inputs.qids)
    scores = np.full(inputs.mask.shape, -np.inf, dtype=np.float64)
    for train, test in GroupKFold(n_splits=CV_FOLDS).split(np.arange(n), groups=inputs.gold):
        ranker = fit_ranker(inputs, train.astype(np.int64))
        feats = learned_features(
            inputs.dense_top[test],
            inputs.bm25_top[test],
            inputs.mask[test],
            inputs.first_stage[test],
        )
        scores[test] = ranker.scores(feats, inputs.mask[test])
    return scores


# ── 選び方（事前登録）─────────────────────────────────────────────────────


def select_by_rule(dev_hit3: dict[str, float]) -> dict[str, Any]:
    """13 通りの dev の hit@3 から，事前登録の規則で 1 つ選ぶ．

    最大のもの．最大との差が TIE_TOLERANCE 以内の候補が複数あるときは，V を L より，小さい k を，wb = 2 に近いものを優先する
    （順に適用．それでも決まらなければ dev の hit@3 が高い方，次に agg が sum の方）．
    """
    specs = {s.name: s for s in CANDIDATES}
    best = max(dev_hit3[name] for name in specs)
    near = [name for name in specs if dev_hit3[name] >= best - TIE_TOLERANCE]

    def priority(name: str) -> tuple[int, int, float, float, int]:
        s = specs[name]
        return (
            int(s.family == "L"),
            s.k or LEARNED_K,
            abs((s.wb or BASELINE_WB) - BASELINE_WB),
            -dev_hit3[name],
            int(s.agg == "expsum"),
        )

    chosen = min(near, key=priority)
    return {"chosen": chosen, "best_dev_hit3": best, "within_tolerance": sorted(near)}


# ── 選択（実機の照合用）─────────────────────────────────────────────────


def selection_of(names: list[str], scores: F64Array, m: int) -> list[list[str]]:
    """質問ごとの上位 m 個の受信箱（実機の `top_m`．同点は names の先の方）．"""
    return [top_m(names, row.tolist(), m) for row in scores]


def baseline_agreement(
    names: list[str], inputs: SplitInputs, reference: dict[str, list[str]]
) -> dict[str, Any]:
    """基準の選択（既存の実装の得点の上位 3 個）が reference（質問 ID → 受信箱）と全問一致する割合．"""
    chosen = selection_of(names, inputs.baseline_fused.astype(np.float64), OWNER_M)
    same = [
        sorted(c) == sorted(reference[q])
        for q, c in zip(inputs.qids, chosen, strict=True)
        if q in reference
    ]
    return {
        "n": len(same),
        "n_questions": len(inputs.qids),
        "n_identical": int(sum(same)),
        "agree": float(np.mean(same)) if same else None,
    }


def paired_gain(spec_hits: F64Array, base_hits: F64Array) -> dict[str, Any]:
    """hit@3 の差（spec − 基準）と，対応ありのブートストラップ（10,000 回・種 12）の 95% 区間．"""
    diff = spec_hits - base_hits
    low, high = paired_bootstrap_ci(diff)
    return {
        "n": len(diff),
        "spec": float(spec_hits.mean()),
        "baseline": float(base_hits.mean()),
        "gain": float(diff.mean()),
        "ci95": [low, high],
        "n_only_spec": int(((spec_hits == 1) & (base_hits == 0)).sum()),
        "n_only_baseline": int(((spec_hits == 0) & (base_hits == 1)).sum()),
    }


def p1_candidate_sources(run_dir: Path) -> dict[str, list[str]]:
    """実機の実行の質問 ID → 選んだ受信箱（results.jsonl の最後の行）．"""
    out: dict[str, list[str]] = {}
    with (run_dir / "results.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("error") is None:
                out[row["qid"]] = list(row["selected_sources"])
    return out


def run(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    placement: Placement,
    work_dir: Path | None,
    out_dir: Path,
    offline_selection: Path,
    p1_run: Path,
    ranker_out: Path,
) -> dict[str, Any]:
    """dev で得点を選び（G-D），選んだ得点と基準を H で比べ（G-H），G-R を確かめ，H ∪ H2 の選択を書く．結果（owner_score.json の中身）を返す．"""
    base = EnronqaBase(cfg, paths, work_dir)
    for split in ("holdout", "holdout2"):
        qset = load_extra_query_set(cfg, paths, split)
        base.sets[split] = qset
        base.second[split] = score_flood_dense(qset.emb, base.inboxes)
    device_of_source = device_index_of_inbox(base.names, placement)
    data = base.data(device_of_source, "n30")
    depth = cfg.routing.bm25_probe_d["enronqa"]
    weight = cfg.routing.summary_weight
    cache_dir = None if work_dir is None else work_dir / "owner_score"

    def build(split: str) -> SplitInputs:
        cache = None if cache_dir is None else cache_dir / f"topk_{split}.npz"
        return build_split_inputs(base, data, split, device_of_source, depth, weight, cache)

    # ── 1. dev で 13 通りを評価して選ぶ（H・H2 はまだ作らない）──
    dev = build(SPLIT_DEV)
    dev_hits: dict[str, F64Array] = {}
    for spec in CANDIDATES:
        scores = cross_validated_scores(dev) if spec.family == "L" else spec_scores(spec, dev)
        dev_hits[spec.name] = hit_flags(scores, dev.gold, HIT_K)
    dev_base = hit_flags(spec_scores(BASELINE, dev), dev.gold, HIT_K)
    dev_hit3 = {name: float(h.mean()) for name, h in dev_hits.items()}
    chosen = select_by_rule(dev_hit3)
    chosen_spec = {s.name: s for s in CANDIDATES}[chosen["chosen"]]
    dev_gain = dev_hit3[chosen["chosen"]] - float(dev_base.mean())
    g_d = {
        "dev_hit3_by_candidate": dev_hit3,
        "dev_hit3_baseline_fused_max": float(dev_base.mean()),
        "dev_hit1_baseline_fused_max": float(
            hit_flags(spec_scores(BASELINE, dev), dev.gold, 1).mean()
        ),
        "selection": chosen,
        "gain_over_baseline": dev_gain,
        "min_gain": G_D_MIN_GAIN,
        "passed": bool(dev_gain >= G_D_MIN_GAIN),
        "n_dev": len(dev.qids),
        "l_cross_validation": f"GroupKFold({CV_FOLDS}) by the gold inbox",
    }
    # L の重み：全 dev で学習（G-D の判定に関わらず書く．実機に使うのは L が選ばれたときだけ）
    ranker = fit_ranker(dev, np.arange(len(dev.qids), dtype=np.int64))
    ranker_out.parent.mkdir(parents=True, exist_ok=True)
    ranker.write(
        ranker_out,
        {
            "trained_on": f"dev ({len(dev.qids)} questions)",
            "model": f"LogisticRegression(C={LR_C}, random_state={LR_SEED}, penalty=l2)",
            "top_k": LEARNED_K,
            "n_features": len(LEARNED_FEATURES),
        },
    )

    # ── 2. G-R（基準の退行）──
    selections = json.loads(offline_selection.read_text(encoding="utf-8"))[OFFLINE_SELECTION_KEY]
    test_inputs = build_test_baseline(base, data, device_of_source, depth, weight)
    holdout = build("holdout")
    g_r: dict[str, Any] = {
        "test_vs_offline_selection": baseline_agreement(base.names, test_inputs, selections),
        "holdout_vs_p1_candidates": baseline_agreement(
            base.names, holdout, p1_candidate_sources(p1_run)
        ),
        "offline_selection": str(offline_selection),
        "p1_run": str(p1_run),
    }
    g_r["passed"] = bool(
        g_r["test_vs_offline_selection"]["agree"] == 1.0
        and g_r["test_vs_offline_selection"]["n"] == len(test_inputs.qids)
        and g_r["holdout_vs_p1_candidates"]["agree"] == 1.0
    )

    # ── G-P（公開情報．要約は変えないので，基準の bm25_probe の公開情報からの持ち主の推定と公開情報の大きさが以前と同じこと）──
    (baseline_config,) = [
        c
        for c in build_bm25_probe(data)
        if c.params == {"V": 64000, "w": 0.5, "D": depth, "idf": "published", "wb": BASELINE_WB}
    ]
    attribution = attribution_scores(data, baseline_config, probe_docs(data))
    g_p = {
        "attribution_top1": attribution["attribution_top1"],
        "attribution_auc": attribution["attribution_auc"],
        "advert_bytes": baseline_config.advert_bytes,
        "expected": {"top1": G_P_TOP1, "auc": G_P_AUC, "advert_bytes": G_P_ADVERT_BYTES},
        "passed": bool(
            round(attribution["attribution_top1"], 3) == G_P_TOP1
            and round(attribution["attribution_auc"], 3) == G_P_AUC
            and baseline_config.advert_bytes == G_P_ADVERT_BYTES
        ),
    }

    result: dict[str, Any] = {
        "preregistration": "journal Iteration 24 / backlog B85",
        "split_sizes": {"dev": len(dev.qids), "holdout": len(holdout.qids)},
        "G_D": g_d,
        "G_R": g_r,
        "G_P_offline": g_p,
        "learned_ranker": {
            "path": str(ranker_out),
            "coef": dict(zip(LEARNED_FEATURES, ranker.coef, strict=True)),
            "intercept": ranker.intercept,
        },
    }
    if not g_d["passed"]:
        result["G_H"] = {"evaluated": False, "reason": "G-D not passed (H is not looked at)"}
        return result

    # ── 3. G-D を通ったときだけ H を見る（G-H）──
    holdout2 = build("holdout2")
    per_split: dict[str, dict[str, Any]] = {}
    chosen_scores: dict[str, F64Array] = {}
    base_scores_q: dict[str, F64Array] = {}
    for name, inputs in (("holdout", holdout), ("holdout2", holdout2)):
        chosen_scores[name] = spec_scores(chosen_spec, inputs, ranker)
        base_scores_q[name] = spec_scores(BASELINE, inputs)
        per_split[name] = {
            "hit3": paired_gain(
                hit_flags(chosen_scores[name], inputs.gold, HIT_K),
                hit_flags(base_scores_q[name], inputs.gold, HIT_K),
            ),
            "hit1": paired_gain(
                hit_flags(chosen_scores[name], inputs.gold, 1),
                hit_flags(base_scores_q[name], inputs.gold, 1),
            ),
        }
    gain_h = per_split["holdout"]["hit3"]
    result["G_H"] = {
        "evaluated": True,
        "spec": chosen_spec.name,
        "holdout": per_split["holdout"],
        "holdout2_descriptive": per_split["holdout2"],
        "min_gain": G_H_MIN_GAIN,
        "passed": bool(gain_h["gain"] >= G_H_MIN_GAIN and gain_h["ci95"][0] > 0),
    }

    # ── 4. H ∪ H2 の選択と得点（実機との照合 G-S 用）──
    q_inputs = {"holdout": holdout, "holdout2": holdout2}
    names = base.names
    q_selection: dict[str, dict[str, dict[str, list[str]]]] = {"new": {}, "base": {}}
    for split, inputs in q_inputs.items():
        for kind, scores in (("new", chosen_scores[split]), ("base", base_scores_q[split])):
            q_selection[kind][split] = {
                q: sel
                for q, sel in zip(inputs.qids, selection_of(names, scores, OWNER_M), strict=True)
            }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "offline_selection_q.json").write_text(
        json.dumps({"spec": chosen_spec.name, **q_selection}, ensure_ascii=False), encoding="utf-8"
    )
    both = (holdout, holdout2)
    np.savez_compressed(
        out_dir / "offline_scores_q.npz",
        names=np.asarray(names),
        qids=np.asarray([q for inputs in both for q in inputs.qids]),
        gold=np.concatenate([inputs.gold for inputs in both]),
        new=np.concatenate([chosen_scores[s] for s in q_inputs]).astype(np.float32),
        base=np.concatenate([base_scores_q[s] for s in q_inputs]).astype(np.float32),
    )
    return result


def build_test_baseline(
    base: EnronqaBase,
    data: FrontierData,
    device_of_source: I64Array,
    depth: int,
    summary_weight: float,
) -> SplitInputs:
    """test の基準の得点だけの入力（G-R．上位の得点は使わないので空の配列を持つ）．"""
    split = "test"
    qset = base.sets[split]
    dscores, _, _ = hybrid_first_stage(data, 64000, summary_weight)
    mask = top_devices(dscores(split), depth, device_of_source)
    fused = zscore_masked(data.second[split], mask) + BASELINE_WB * zscore_masked(
        bm25_scores(data, split, "published"), mask
    )
    n, n_inbox = mask.shape
    empty = np.zeros((n, n_inbox, 0), dtype=np.float64)
    return SplitInputs(
        qids=list(qset.qids),
        gold=np.asarray(qset.gold, dtype=np.int64),
        mask=mask,
        dense_top=empty,
        bm25_top=empty,
        first_stage=np.zeros((n, n_inbox), dtype=np.float64),
        baseline_fused=np.where(mask, fused, -np.inf).astype(np.float32),
    )
