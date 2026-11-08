"""保存した 3 候補から最終回答を選ぶ規則 S1〜S4 のオフラインの評価（d0009 B3．Iteration 25．`atrium answer-select-eval`）．

実機は使わない．入力は `answer.save_candidates: true` の実行（results.jsonl の `owner_answer.candidates`），候補ごとの判定
（candidate_judgements.jsonl．`atrium judge --with-candidates`），R3′ の特徴量（ce_features.jsonl．`atrium select-features`）と
回答を入れた cross-encoder の得点（ce_answer_features.jsonl．`select-features --answer-aware`）．

| 記号 | 中身 | 学習 |
|---|---|---|
| 基準 R3′ | `ce_max`（質問と文脈の上位 5 通の関連度の最大．`atrium.select_eval` の R3 を UNKNOWN の除外なしで） | なし |
| S1 | `ce_qa_max`（質問と回答をつないだ文と，文脈の上位 5 通の関連度の最大） | なし |
| S2 | `ce_ans`（質問と回答の組の関連度） | なし |
| S3 | 候補が正解か否かのロジスティック回帰．特徴量 10 個（下の `S3_FEATURES`） | dev |
| S4 | S3 の特徴量に `ce_qa_max`・`ce_ans` を足した 12 個 | dev |
| V1 | 検証の LLM の得点 `v`（`atrium verify-features`）の最大．差 ≤ 1e-6 は同点で `ce_max` の大きい方（Iteration 28） | なし |
| V2 | S3 の 10 特徴量に 1 問の中で標準化した `v` を足した 11 個のロジスティック回帰（Iteration 28） | dev |

特徴量は 1 問の 3 候補の中で標準化する（平均・母標準偏差．分母に 1e-9．特徴量の無い候補は 0）．「書かれていない」型の表現は
標準化しない 0/1．一致は他の候補との語の集合の F1 の最大（正規化は `atrium.merge.normalize_answer`）．
選ぶ規則：dev の正答率（S3・S4 は受信箱で束ねた 5 分割の交差検証）が最大のもの．最大との差が 0.002 以内なら学習なし（S1・S2）を
学習あり（S3・S4）より，S3 を S4 より優先する．同点（予測・得点の差 ≤ 1e-6）は候補の並びの先の方．

段階（`--stage`）：
- `dev`：dev で S1〜S4 を評価して 1 つ選び（G-D），G-R・G-J2 を確かめ，学習ありなら全 dev で学習し直した重みを書く
  （select_dev.json・answer_selector.json・verdict.json）．H・H2 は見ない．
- `apply`：固定した answer_selector.json を Q（H ∪ H2）に当てはめ，R3′ との差の区間・McNemar・区分・天井を出す
  （answer_select_q.json．`--derived` の実行へ選んだ候補を書き，`fabrication-eval`・`multi_requester_eval.exposure` が読める）．

統計は既存の実装を使う：McNemar の正確検定（`atrium.analysis.mcnemar_exact`），対応ありのブートストラップ区間
（`atrium.verdict.paired_bootstrap_ci`），分散の分解の区間（`atrium.rerun_variance.reread_test_pair`），verdict.json の書式
（`atrium.verdict.write_verdict`）．

`--verify-features`（Iteration 28）を付けると，dev の段階は S1〜S4 ではなく V1・V2（と参照の R3′・S3）を評価し（G-V・G-D・AUROC．
verify_dev.json・answer_verifier_selector.json），apply の段階は固定した answer_verifier_selector.json を `--q-run`・`--extra-run` の順に
並べた `--verify-features` の各ファイルとともに当てはめる．付けないときの挙動・出力は Iteration 25 のまま．

使い方:
    uv run atrium answer-select-eval --stage dev --dev-run results/d0009_b3s_dev --out-dir results/d0009_b3_select
    uv run atrium answer-select-eval --stage apply --out-dir results/d0009_b3_select \\
        --q-run results/d0009_a3_p1 --q-run results/d0009_b3s_h2_base
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import mcnemar_exact, read_results
from atrium.arrays import F64Array
from atrium.merge import normalize_answer
from atrium.select_eval import (
    CANDIDATE_JUDGEMENTS,
    CE_ANSWER_FEATURES,
    CE_FEATURES,
    QuestionOutcome,
    apply_rule,
    candidate_ceilings,
    read_candidate_judgements,
    read_ce_features,
    segment_table,
    write_derived_run,
)
from atrium.verdict import paired_bootstrap_ci

logger = logging.getLogger(__name__)

# 同点とみなす予測・得点の差（事前登録）
TIE_TOLERANCE = 1e-6
# 標準化の分母に足す値
STD_EPS = 1e-9
# ロジスティック回帰（L2）の C・種・反復の上限・交差検証の分割数
LR_C = 1.0
LR_SEED = 12
LR_MAX_ITER = 1000
CV_FOLDS = 5
# 選び方：最大との差がこれ以内なら学習なしを優先する
SELECT_TIE_TOLERANCE = 0.002
# G-D：選んだ規則が R3′ より dev の正答率で上がっていなければならない幅
G_D_MIN_GAIN = 0.006
# G-J2：dev の採点し直しで判定できなかった候補の割合の上限
G_J2_MAX_UNPARSED = 0.01
# G-R (ii)：旧い判定の印の上の dev の R3′ の正解数（results/p0007_a6_dev_select）
G_R_DEV_CORRECT = 2293
# 分散の分解の区間に足す実行の水準の揺れ σ²_u,P（Iteration 22．results/d0009_a3_rerun_variance/rerun_variance.json）
SIGMA2_U_P = 1.63e-7
ANSWER_SELECTOR = "answer_selector.json"
SELECT_DEV = "select_dev.json"
ANSWER_SELECT_Q = "answer_select_q.json"
RULES = ("S1", "S2", "S3", "S4")
LEARNED_RULES = ("S3", "S4")
S3_FEATURES = (
    "ce_max",
    "ce_top1",
    "probe_fused",
    "top_score",
    "bm25_max",
    "rrf_max",
    "avg_logprob",
    "log_chars",
    "agree",
    "not_written",
)
S4_FEATURES = (*S3_FEATURES, "ce_qa_max", "ce_ans")
# 検証の LLM の得点（Iteration 28．欠損は選択では −inf，V2 の特徴量としては標準化後に 0）
V_FEATURE = "v"
V2_FEATURES = (*S3_FEATURES, V_FEATURE)
V_RULES = ("V1", "V2")
# 標準化しない特徴量（0/1）
UNSTANDARDIZED = frozenset({"not_written"})
# 「書かれていない」型の表現（事前登録で固定．dev を見て変えない）
NOT_WRITTEN_PATTERN = (
    r"\b(not (mentioned|provided|specified|stated|included|available)"
    r"|does not (mention|say|specify|provide|include|state)"
    r"|no (information|mention)|cannot be determined|unable to (determine|find)|there is no)\b"
)
NOT_WRITTEN = re.compile(NOT_WRITTEN_PATTERN, re.IGNORECASE)


@dataclass(frozen=True)
class QuestionCandidates:
    """1 問の候補（特徴量の行列と，候補が正解か）．失敗・候補の無い問は n_candidates = 0."""

    qid: str
    bank: str
    sources: tuple[str, ...]
    # 候補ごとの生の特徴量（標準化前．無ければ NaN）．列は FEATURE_NAMES の順
    raw: F64Array
    correct: tuple[bool, ...]
    unparsed: int


FEATURE_NAMES = (*S4_FEATURES, V_FEATURE)


def word_f1(a: str, b: str) -> float:
    """正規化した回答の語の集合の F1（どちらかが空なら 0）．"""
    sa, sb = set(normalize_answer(a).split()), set(normalize_answer(b).split())
    overlap = len(sa & sb)
    if not overlap:
        return 0.0
    precision, recall = overlap / len(sb), overlap / len(sa)
    return 2 * precision * recall / (precision + recall)


def agreement_scores(answers: Sequence[str]) -> list[float]:
    """各候補の，他の候補との語の F1 の最大（候補が 1 つなら 0）．"""
    return [
        max((word_f1(a, b) for j, b in enumerate(answers) if j != i), default=0.0)
        for i, a in enumerate(answers)
    ]


def read_answer_features(path: Path) -> dict[tuple[str, int], dict[str, float | None]]:
    """（質問 ID，候補の番号）→ {ce_qa_max, ce_ans}（ce_answer_features.jsonl）．"""
    with path.open(encoding="utf-8") as f:
        return {
            (j["qid"], int(j["index"])): {"ce_qa_max": j["ce_qa_max"], "ce_ans": j["ce_ans"]}
            for j in map(json.loads, f)
            if j
        }


def read_ce_top1(path: Path) -> dict[tuple[str, int], float]:
    """（質問 ID，候補の番号）→ ce_top1（ce_features.jsonl）．"""
    with path.open(encoding="utf-8") as f:
        return {
            (j["qid"], int(j["index"])): float(j["ce_top1"])
            for j in map(json.loads, f)
            if j and j["ce_top1"] is not None
        }


def read_verify_scores(path: Path) -> dict[tuple[str, int], float | None]:
    """（質問 ID，候補の番号）→ v（verify_features.jsonl．得点の無い候補は None．同じ候補は後の行が優先）．"""
    from atrium.answer_verify import read_verify_rows

    return {k: row.get("v") for k, row in read_verify_rows(path).items()}


def load_questions(
    run_dir: Path,
    need_labels: bool = True,
    need_answer_features: bool = True,
    verify_features: Path | None = None,
) -> list[QuestionCandidates]:
    """実行の全問の候補の特徴量（と，need_labels なら候補の正誤）を読む．失敗した問は候補なしにする．

    need_answer_features が False なら ce_answer_features.jsonl を読まず，ce_qa_max・ce_ans は欠損にする（R3′ だけを使う G-R 用）．
    verify_features があれば検証の得点 v を読む（無ければ v は欠損）．
    """
    rows = read_results(run_dir / "results.jsonl")
    ce_max = read_ce_features(run_dir / CE_FEATURES)
    ce_top1 = read_ce_top1(run_dir / CE_FEATURES)
    answer = read_answer_features(run_dir / CE_ANSWER_FEATURES) if need_answer_features else {}
    judged = read_candidate_judgements(run_dir / CANDIDATE_JUDGEMENTS) if need_labels else {}
    verify = read_verify_scores(verify_features) if verify_features is not None else {}
    out = []
    for row in rows:
        cands = (
            (row.get("owner_answer") or {}).get("candidates") or [] if not row.get("error") else []
        )
        answers = [str(c.get("answer") or "") for c in cands]
        agree = agreement_scores(answers)
        raw = np.full((len(cands), len(FEATURE_NAMES)), np.nan, dtype=np.float64)
        for i, c in enumerate(cands):
            key = (row["qid"], i)
            values: dict[str, float | None] = {
                "ce_max": ce_max.get(key),
                "ce_top1": ce_top1.get(key),
                "probe_fused": c.get("probe_fused"),
                "top_score": c.get("top_score"),
                "bm25_max": c.get("bm25_max"),
                "rrf_max": c.get("rrf_max"),
                "avg_logprob": c.get("avg_logprob"),
                "log_chars": math.log(len(answers[i]) + 1),
                "agree": agree[i],
                "not_written": float(bool(NOT_WRITTEN.search(answers[i]))),
                **answer.get(key, {"ce_qa_max": None, "ce_ans": None}),
                V_FEATURE: verify.get(key),
            }
            for j, name in enumerate(FEATURE_NAMES):
                v = values[name]
                if v is not None and math.isfinite(float(v)):
                    raw[i, j] = float(v)
        out.append(
            QuestionCandidates(
                row["qid"],
                str(row["bank"]),
                tuple(str(c["source"]) for c in cands),
                raw,
                tuple(judged.get((row["qid"], i)) is True for i in range(len(cands))),
                sum(judged.get((row["qid"], i), 0) is None for i in range(len(cands))),
            )
        )
    return out


def standardize_question(raw: F64Array, names: Sequence[str]) -> F64Array:
    """1 問の候補の特徴量を選んだ列の順に並べ，標準化する特徴量は問の中の平均・母標準偏差で標準化する（欠損は 0）．"""
    cols = [FEATURE_NAMES.index(n) for n in names]
    x = raw[:, cols].copy()
    for k, name in enumerate(names):
        col = x[:, k]
        present = np.isfinite(col)
        if name in UNSTANDARDIZED:
            col[~present] = 0.0
            continue
        if not present.any():
            col[:] = 0.0
            continue
        mean = col[present].mean()
        std = col[present].std()
        col[present] = (col[present] - mean) / (std + STD_EPS)
        col[~present] = 0.0
    return x


def choose_index(scores: Sequence[float]) -> int:
    """得点が最高（最高との差 ≤ TIE_TOLERANCE を同点とする）の候補のうち先の番号．全て −inf なら先頭．"""
    finite = [s if math.isfinite(s) else float("-inf") for s in scores]
    best = max(finite)
    if best == float("-inf"):
        return 0
    return next(i for i, s in enumerate(finite) if s >= best - TIE_TOLERANCE)


@dataclass(frozen=True)
class LinearSelector:
    """候補の正しさのロジスティック回帰（重みと切片）．1 問の中で確率が最大の候補を選ぶ（確率の単調変換なので得点は線形の項で足りる）．"""

    rule: str
    features: tuple[str, ...]
    coef: tuple[float, ...]
    intercept: float

    def probabilities(self, q: QuestionCandidates) -> list[float]:
        """1 問の候補ごとの正解の確率（シグモイド）．"""
        z = standardize_question(q.raw, self.features) @ np.asarray(self.coef) + self.intercept
        return [float(p) for p in 1.0 / (1.0 + np.exp(-z))]

    def to_json(self) -> dict[str, Any]:
        """答えの重みの JSON（answer_selector.json の rule 以下）．"""
        return {
            "rule": self.rule,
            "features": list(self.features),
            "coef": list(self.coef),
            "intercept": self.intercept,
        }


def fit_selector(rule: str, questions: Sequence[QuestionCandidates]) -> LinearSelector:
    """候補の行（正解か否か）にロジスティック回帰（L2．C = LR_C．種 LR_SEED．class_weight なし）を当てはめる．"""
    from sklearn.linear_model import LogisticRegression

    names = {"S3": S3_FEATURES, "S4": S4_FEATURES, "V2": V2_FEATURES}[rule]
    usable = [q for q in questions if q.sources]
    x = np.concatenate([standardize_question(q.raw, names) for q in usable])
    y = np.asarray([float(c) for q in usable for c in q.correct])
    model = LogisticRegression(C=LR_C, random_state=LR_SEED, max_iter=LR_MAX_ITER)
    model.fit(x, y)
    return LinearSelector(
        rule, names, tuple(float(c) for c in model.coef_[0]), float(model.intercept_[0])
    )


def rule_scores(rule: str, q: QuestionCandidates, selector: LinearSelector | None) -> list[float]:
    """規則の候補ごとの得点（S1・S2 は cross-encoder の得点，S3・S4 は正解の確率．欠損は −inf）．"""
    if rule in ("S1", "S2"):
        col = FEATURE_NAMES.index("ce_qa_max" if rule == "S1" else "ce_ans")
        return [float(v) if np.isfinite(v) else float("-inf") for v in q.raw[:, col]]
    if rule in ("R3p", "V1"):
        col = FEATURE_NAMES.index("ce_max" if rule == "R3p" else V_FEATURE)
        return [float(v) if np.isfinite(v) else float("-inf") for v in q.raw[:, col]]
    if selector is None:
        raise ValueError(f"{rule} needs a fitted selector")
    return selector.probabilities(q)


def choose_by_verification(q: QuestionCandidates) -> int:
    """V1：v が最大（最大との差 ≤ TIE_TOLERANCE は同点）の候補のうち，ce_max が大きい方（それも同点なら先）．

    v が欠損の候補は −inf（選ばれにくい側）．全候補が欠損なら全員が同点になり ce_max で決まる．
    """
    v = rule_scores("V1", q, None)
    best = max(v)
    tied = [i for i, s in enumerate(v) if s >= best - TIE_TOLERANCE]
    return tied[choose_index([rule_scores("R3p", q, None)[i] for i in tied])]


def choose_for(
    rule: str, questions: Sequence[QuestionCandidates], selector: LinearSelector | None
) -> list[int | None]:
    """全問に規則を当てはめて選んだ候補の番号（候補の無い問は None）．"""
    if rule == "V1":
        return [choose_by_verification(q) if q.sources else None for q in questions]
    return [choose_index(rule_scores(rule, q, selector)) if q.sources else None for q in questions]


def outcomes_of(
    questions: Sequence[QuestionCandidates], chosen: Sequence[int | None]
) -> list[QuestionOutcome]:
    """選んだ候補から 1 問ごとの結果を作る（失敗・候補なし・判定できなかった候補は不正解）．"""
    out = []
    for q, index in zip(questions, chosen, strict=True):
        owner_in = q.bank in q.sources
        if index is None:
            out.append(QuestionOutcome(q.qid, False, None, owner_in, False))
        else:
            out.append(
                QuestionOutcome(
                    q.qid, q.correct[index], index, owner_in, q.sources[index] == q.bank
                )
            )
    return out


def cross_validated_choices(rule: str, questions: Sequence[QuestionCandidates]) -> list[int | None]:
    """S3・S4 の dev の選択を，受信箱（持ち主）で束ねた 5 分割の交差検証の外側の予測で求める（受信箱は学習と評価に跨がらない）．"""
    from sklearn.model_selection import GroupKFold

    groups = [q.bank for q in questions]
    chosen: list[int | None] = [None] * len(questions)
    indices = np.arange(len(questions))
    for train, test in GroupKFold(n_splits=CV_FOLDS).split(indices, groups=groups):
        selector = fit_selector(rule, [questions[i] for i in train])
        for i in test:
            q = questions[i]
            chosen[i] = choose_index(selector.probabilities(q)) if q.sources else None
    return chosen


def select_rule_by_accuracy(accuracy: Mapping[str, float]) -> dict[str, Any]:
    """事前登録の選び方（割合）．最大との差が 0.002 以内なら学習なしを学習ありより，S3 を S4 より優先する．"""
    best = max(accuracy[r] for r in RULES)
    near = [r for r in RULES if accuracy[r] >= best - SELECT_TIE_TOLERANCE]
    unlearned = [r for r in near if r not in LEARNED_RULES]
    if unlearned:
        chosen = max(unlearned, key=lambda r: (accuracy[r], -RULES.index(r)))
    else:
        chosen = min(near, key=RULES.index)
    return {"chosen": chosen, "best_dev_accuracy": best, "within_tolerance": near}


def accuracy_of(outcomes: Sequence[QuestionOutcome]) -> dict[str, Any]:
    """正解数・問数・正答率．"""
    n, k = len(outcomes), sum(o.correct for o in outcomes)
    return {"n": n, "correct": k, "accuracy": k / n if n else 0.0}


def baseline_choices(questions: Sequence[QuestionCandidates]) -> list[int | None]:
    """R3′（ce_max の最大．同点は先）の，新しいコードで計算し直した選択（同点は TIE_TOLERANCE 以内を同点とみなす）．"""
    return choose_for("R3p", questions, None)


def tie_tolerant_agreement(
    questions: Sequence[QuestionCandidates],
    mine: Sequence[int | None],
    reference_index: Mapping[str, int | None],
) -> dict[str, Any]:
    """新しいコードの R3′ の選択と参照（既存の `select-eval --apply R3` の chosen_index）の一致（厳密と，ce_max の差 ≤ 1e-6 を同点とする読み）．"""
    col = FEATURE_NAMES.index("ce_max")
    strict = tied = n = 0
    for q, m in zip(questions, mine, strict=True):
        ref = reference_index.get(q.qid)
        if m is None and ref is None:
            continue
        n += 1
        same = m == ref
        strict += same
        if not same and m is not None and ref is not None:
            same = abs(q.raw[m, col] - q.raw[ref, col]) <= TIE_TOLERANCE
        tied += bool(same)
    return {"n": n, "n_identical": strict, "n_identical_tie_tolerant": tied}


def read_derived_choices(path: Path) -> dict[str, int | None]:
    """`select-eval --apply` の derived run の results.jsonl → 質問 ID → chosen_index．"""
    with path.open(encoding="utf-8") as f:
        return {j["qid"]: j["chosen_index"] for j in map(json.loads, f) if j}


def existing_r3p_outcomes(
    run_dir: Path, questions: Sequence[QuestionCandidates]
) -> list[QuestionOutcome]:
    """基準 R3′ の結果（既存の `select_eval.apply_rule` の R3・UNKNOWN の除外なし．results/<run>_R3p と同じ選択）．questions の順に並べる．"""
    rows = read_results(run_dir / "results.jsonl")
    judged = read_candidate_judgements(run_dir / CANDIDATE_JUDGEMENTS)
    by_qid = {
        o.qid: o
        for o in apply_rule(
            rows, judged, "R3", read_ce_features(run_dir / CE_FEATURES), ignore_unknown=True
        )
    }
    return [by_qid[q.qid] for q in questions]


# ── dev ───────────────────────────────────────────────────────────────────────


def evaluate_dev(
    dev_run: Path, old_dev_run: Path, h_run: Path, h_derived: Path, out_dir: Path
) -> dict[str, Any]:
    """dev の S1〜S4 を評価して 1 つ選び（G-D），G-R・G-J2 を確かめ，select_dev.json・answer_selector.json・verdict.json を書く．"""
    dev = load_questions(dev_run)
    n = len(dev)
    base = existing_r3p_outcomes(dev_run, dev)
    accuracy = {"R3p": accuracy_of(base)}
    dev_outcomes: dict[str, list[QuestionOutcome]] = {"R3p": base}
    for rule in RULES:
        chosen = (
            cross_validated_choices(rule, dev)
            if rule in LEARNED_RULES
            else choose_for(rule, dev, None)
        )
        dev_outcomes[rule] = outcomes_of(dev, chosen)
        accuracy[rule] = accuracy_of(dev_outcomes[rule])
    selection = select_rule_by_accuracy({r: accuracy[r]["accuracy"] for r in RULES})
    chosen_rule = selection["chosen"]
    gain = accuracy[chosen_rule]["accuracy"] - accuracy["R3p"]["accuracy"]
    g_d = {
        "dev_accuracy": accuracy,
        "selection": selection,
        "gain_over_r3p": gain,
        "min_gain": G_D_MIN_GAIN,
        "passed": bool(gain >= G_D_MIN_GAIN),
        "n_dev": n,
        "cross_validation": f"GroupKFold({CV_FOLDS}) by the gold inbox (S3, S4)",
        "segments": {r: segment_table(dev_outcomes[r]) for r in ("R3p", *RULES)},
        "ceilings": candidate_ceilings(
            read_results(dev_run / "results.jsonl"),
            read_candidate_judgements(dev_run / CANDIDATE_JUDGEMENTS),
            dev_outcomes["R3p"],
        ),
    }
    # 全 dev で学習し直した重み（選ばれたものが学習ありのときだけ固定する．S3・S4 の両方の重みは記録として書く）
    fitted = {r: fit_selector(r, dev) for r in LEARNED_RULES}
    selector_doc: dict[str, Any] = {
        "chosen": chosen_rule,
        "trained_on": f"dev ({n} questions; {dev_run})",
        "model": f"LogisticRegression(C={LR_C}, random_state={LR_SEED}, penalty=l2)",
        "not_written_pattern": NOT_WRITTEN_PATTERN,
        "weights": {r: fitted[r].to_json() for r in LEARNED_RULES},
    }
    if chosen_rule in LEARNED_RULES:
        payload = json.dumps(selector_doc["weights"][chosen_rule], sort_keys=True).encode()
        selector_doc["chosen_sha256"] = hashlib.sha256(payload).hexdigest()
    # ── G-R ──
    old = load_questions(old_dev_run, need_answer_features=False)
    old_outcomes = outcomes_of(old, baseline_choices(old))
    h = load_questions(h_run, need_answer_features=False)
    h_agreement = tie_tolerant_agreement(
        h, baseline_choices(h), read_derived_choices(h_derived / "results.jsonl")
    )
    g_r = {
        "dev_old_labels_r3p_correct": accuracy_of(old_outcomes)["correct"],
        "dev_old_labels_expected": G_R_DEV_CORRECT,
        "h_vs_a3_p1_R3p": h_agreement,
    }
    g_r["passed"] = bool(
        g_r["dev_old_labels_r3p_correct"] == G_R_DEV_CORRECT
        and h_agreement["n_identical_tie_tolerant"] == h_agreement["n"]
    )
    g_r["h_strictly_identical"] = h_agreement["n_identical"] == h_agreement["n"]
    # ── G-J2 ──
    judged = read_candidate_judgements(dev_run / CANDIDATE_JUDGEMENTS)
    old_judged = read_candidate_judgements(old_dev_run / CANDIDATE_JUDGEMENTS)
    both = [k for k in judged if judged[k] is not None and old_judged.get(k) is not None]
    g_j2 = {
        "n_candidates": len(judged),
        "n_unparsed": sum(v is None for v in judged.values()),
        "unparsed_rate": sum(v is None for v in judged.values()) / max(1, len(judged)),
        "max_unparsed_rate": G_J2_MAX_UNPARSED,
        "agreement_with_old_labels": sum(judged[k] == old_judged[k] for k in both)
        / max(1, len(both)),
        "n_compared": len(both),
    }
    g_j2["passed"] = bool(g_j2["unparsed_rate"] <= G_J2_MAX_UNPARSED)
    result = {"G_D": g_d, "G_R": g_r, "G_J2": g_j2, "selector": selector_doc["chosen"]}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / SELECT_DEV).write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / ANSWER_SELECTOR).write_text(
        json.dumps(selector_doc, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return result


# ── Q への当てはめ ─────────────────────────────────────────────────────────────


def load_selector(path: Path) -> tuple[str, LinearSelector | None]:
    """固定した answer_selector.json から，選んだ規則と（学習ありなら）重みを読む．"""
    doc = json.loads(path.read_text(encoding="utf-8"))
    rule = doc["chosen"]
    if rule not in (*LEARNED_RULES, "V2"):
        return rule, None
    w = doc["weights"][rule]
    return rule, LinearSelector(rule, tuple(w["features"]), tuple(w["coef"]), float(w["intercept"]))


def paired_report(new: Sequence[bool], base: Sequence[bool]) -> dict[str, Any]:
    """new − 基準の正答率の差・対応ありのブートストラップ区間・分散の分解の区間・McNemar（b = new だけ正解，c = 基準だけ正解）．"""
    from atrium.rerun_variance import Z_975, student_t_quantile
    from atrium.verdict import ALPHA

    y_new, y_base = np.asarray(new, dtype=np.float64), np.asarray(base, dtype=np.float64)
    diff = y_new - y_base
    n = len(diff)
    low, high = paired_bootstrap_ci(diff)
    # 分散の分解の区間（reread_test_pair と同じ式．各設定 1 回の実行なので σ²_u,P を両側に 1 倍足す）
    se_q = float(diff.std(ddof=1) / math.sqrt(n))
    se_tot = math.sqrt(se_q**2 + 2 * SIGMA2_U_P)
    df = se_tot**4 / ((SIGMA2_U_P**2 + SIGMA2_U_P**2) / 2.0)
    t = student_t_quantile(1.0 - ALPHA / 2, df) if SIGMA2_U_P > 0 else Z_975
    delta = float(diff.mean())
    b = int(((y_new == 1) & (y_base == 0)).sum())
    c = int(((y_new == 0) & (y_base == 1)).sum())
    return {
        "n": n,
        "acc_new": float(y_new.mean()),
        "acc_base": float(y_base.mean()),
        "delta": delta,
        "bootstrap_ci95": [low, high],
        "analytic_ci95": [delta - t * se_tot, delta + t * se_tot],
        "se_q": se_q,
        "se_total": se_tot,
        "df": df,
        "t": t,
        "b_only_new": b,
        "c_only_base": c,
        "mcnemar_p": mcnemar_exact(b, c),
    }


def decide_improvement(report: Mapping[str, Any]) -> str:
    """H-B3s：両方の 95% 区間の下端 > 0 なら「改善」，両方の上端 < 0 なら「悪化」，それ以外は「決まらない」．"""
    lows = [report["bootstrap_ci95"][0], report["analytic_ci95"][0]]
    highs = [report["bootstrap_ci95"][1], report["analytic_ci95"][1]]
    if all(v > 0 for v in lows):
        return "improved"
    if all(v < 0 for v in highs):
        return "worse"
    return "undecided"


def apply_to_runs(
    selector_path: Path,
    q_runs: Sequence[Path],
    extra_runs: Sequence[Path],
    out_dir: Path,
    derived_prefix: str,
    verify_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    """固定した規則を Q（q_runs の合併）と記述用の実行（extra_runs）に当てはめ，R3′ との差などを出す．

    verify_paths（Iteration 28 の V1・V2）は [*q_runs, *extra_runs] と同じ順の verify_features.jsonl（V の規則では必須）．

    基準 R3′ は既存の `select_eval.apply_rule`（R3・UNKNOWN の除外なし）の選択（results/<run>_R3p と同じ）．選んだ候補は
    `<derived_prefix><run 名>_S` に書く．
    """
    rule, selector = load_selector(selector_path)
    all_runs = [*q_runs, *extra_runs]
    if rule in V_RULES and len(verify_paths) != len(all_runs):
        raise ValueError(f"{rule} needs one verify_features.jsonl per run ({len(all_runs)} runs)")
    verify_of = dict(zip((r.name for r in all_runs), verify_paths, strict=False))

    def evaluate_run(run_dir: Path) -> dict[str, Any]:
        questions = load_questions(run_dir, verify_features=verify_of.get(run_dir.name))
        chosen = choose_for(rule, questions, selector)
        new = outcomes_of(questions, chosen)
        rows = read_results(run_dir / "results.jsonl")
        judged = read_candidate_judgements(run_dir / CANDIDATE_JUDGEMENTS)
        base = existing_r3p_outcomes(run_dir, questions)
        derived = Path(f"{derived_prefix}{run_dir.name}_{rule}")
        write_derived_run(run_dir, new, rule, derived, ignore_unknown=True)
        return {
            "questions": questions,
            "new": new,
            "base": base,
            "rows": rows,
            "judged": judged,
        }

    per_run = {run.name: evaluate_run(run) for run in all_runs}
    result: dict[str, Any] = {"rule": rule, "selector": str(selector_path), "runs": {}}
    q_new = [o for r in q_runs for o in per_run[r.name]["new"]]
    q_base = [o for r in q_runs for o in per_run[r.name]["base"]]
    report = paired_report([o.correct for o in q_new], [o.correct for o in q_base])
    result["Q"] = {
        **report,
        "verdict": decide_improvement(report),
        "segments": {"R3p": segment_table(q_base), rule: segment_table(q_new)},
        "n_chosen_changed": int(
            sum(a.chosen_index != b.chosen_index for a, b in zip(q_new, q_base, strict=True))
        ),
        "any_candidate_correct": sum(
            candidate_ceilings(d["rows"], d["judged"], d["base"])["any_candidate_correct"]
            * len(d["rows"])
            for d in (per_run[r.name] for r in q_runs)
        )
        / max(1, sum(len(per_run[r.name]["rows"]) for r in q_runs)),
    }
    for run in [*q_runs, *extra_runs]:
        data = per_run[run.name]
        result["runs"][run.name] = paired_report(
            [o.correct for o in data["new"]], [o.correct for o in data["base"]]
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ANSWER_SELECT_Q).write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return result


def dev_rejection(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """dev の段階で G-D が不通過なら verdict_original（棄却）を返す（通過なら None．最終の判定は Q で出す）．"""
    g_d = result["G_D"]
    if g_d["passed"]:
        return None
    return {
        "hypothesis": "H-B3s（回答の選択の替え方は R3′ の正答率を上げる）．実機に進むかはオフラインの G-D で決める",
        "verdict": "rejected_gate_not_passed",
        "live_run": False,
        "reason": "G-D 不通過（dev で選んだ規則の増分が +0.006 に届かない）．H2 の実機も H・H2 への当てはめも行わない．",
        "G_D": {
            "chosen": g_d["selection"]["chosen"],
            "gain": g_d["gain_over_r3p"],
            "min_gain": g_d["min_gain"],
            "dev_accuracy": {r: v["accuracy"] for r, v in g_d["dev_accuracy"].items()},
            "passed": False,
        },
        "G_R": result["G_R"],
        "G_J2": result["G_J2"],
    }


# ── 検証の得点で選ぶ V1・V2（Iteration 28） ─────────────────────────────────────────

ANSWER_VERIFIER_SELECTOR = "answer_verifier_selector.json"
VERIFY_DEV = "verify_dev.json"
# G-R：dev の R3′ の正解数（qwen3:8b の印）と S3 の交差検証の正解数（Iteration 25 の select_dev.json）
G_R_DEV_R3P_CORRECT = 2131
G_R_DEV_S3_CORRECT = 2141
# G-V：得点の無い候補の割合の上限
G_V_MAX_NO_SCORE = 0.01
# V2 を選べる条件の参照（Iteration 25 の S3 の dev の正答率．同じ交差検証を今のコードで計算し直した値と併せて記録する）
S3_DEV_ACCURACY_REFERENCE = 0.7263
RULES_V = ("R3p", "S3", "V1", "V2")


def select_verification_rule(accuracy: Mapping[str, float]) -> dict[str, Any]:
    """G-D の選び方．V2 は dev の正答率が S3 を上回るときだけ候補になる．最大との差が 0.002 以内なら V1（学習なし）を優先する．"""
    eligible = ["V1"] + (["V2"] if accuracy["V2"] > accuracy["S3"] else [])
    best = max(accuracy[r] for r in eligible)
    near = [r for r in eligible if accuracy[r] >= best - SELECT_TIE_TOLERANCE]
    chosen = "V1" if "V1" in near else max(near, key=lambda r: accuracy[r])
    return {
        "chosen": chosen,
        "eligible": eligible,
        "best_dev_accuracy": best,
        "within_tolerance": near,
        "v2_exceeds_s3": accuracy["V2"] > accuracy["S3"],
    }


def auroc_by_owner(questions: Sequence[QuestionCandidates], feature: str) -> dict[str, Any]:
    """候補の正しさに対する特徴量の AUROC（記述）．全候補・持ち主の候補・持ち主でない候補．欠損は最小より小さい値に置く．"""
    from sklearn.metrics import roc_auc_score

    col = FEATURE_NAMES.index(feature)
    rows = [
        (q.raw[i, col], q.correct[i], q.sources[i] == q.bank)
        for q in questions
        for i in range(len(q.sources))
    ]
    finite = [r[0] for r in rows if np.isfinite(r[0])]
    floor = min(finite) - 1.0 if finite else -1.0
    out: dict[str, Any] = {}
    for name, keep in (
        ("all", lambda owner: True),
        ("owner", lambda owner: owner),
        ("non_owner", lambda owner: not owner),
    ):
        sub = [(v if np.isfinite(v) else floor, c) for v, c, owner in rows if keep(owner)]
        labels = [c for _, c in sub]
        out[name] = {
            "n": len(sub),
            "positive_rate": sum(labels) / len(sub) if sub else 0.0,
            "auroc": float(roc_auc_score(labels, [v for v, _ in sub]))
            if 0 < sum(labels) < len(sub)
            else None,
        }
    return out


def verification_gate(verify_path: Path, questions: Sequence[QuestionCandidates]) -> dict[str, Any]:
    """G-V：dev の候補のうち得点が無い（yes・no が上位に無い・文脈長の超過・失敗・ファイルに無い）割合 ≤ 1%．最初のトークンの内訳も記録する．"""
    from atrium.answer_verify import read_verify_rows

    rows = read_verify_rows(verify_path)
    keys = [(q.qid, i) for q in questions for i in range(len(q.sources))]
    n = len(keys)
    no_score = sum(rows.get(k, {}).get("v") is None for k in keys)
    other = sum(bool(rows.get(k, {}).get("first_token_other")) for k in keys)
    errors: dict[str, int] = {}
    for k in keys:
        err = rows.get(k, {"error": "missing"}).get("error")
        if err:
            errors[str(err)] = errors.get(str(err), 0) + 1
    gate: dict[str, Any] = {
        "n_candidates": n,
        "n_no_score": no_score,
        "no_score_rate": no_score / max(1, n),
        "max_no_score_rate": G_V_MAX_NO_SCORE,
        "n_first_token_not_yes_no": other,
        "first_token_not_yes_no_rate": other / max(1, n),
        "n_answer_empty": sum(bool(rows.get(k, {}).get("answer_empty")) for k in keys),
        "errors": errors,
    }
    gate["passed"] = bool(gate["no_score_rate"] <= G_V_MAX_NO_SCORE)
    return gate


def evaluate_verify_dev(
    dev_run: Path,
    verify_path: Path,
    h_run: Path,
    h_derived: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """dev で V1・V2（参照に R3′・S3）を評価し，G-R・G-V・G-D・AUROC を verify_dev.json に，V2 の重みを answer_verifier_selector.json に書く．"""
    from atrium.answer_verify import prompt_sha256

    dev = load_questions(dev_run, need_answer_features=False, verify_features=verify_path)
    base = existing_r3p_outcomes(dev_run, dev)
    outcomes: dict[str, list[QuestionOutcome]] = {"R3p": base}
    outcomes["S3"] = outcomes_of(dev, cross_validated_choices("S3", dev))
    outcomes["V1"] = outcomes_of(dev, choose_for("V1", dev, None))
    outcomes["V2"] = outcomes_of(dev, cross_validated_choices("V2", dev))
    accuracy = {r: accuracy_of(outcomes[r]) for r in RULES_V}
    # G-R
    recomputed = outcomes_of(dev, baseline_choices(dev))
    h = load_questions(h_run, need_answer_features=False)
    h_agreement = tie_tolerant_agreement(
        h, baseline_choices(h), read_derived_choices(h_derived / "results.jsonl")
    )
    g_r = {
        "dev_r3p_correct": accuracy["R3p"]["correct"],
        "dev_r3p_recomputed_correct": accuracy_of(recomputed)["correct"],
        "dev_r3p_expected": G_R_DEV_R3P_CORRECT,
        "dev_s3_correct": accuracy["S3"]["correct"],
        "dev_s3_expected": G_R_DEV_S3_CORRECT,
        "h_vs_a3_p1_R3p": h_agreement,
        "h_strictly_identical": h_agreement["n_identical"] == h_agreement["n"],
    }
    g_r["passed"] = bool(
        g_r["dev_r3p_correct"] == G_R_DEV_R3P_CORRECT
        and g_r["dev_r3p_recomputed_correct"] == G_R_DEV_R3P_CORRECT
        and g_r["dev_s3_correct"] == G_R_DEV_S3_CORRECT
        and h_agreement["n_identical_tie_tolerant"] == h_agreement["n"]
    )
    g_v = verification_gate(verify_path, dev)
    # G-D
    selection = select_verification_rule({r: accuracy[r]["accuracy"] for r in RULES_V})
    chosen = selection["chosen"]
    gain = accuracy[chosen]["accuracy"] - accuracy["R3p"]["accuracy"]
    paired = {
        r: paired_report([o.correct for o in outcomes[r]], [o.correct for o in base])
        for r in ("S3", "V1", "V2")
    }
    g_d = {
        "dev_accuracy": accuracy,
        "selection": selection,
        "gain_over_r3p": gain,
        "min_gain": G_D_MIN_GAIN,
        "passed": bool(gain >= G_D_MIN_GAIN),
        "s3_dev_accuracy_reference": S3_DEV_ACCURACY_REFERENCE,
        "paired_vs_r3p": paired,
        "n_dev": len(dev),
        "cross_validation": f"GroupKFold({CV_FOLDS}) by the gold inbox (S3, V2)",
        "segments": {r: segment_table(outcomes[r]) for r in RULES_V},
        "ceilings": candidate_ceilings(
            read_results(dev_run / "results.jsonl"),
            read_candidate_judgements(dev_run / CANDIDATE_JUDGEMENTS),
            base,
        ),
        "n_chosen_differs_from_r3p": {
            r: sum(a.chosen_index != b.chosen_index for a, b in zip(outcomes[r], base, strict=True))
            for r in ("S3", "V1", "V2")
        },
    }
    diagnostics = {f: auroc_by_owner(dev, f) for f in ("v", "ce_max")}
    # V2 の重み（全 dev で学習し直す．選ばれたときだけ固定する）
    v2 = fit_selector("V2", dev)
    selector_doc: dict[str, Any] = {
        "chosen": chosen,
        "trained_on": f"dev ({len(dev)} questions; {dev_run})",
        "model": f"LogisticRegression(C={LR_C}, random_state={LR_SEED}, penalty=l2)",
        "not_written_pattern": NOT_WRITTEN_PATTERN,
        "verify_prompt_sha256": prompt_sha256(),
        "weights": {"V2": v2.to_json()},
    }
    if chosen == "V2":
        payload = json.dumps(selector_doc["weights"]["V2"], sort_keys=True).encode()
        selector_doc["chosen_sha256"] = hashlib.sha256(payload).hexdigest()
    result = {"G_R": g_r, "G_V": g_v, "G_D": g_d, "auroc_dev": diagnostics, "selector": chosen}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / VERIFY_DEV).write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / ANSWER_VERIFIER_SELECTOR).write_text(
        json.dumps(selector_doc, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return result
