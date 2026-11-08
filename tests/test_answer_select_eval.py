"""d0009 B3 Iteration 25 の回答の選択の規則 S1〜S4（atrium.answer_select_eval）の仕様：特徴量の定義・同点の扱い・交差検証の分割・R3′ の再計算・選び方．"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from atrium.answer_select_eval import (
    FEATURE_NAMES,
    NOT_WRITTEN,
    S3_FEATURES,
    S4_FEATURES,
    QuestionCandidates,
    agreement_scores,
    apply_to_runs,
    baseline_choices,
    choose_for,
    choose_index,
    cross_validated_choices,
    decide_improvement,
    fit_selector,
    load_questions,
    paired_report,
    select_rule_by_accuracy,
    standardize_question,
    word_f1,
)
from atrium.select_eval import apply_rule, read_candidate_judgements, read_ce_features


def question(
    qid: str, bank: str, sources: list[str], correct: list[bool], **columns: list[float]
) -> QuestionCandidates:
    """特徴量を列ごと（名前 = 候補ごとの値）で渡して 1 問を作る．渡さない列は NaN（欠損）．"""
    raw = np.full((len(sources), len(FEATURE_NAMES)), np.nan)
    for name, values in columns.items():
        raw[:, FEATURE_NAMES.index(name)] = values
    return QuestionCandidates(qid, bank, tuple(sources), raw, tuple(correct), 0)


# ── 特徴量の定義 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The email does not mention a phone number.", True),
        ("This information is NOT MENTIONED in the email.", True),
        ("There is no record of it.", True),
        ("Unable to find the date.", True),
        ("The phone number is 503/464-3822.", False),
        ("He knows nothing about it.", False),
    ],
)
def test_not_written_pattern_is_fixed_and_case_insensitive(text: str, expected: bool) -> None:
    assert bool(NOT_WRITTEN.search(text)) is expected


def test_word_f1_is_the_f1_of_normalised_word_sets() -> None:
    assert word_f1("Chris Foster, 503", "chris foster 503") == 1.0
    assert word_f1("a b", "c d") == 0.0
    assert word_f1("", "a") == 0.0
    # 集合 {a, b} と {b, c}: 重なり 1，適合率 = 再現率 = 1/2，F1 = 1/2
    assert word_f1("a b", "b c") == pytest.approx(0.5)


def test_agreement_is_the_best_f1_against_the_other_candidates() -> None:
    scores = agreement_scores(["a b", "a b", "x y"])
    assert scores == [1.0, 1.0, 0.0]
    assert agreement_scores(["only one"]) == [0.0]


def test_standardisation_is_within_the_question_with_population_std_and_missing_as_zero() -> None:
    q = question(
        "q",
        "a",
        ["a", "b", "c"],
        [True, False, False],
        ce_max=[1.0, 2.0, 3.0],
        not_written=[1.0, 0.0, 0.0],
        ce_top1=[np.nan, np.nan, np.nan],
    )
    x = standardize_question(q.raw, ("ce_max", "ce_top1", "not_written"))
    std = np.std([1.0, 2.0, 3.0])
    np.testing.assert_allclose(x[:, 0], (np.array([1.0, 2.0, 3.0]) - 2.0) / (std + 1e-9))
    assert x[:, 1].tolist() == [0.0, 0.0, 0.0]
    # 「書かれていない」型の表現は標準化しない
    assert x[:, 2].tolist() == [1.0, 0.0, 0.0]


def test_feature_sets_have_the_registered_sizes() -> None:
    assert len(S3_FEATURES) == 10
    assert len(S4_FEATURES) == 12


# ── 同点の扱い・選択 ──────────────────────────────────────────────────────────


def test_choose_index_treats_differences_within_1e6_as_ties_and_takes_the_earlier() -> None:
    assert choose_index([0.5, 0.5 + 5e-7, 0.1]) == 0
    assert choose_index([0.5, 0.5 + 2e-6, 0.1]) == 1
    assert choose_index([float("-inf"), float("-inf")]) == 0
    assert choose_index([float("-inf"), -3.0]) == 1


def test_s1_picks_the_candidate_with_the_best_answer_aware_score_and_ties_go_first() -> None:
    q = question("q", "a", ["a", "b", "c"], [False, True, False], ce_qa_max=[0.1, 0.9, 0.9])
    assert choose_for("S1", [q], None) == [1]


def test_questions_without_candidates_are_unanswered_and_counted_wrong() -> None:
    empty = question("e", "a", [], [])
    assert choose_for("S2", [empty], None) == [None]


# ── 学習・交差検証 ────────────────────────────────────────────────────────────


def separable_questions(n: int) -> list[QuestionCandidates]:
    """ce_max が高い候補だけが正解の問（学習で必ず選べる）．受信箱は 5 種類を巡る．"""
    out = []
    for i in range(n):
        top = i % 3
        scores = [0.1, 0.2, 0.3]
        scores[top] = 0.95
        out.append(
            question(
                f"q{i}",
                f"inbox{i % 5}",
                ["x", "y", "z"],
                [j == top for j in range(3)],
                ce_max=scores,
                ce_qa_max=scores,
                ce_ans=scores,
            )
        )
    return out


def test_logistic_selector_learns_the_feature_that_separates_correct_candidates() -> None:
    qs = separable_questions(40)
    selector = fit_selector("S3", qs)
    assert choose_for("S3", qs, selector) == [i % 3 for i in range(40)]


def test_cross_validation_never_trains_and_evaluates_on_the_same_inbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import atrium.answer_select_eval as module

    qs = separable_questions(50)
    seen: list[tuple[set[str], set[str]]] = []
    original = module.fit_selector

    def spy(rule: str, questions: Any) -> Any:
        selector = original(rule, questions)
        seen.append(({q.bank for q in questions}, set()))
        return selector

    monkeypatch.setattr(module, "fit_selector", spy)
    chosen = cross_validated_choices("S3", qs)
    assert len(seen) == module.CV_FOLDS
    # 5 種類の受信箱を 5 分割すると，各分割の学習には評価の受信箱が含まれない（学習の受信箱は 4 種類）
    assert all(len(train) == 4 for train, _ in seen)
    assert chosen == [i % 3 for i in range(50)]


# ── R3′ の再計算が select-eval と同じ選択になること ─────────────────────────────


def write_run(
    run: Path, rows: list[dict[str, Any]], ce: list[dict[str, Any]], judged: list[dict[str, Any]]
) -> None:
    """実行のディレクトリ（results.jsonl・ce_features.jsonl・ce_answer_features.jsonl・candidate_judgements.jsonl）を書く．"""
    run.mkdir(parents=True)
    (run / "results.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    (run / "ce_features.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in ce), encoding="utf-8"
    )
    answer = [
        {
            "qid": c["qid"],
            "index": c["index"],
            "source": c["source"],
            "ce_qa_max": c["ce_max"],
            "ce_ans": None,
        }
        for c in ce
    ]
    (run / "ce_answer_features.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in answer), encoding="utf-8"
    )
    (run / "candidate_judgements.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in judged), encoding="utf-8"
    )


def make_run(run: Path) -> None:
    """2 問・各 3 候補の実行．1 問目は ce_max の最高が 2 番目，2 問目は先頭と 2 番目が 1e-7 差の同点（先頭を採る）．"""
    answers = [
        ["Foo is 1", "Foo is 2", "not mentioned"],
        ["Bar is 3", "Bar is 3", "Baz"],
    ]
    rows, ce, judged = [], [], []
    ce_values = [[0.1, 0.8, 0.3], [0.5, 0.5 + 1e-7, 0.2]]
    correct = [[False, True, False], [True, True, False]]
    for qi, qid in enumerate(["q0", "q1"]):
        cands = [
            {
                "source": s,
                "answer": answers[qi][i],
                "unknown": False,
                "top_score": 0.1 * i,
                "probe_fused": 0.0,
                "bm25_max": 1.0,
                "rrf_max": 0.03,
                "avg_logprob": -0.1,
                "context_doc_ids": [],
            }
            for i, s in enumerate(["a", "b", "c"])
        ]
        rows.append({"qid": qid, "bank": "a", "error": None, "owner_answer": {"candidates": cands}})
        for i, s in enumerate(["a", "b", "c"]):
            ce.append(
                {
                    "qid": qid,
                    "index": i,
                    "source": s,
                    "ce_max": ce_values[qi][i],
                    "ce_top1": ce_values[qi][i],
                }
            )
            judged.append({"qid": qid, "index": i, "source": s, "correct": correct[qi][i]})
    write_run(run, rows, ce, judged)


def test_recomputed_r3p_matches_select_eval_up_to_ties(tmp_path: Path) -> None:
    run = tmp_path / "run"
    make_run(run)
    questions = load_questions(run)
    mine = baseline_choices(questions)
    existing = apply_rule(
        [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()],
        read_candidate_judgements(run / "candidate_judgements.jsonl"),
        "R3",
        read_ce_features(run / "ce_features.jsonl"),
        ignore_unknown=True,
    )
    # 1 問目は同じ．2 問目は 1e-7 差の同点で，新しいコードは先頭，既存の厳密な最大は 2 番目を採る
    assert mine == [1, 0]
    assert [o.chosen_index for o in existing] == [1, 1]


def test_apply_selects_with_the_fixed_rule_and_writes_the_chosen_candidates(tmp_path: Path) -> None:
    run = tmp_path / "run"
    make_run(run)
    selector = tmp_path / "answer_selector.json"
    selector.write_text(json.dumps({"chosen": "S1"}), encoding="utf-8")
    result = apply_to_runs(selector, [run], [], tmp_path / "out", f"{tmp_path}/")
    # S1 は ce_qa_max（このテストでは ce_max と同じ値）の最大．問 q1 の同点は先頭
    derived = [
        json.loads(line)
        for line in (tmp_path / "run_S1" / "results.jsonl").read_text().splitlines()
    ]
    assert [d["chosen_index"] for d in derived] == [1, 0]
    assert result["Q"]["n"] == 2
    assert result["Q"]["acc_new"] == 1.0


# ── 選び方・判定 ──────────────────────────────────────────────────────────────


def test_selection_prefers_unlearned_rules_within_0_002_and_s3_over_s4() -> None:
    acc = {"S1": 0.790, "S2": 0.700, "S3": 0.791, "S4": 0.792}
    assert select_rule_by_accuracy(acc)["chosen"] == "S1"
    acc = {"S1": 0.700, "S2": 0.700, "S3": 0.791, "S4": 0.792}
    assert select_rule_by_accuracy(acc)["chosen"] == "S3"
    acc = {"S1": 0.700, "S2": 0.700, "S3": 0.790, "S4": 0.793}
    assert select_rule_by_accuracy(acc)["chosen"] == "S4"


def test_improvement_needs_both_interval_lower_bounds_above_zero() -> None:
    both = {"bootstrap_ci95": [0.001, 0.01], "analytic_ci95": [0.002, 0.01]}
    assert decide_improvement(both) == "improved"
    assert (
        decide_improvement({"bootstrap_ci95": [0.001, 0.01], "analytic_ci95": [-0.001, 0.01]})
        == "undecided"
    )
    assert (
        decide_improvement({"bootstrap_ci95": [-0.01, -0.001], "analytic_ci95": [-0.01, -0.002]})
        == "worse"
    )


def test_paired_report_counts_discordant_pairs_and_uses_the_extended_interval() -> None:
    new = [True] * 60 + [False] * 40
    base = [True] * 50 + [False] * 10 + [True] * 5 + [False] * 35
    report = paired_report(new, base)
    assert report["b_only_new"] == 10
    assert report["c_only_base"] == 5
    assert report["delta"] == pytest.approx(0.05)
    # 実行の水準の揺れの項（σ²_u,P の 2 倍）を足すので，解析的な区間は問だけの区間より広い
    assert report["se_total"] > report["se_q"]
