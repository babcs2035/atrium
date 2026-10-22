"""p0007 A5・A6 の候補の選び方の規則（atrium.select_eval）の仕様：規則の当てはめ（UNKNOWN の除外の有無）・順位の和・区分・上限・選択・compare への出力．"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from atrium.analysis import merge_judgements, read_results
from atrium.merge import OwnerCandidate, choose_max_score
from atrium.select_eval import (
    RULE_ORDER,
    Candidate,
    apply_rule,
    average_ranks,
    choose_by_feature,
    choose_by_rank_sum,
    cross_encoder_features,
    evaluate,
    rule_chooser,
    select_rule,
    write_derived_run,
)


def cand(source: str, unknown: bool = False, **features: float | None) -> Candidate:
    """候補（特徴量は名前 = 値で渡す）．"""
    return Candidate(source, unknown, features)


# ── 規則の当てはめ（純粋関数） ─────────────────────────────────────────────


def test_feature_rule_takes_the_best_answered_candidate_and_skips_unknown() -> None:
    cands = [
        cand("a", unknown=True, top_score=0.9),
        cand("b", top_score=0.5),
        cand("c", top_score=0.7),
    ]
    assert choose_by_feature(cands, "top_score") == 2


def test_feature_rule_falls_back_to_all_candidates_when_every_answer_is_unknown() -> None:
    cands = [cand("a", True, top_score=0.3), cand("b", True, top_score=0.8)]
    assert choose_by_feature(cands, "top_score") == 1


def test_feature_rule_ties_go_to_the_earlier_candidate_and_missing_values_are_lowest() -> None:
    assert choose_by_feature([cand("a", x=1.0), cand("b", x=1.0)], "x") == 0
    assert choose_by_feature([cand("a", x=None), cand("b", x=-5.0)], "x") == 1


def test_r0_is_the_live_abstain_rule_on_the_dense_top_score() -> None:
    cands = [cand("a", True, top_score=0.9), cand("b", top_score=0.2), cand("c", top_score=0.4)]
    assert rule_chooser("R0")(cands) == choose_by_feature(cands, "top_score") == 2


def test_rules_differ_only_in_the_feature_they_rank_by() -> None:
    cands = [
        cand("a", top_score=0.9, probe_fused=-1.0, avg_logprob=-0.1),
        cand("b", top_score=0.1, probe_fused=2.0, avg_logprob=-3.0),
    ]
    assert rule_chooser("R0")(cands) == 0
    assert rule_chooser("R1")(cands) == 1
    assert rule_chooser("R2")(cands) == 0


def test_average_ranks_give_tied_values_the_mean_rank() -> None:
    assert average_ranks([3.0, 1.0, 3.0, 0.5]) == [1.5, 3.0, 1.5, 4.0]


def test_rank_sum_takes_the_candidate_best_on_most_features() -> None:
    feats = ("f1", "f2", "f3")
    cands = [
        cand("a", f1=9.0, f2=0.0, f3=0.0),
        cand("b", f1=1.0, f2=5.0, f3=5.0),
        cand("c", f1=2.0, f2=1.0, f3=1.0),
    ]
    # 順位の和：a = 1 + 3 + 3，b = 3 + 1 + 1，c = 2 + 2 + 2
    assert choose_by_rank_sum(cands, feats) == 1


def test_rank_sum_ranks_only_the_answered_candidates() -> None:
    cands = [cand("a", True, f=9.0), cand("b", f=1.0), cand("c", f=2.0)]
    assert choose_by_rank_sum(cands, ("f",)) == 2


def test_ignore_unknown_lets_an_unknown_candidate_win_on_its_feature() -> None:
    cands = [cand("a", True, top_score=0.9), cand("b", top_score=0.5)]
    assert choose_by_feature(cands, "top_score", ignore_unknown=True) == 0


def test_ignore_unknown_rank_sum_ranks_every_candidate() -> None:
    cands = [cand("a", True, f=9.0), cand("b", f=1.0), cand("c", f=2.0)]
    assert choose_by_rank_sum(cands, ("f",), ignore_unknown=True) == 0


@pytest.mark.parametrize(
    "scores",
    [
        [(True, 0.9), (False, 0.5), (False, 0.7)],
        [(False, 0.4), (False, 0.4), (True, None)],
        [(True, None), (True, -1.0)],
    ],
)
def test_r0_prime_is_the_live_max_score_rule(scores: list[tuple[bool, float | None]]) -> None:
    owner = [OwnerCandidate(str(i), "x", s, u) for i, (u, s) in enumerate(scores)]
    cands = [cand(str(i), u, top_score=s) for i, (u, s) in enumerate(scores)]
    assert rule_chooser("R0", ignore_unknown=True)(cands) == choose_max_score(owner).index


def test_unknown_rule_name_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown rule"):
        rule_chooser("R9")


def test_select_rule_prefers_the_simpler_rule_on_a_tie() -> None:
    assert select_rule({"R4": 10, "R1": 10, "R0": 9}) == "R1"
    assert select_rule({"R3": 11, "R1": 10}) == "R3"
    assert RULE_ORDER[0] == "R0"


# ── 実行の単位の評価 ──────────────────────────────────────────────────────


def _row(qid: str, bank: str, cands: list[dict[str, Any]], chosen: int) -> dict[str, Any]:
    return {
        "qid": qid,
        "bank": bank,
        "error": None,
        "owner_answer": {"candidates": cands, "chosen_index": chosen},
    }


def _c(source: str, unknown: bool, top: float, fused: float) -> dict[str, Any]:
    return {
        "source": source,
        "answer": "x",
        "unknown": unknown,
        "top_score": top,
        "probe_fused": fused,
        "context_doc_ids": [f"{source}/1", f"{source}/2"],
    }


def _write_run(run_dir: Path) -> None:
    """3 問：q1 は (b)，q2 は R0 で (c)・R1 で (b)，q3 は (a)．q4 は失敗．"""
    rows = [
        _row("q1", "a", [_c("a", False, 0.9, 1.0), _c("b", True, 0.5, -1.0)], 0),
        _row("q2", "a", [_c("a", False, 0.4, 2.0), _c("b", False, 0.6, -2.0)], 1),
        _row("q3", "z", [_c("a", False, 0.4, 0.0), _c("b", False, 0.3, 1.0)], 0),
        {"qid": "q4", "bank": "a", "error": "boom"},
    ]
    judged = [
        ("q1", 0, True),
        ("q1", 1, False),
        ("q2", 0, True),
        ("q2", 1, False),
        ("q3", 0, False),
        ("q3", 1, None),
    ]
    run_dir.mkdir()
    (run_dir / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (run_dir / "candidate_judgements.jsonl").write_text(
        "".join(json.dumps({"qid": q, "index": i, "correct": c}) + "\n" for q, i, c in judged)
    )
    (run_dir / "judgements.jsonl").write_text(
        "".join(
            json.dumps({"qid": q, "correct": c, "answer_overlap_lcs": 0.0}) + "\n"
            for q, c in (("q1", True), ("q2", False), ("q3", False))
        )
    )


def test_apply_rule_counts_failures_and_unjudged_candidates_as_incorrect(tmp_path: Path) -> None:
    _write_run(tmp_path / "run")
    rows = read_results(tmp_path / "run" / "results.jsonl")
    judged = {("q1", 0): True, ("q2", 0): True, ("q2", 1): False, ("q3", 1): None}

    r0 = apply_rule(rows, judged, "R0")
    r1 = apply_rule(rows, judged, "R1")

    assert [o.correct for o in r0] == [True, False, False, False]
    assert [o.correct for o in r1] == [True, True, False, False]
    assert r0[3].chosen_index is None


def test_evaluate_reports_accuracy_segments_and_ceilings_per_rule(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_run(run)

    report, _ = evaluate(run, ["R0", "R1", "R3"], run / "ce_features.jsonl")

    assert report["rules_skipped_no_ce"] == ["R3"]
    assert report["per_rule"]["R0"]["correct"] == 1
    assert report["per_rule"]["R1"]["correct"] == 2
    assert report["per_rule"]["R0"]["n"] == 4  # 失敗した問も分母に入る
    seg = report["per_rule"]["R0"]["segments"]
    assert (seg["a_owner_out"]["n"], seg["b_owner_chosen"]["n"]) == (2, 1)
    assert seg["c_owner_in_not_chosen"]["n"] == 1
    assert report["selected_rule"] == "R1"
    # R0 で (c) になった q2 の持ち主の候補は正解（(c) の天井の推定）
    assert report["ceilings"]["owner_candidate_in_segment_c"] == {"n": 1, "accuracy": 1.0}
    assert report["ceilings"]["any_candidate_correct"] == pytest.approx(2 / 4)
    assert report["r0_matches_live_choice"] == 1.0
    assert report["r0_vs_main_judgement"]["agreement"] == 1.0
    assert report["unknown_share_by_owner"] == {"owner_unknown": 0.0, "non_owner_unknown": 0.25}


def test_evaluate_uses_the_cross_encoder_scores_when_present(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_run(run)
    ce = [("q1", 0, 0.1), ("q1", 1, 0.9), ("q2", 0, 5.0), ("q2", 1, 1.0), ("q3", 0, 0.0)]
    (run / "ce_features.jsonl").write_text(
        "".join(json.dumps({"qid": q, "index": i, "ce_max": v}) + "\n" for q, i, v in ce)
    )

    report, outcomes = evaluate(run, ["R3"], run / "ce_features.jsonl")

    # q1 の b は UNKNOWN なので，得点が高くても採らない．q2 は a を採る
    assert [o.chosen_index for o in outcomes["R3"][:2]] == [0, 0]
    assert report["per_rule"]["R3"]["correct"] == 2


def test_evaluate_with_ignore_unknown_considers_unknown_candidates(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_run(run)
    ce = [("q1", 0, 0.1), ("q1", 1, 0.9), ("q2", 0, 5.0), ("q2", 1, 1.0), ("q3", 0, 0.0)]
    (run / "ce_features.jsonl").write_text(
        "".join(json.dumps({"qid": q, "index": i, "ce_max": v}) + "\n" for q, i, v in ce)
    )

    report, outcomes = evaluate(run, ["R3"], run / "ce_features.jsonl", ignore_unknown=True)

    # q1 の b は UNKNOWN だが除外しないので，得点の高い b を採る（不正解）
    assert [o.chosen_index for o in outcomes["R3"][:2]] == [1, 0]
    assert report["per_rule"]["R3"]["correct"] == 1
    assert report["ignore_unknown"] is True


def test_derived_run_records_whether_unknown_was_ignored(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_run(run)
    _, outcomes = evaluate(run, ["R0"], ignore_unknown=True)
    out = tmp_path / "derived"

    write_derived_run(run, outcomes["R0"], "R0", out, ignore_unknown=True)

    meta = json.loads((out / "derived_from.json").read_text())
    assert (meta["select_rule"], meta["ignore_unknown"]) == ("R0", True)


def test_derived_run_feeds_atrium_compare_without_answer_text(tmp_path: Path) -> None:
    run = tmp_path / "run"
    _write_run(run)
    _, outcomes = evaluate(run, ["R1"])
    out = tmp_path / "derived"

    write_derived_run(run, outcomes["R1"], "R1", out)

    rows = merge_judgements(read_results(out / "results.jsonl"), out / "judgements.jsonl")
    assert {r["qid"]: r.get("correct") for r in rows} == {
        "q1": True,
        "q2": True,
        "q3": False,
        "q4": None,
    }
    assert "answer" not in (out / "results.jsonl").read_text()
    assert json.loads((out / "derived_from.json").read_text())["select_rule"] == "R1"


def test_cross_encoder_features_score_each_candidate_context_against_the_question() -> None:
    rows = [_row("q1", "a", [_c("a", False, 0.9, 1.0), _c("b", True, 0.5, -1.0)], 0)]
    calls: list[tuple[str, list[str]]] = []

    def score(question: str, texts: list[str]) -> list[float]:
        calls.append((question, list(texts)))
        return [float(len(t)) for t in texts]

    out = cross_encoder_features(
        rows,
        {"q1": "who?"},
        {"a/1": "t\nx", "a/2": "t\nxyz", "b/1": "t\nlonger", "b/2": "t"},
        score,  # type: ignore[arg-type]
    )

    assert calls[0] == ("who?", ["t\nx", "t\nxyz"])
    assert [(o["source"], o["ce_max"], o["ce_top1"]) for o in out] == [
        ("a", 5.0, 3.0),
        ("b", 8.0, 8.0),
    ]
