"""d0009 A2 の 3 分類（atrium.fabrication_eval）の仕様：棄権の判定の解析・UNKNOWN の字句の優先・集計・ブートストラップ・ゲート．"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from atrium import fabrication_eval as fe
from atrium.config import AtriumConfig
from atrium.judge import JudgeItem
from atrium.paths import dataset_paths


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("It only says nothing was found.\nAnswer: NOT_ATTEMPTED", True),
        ("It names a person.\nAnswer: ATTEMPTED", False),
        ("Reason.\n**Answer**: **not attempted**", True),
        ("first\nAnswer: NOT_ATTEMPTED\nrevised\nAnswer: ATTEMPTED", False),
        ("no final line", None),
    ],
)
def test_parse_abstain_reads_the_last_answer_line(output: str, expected: bool | None) -> None:
    assert fe.parse_abstain(output) is expected


def test_not_attempted_is_not_confused_with_attempted() -> None:
    assert fe.parse_abstain("Answer: NOT_ATTEMPTED") is True


@pytest.mark.parametrize(
    ("correct", "label", "expected"),
    [
        (None, None, (fe.LABEL_UNJUDGED, False)),
        (True, fe.AbstainLabel(fe.STATUS_NOT_SENT, None), (fe.LABEL_CORRECT, False)),
        (False, fe.AbstainLabel(fe.STATUS_LEXICAL, True), (fe.LABEL_DONT_KNOW, True)),
        (False, fe.AbstainLabel(fe.STATUS_MODEL, True), (fe.LABEL_DONT_KNOW, False)),
        (False, fe.AbstainLabel(fe.STATUS_MODEL, False), (fe.LABEL_FABRICATED, False)),
        (False, fe.AbstainLabel(fe.STATUS_UNPARSED, None), (fe.LABEL_ABSTAIN_UNPARSED, False)),
    ],
)
def test_classify_follows_the_three_way_rule(
    correct: bool | None, label: fe.AbstainLabel | None, expected: tuple[str, bool]
) -> None:
    assert fe.classify(correct, label) == expected


def test_classify_rejects_an_incorrect_answer_without_an_abstain_judgement() -> None:
    with pytest.raises(ValueError, match="no abstain judgement"):
        fe.classify(False, None)


def _write_enron_questions(root: Path, qids: list[str]) -> None:
    paths = dataset_paths(root, "enronqa")
    bench = paths.questions.parent
    bench.mkdir(parents=True)
    rows = [
        {"qid": q, "bank": "a", "question": f"Question of {q}?", "options": {}, "answer": "G"}
        for q in qids
    ]
    (bench / "questions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for name in ("dev_questions.jsonl", "train_questions.jsonl"):
        (bench / name).write_text("")


def _write_run(
    run: Path, rows: list[dict[str, object]], correct: dict[tuple[str, int], bool | None]
) -> None:
    """results.jsonl・candidate_judgements.jsonl・judgements.jsonl・ce_features.jsonl の最小の実行．"""
    run.mkdir(parents=True)
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (run / "candidate_judgements.jsonl").write_text(
        "".join(
            json.dumps({"qid": q, "index": i, "correct": c}) + "\n" for (q, i), c in correct.items()
        )
    )
    (run / "judgements.jsonl").write_text(
        "".join(
            json.dumps({"qid": r["qid"], "correct": correct.get((str(r["qid"]), 0))}) + "\n"
            for r in rows
        )
    )
    ce = [
        {"qid": q, "index": i, "ce_max": float(3 - i)}  # 候補 0 の得点が最高
        for (q, i) in correct
    ]
    (run / "ce_features.jsonl").write_text("".join(json.dumps(c) + "\n" for c in ce))


def _row(qid: str, answers: list[str], owner_index: int | None = 0) -> dict[str, object]:
    return {
        "qid": qid,
        "bank": "own",
        "error": None,
        "final_answer": answers[0],
        "owner_answer": {
            "candidates": [
                {
                    "source": "own" if i == owner_index else f"x{i}",
                    "answer": a,
                    "unknown": a.upper().startswith("UNKNOWN"),
                }
                for i, a in enumerate(answers)
            ]
        },
    }


def test_abstain_judge_sends_only_incorrect_non_unknown_answers_with_question_and_answer_only(
    cfg: AtriumConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    _write_enron_questions(data, ["q0"])
    run = tmp_path / "run"
    _write_run(
        run,
        [_row("q0", ["Right answer", "UNKNOWN. nothing", "Bob did it", "Bob did it"])],
        {("q0", 0): True, ("q0", 1): False, ("q0", 2): False, ("q0", 3): False},
    )
    asked: list[JudgeItem] = []

    async def fake_judge_items(
        items: list[JudgeItem], *args: object, **kwargs: object
    ) -> dict[str, tuple[bool | None, str]]:
        asked.extend(items)
        assert kwargs["prompt_template"] == fe.ABSTAIN_PROMPT
        return {i.key: (False, "Answer: ATTEMPTED") for i in items}

    monkeypatch.setattr(fe, "judge_items", fake_judge_items)
    summary = fe.abstain_judge_run(
        cfg, dataset_paths(data, "enronqa"), run, tmp_path / "labels", ["u"], "m"
    )

    # 正解・UNKNOWN の字句は送らず，同じ回答の文字列は 1 回だけ送る．メール・正解は空で渡す
    assert [(i.question, i.answer, i.email, i.gold) for i in asked] == [
        ("Question of q0?", "Bob did it", "", "")
    ]
    labels = fe.read_labels(tmp_path / "labels" / "run" / fe.CANDIDATE_ABSTAIN)
    assert [labels[("q0", i)].status for i in range(4)] == [
        fe.STATUS_NOT_SENT,
        fe.STATUS_LEXICAL,
        fe.STATUS_MODEL,
        fe.STATUS_MODEL,
    ]
    assert summary["n_unjudged"] == 0


def test_abstain_judge_resends_an_unparsed_answer_once_and_keeps_it_unparsed_if_it_fails_again(
    cfg: AtriumConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    _write_enron_questions(data, ["q0"])
    run = tmp_path / "run"
    _write_run(run, [_row("q0", ["Bob", "Ann"])], {("q0", 0): False, ("q0", 1): False})
    calls: list[str] = []

    async def fake_judge_items(
        items: list[JudgeItem], *args: object, **kwargs: object
    ) -> dict[str, tuple[bool | None, str]]:
        calls.extend(i.key for i in items)
        # Ann は 2 回とも取り出せない．Bob は初回で判定できる
        return {i.key: (False if i.answer == "Bob" else None, "x") for i in items}

    monkeypatch.setattr(fe, "judge_items", fake_judge_items)
    fe.abstain_judge_run(cfg, dataset_paths(data, "enronqa"), run, tmp_path / "labels", ["u"], "m")

    # 最終回答 q0（Bob）は候補 0 と同じ文字列なので 1 回にまとまる
    assert sorted(calls) == ["q0", "q0#1", "q0#1" + fe.RETRY_SUFFIX]
    labels = fe.read_labels(tmp_path / "labels" / "run" / fe.CANDIDATE_ABSTAIN)
    assert labels[("q0", 1)].status == fe.STATUS_UNPARSED


def test_unparsed_abstain_is_in_the_upper_bound_of_fabrication_but_not_in_the_lower() -> None:
    table = np.array(
        [[0, 1, 1, 2, 1, 1]], dtype=np.float64
    )  # correct, dk_lex, dk_model, fab, unparsed, unjudged
    stats = fe.column_stats(table)

    # 判定不能の 1 件は分母から除く（valid = 5）
    assert stats["F"].value() == pytest.approx(2 / 5)
    assert stats["F_upper"].value() == pytest.approx(3 / 5)
    # 字句だけの最大値は UNKNOWN の字句以外の不正解（dk_model + fab + unparsed）
    assert stats["F_lexmax"].value() == pytest.approx(4 / 5)
    assert stats["unjudged"].value() == pytest.approx(1 / 6)


def test_f_rand_is_the_mean_over_questions_of_the_fabricated_share_of_candidates() -> None:
    owner = np.array([[0, 0, 0, 1, 0, 0], [1, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 1]], dtype=np.float64)
    other = np.array([[0, 1, 0, 1, 0, 0], [0, 0, 1, 0, 0, 0], [0, 0, 0, 0, 0, 1]], dtype=np.float64)

    stat = fe.random_choice_stat([owner, other])

    # 問 1 は 2/3，問 2 は 0，問 3 は候補が全て判定不能で平均に入れない
    assert stat.value() == pytest.approx((2 / 3 + 0) / 2)


def test_bootstrap_interval_is_reproducible_and_covers_the_point_estimate() -> None:
    rng = np.random.default_rng(0)
    num = rng.integers(0, 2, 300).astype(np.float64)
    stat = fe.Stat(num, np.ones(300))

    first = fe.bootstrap_interval([(1.0, stat)], samples=500, seed=12)
    second = fe.bootstrap_interval([(1.0, stat)], samples=500, seed=12)

    assert first == second
    assert first[0] < stat.value() < first[1]


def test_paired_bootstrap_of_identical_stats_has_a_zero_width_interval() -> None:
    stat = fe.Stat(np.array([1.0, 0.0, 1.0, 0.0]), np.ones(4))

    assert fe.bootstrap_interval([(1.0, stat), (-1.0, stat)], samples=200, seed=12) == (0.0, 0.0)


def _rows_for_report() -> tuple[list[dict[str, object]], dict[tuple[str, int], bool | None]]:
    rows = [_row(f"q{i}", ["Right", "UNKNOWN", "Made up"]) for i in range(4)]
    correct: dict[tuple[str, int], bool | None] = {}
    for i in range(4):
        correct |= {(f"q{i}", 0): True, (f"q{i}", 1): False, (f"q{i}", 2): False}
    return rows, correct


def _write_labels(labels_dir: Path, run: str, abstain_of_made_up: bool) -> None:
    target = labels_dir / run
    target.mkdir(parents=True)
    lines = []
    for i in range(4):
        lines += [
            {"qid": f"q{i}", "index": 0, "source": "own", "status": "not_sent", "abstain": None},
            {
                "qid": f"q{i}",
                "index": 1,
                "source": "x1",
                "status": "lexical_unknown",
                "abstain": True,
            },
            {
                "qid": f"q{i}",
                "index": 2,
                "source": "x2",
                "status": "model",
                "abstain": abstain_of_made_up,
            },
        ]
    (target / fe.CANDIDATE_ABSTAIN).write_text("".join(json.dumps(x) + "\n" for x in lines))


def test_evaluate_fabrication_separates_owner_and_non_owner_candidates_and_withholds_without_the_gate(
    tmp_path: Path,
) -> None:
    rows, correct = _rows_for_report()
    a5, a6 = tmp_path / "a5", tmp_path / "a6"
    _write_run(a5, rows, correct)
    _write_run(a6, rows, correct)
    labels = tmp_path / "labels"
    _write_labels(labels, "a5", abstain_of_made_up=True)
    _write_labels(labels, "a6", abstain_of_made_up=False)

    report, verdict = fe.evaluate_fabrication(a5, a6, None, labels, None)

    assert report["n_common_questions"] == 4
    # A5 は持ち主以外の候補 2 件がどちらも「わからない」，A6 は 1 件が誤答
    assert report["runs"]["A5"]["candidates_non_owner"]["F"]["value"] == 0.0
    assert report["runs"]["A6"]["candidates_non_owner"]["F"]["value"] == pytest.approx(0.5)
    assert report["runs"]["A6"]["candidates_owner"]["correct"] == 1.0
    # ゲートが無いので判定は保留し，下限・上限だけを出す
    assert verdict["status"] == "withheld"
    assert verdict["hypotheses"] is None
    assert verdict["range_only"]["A6"]["F_non"] == [pytest.approx(0.5), pytest.approx(0.5)]
    # 候補 0（持ち主．cross-encoder の得点が最高）を選ぶので，選んだ回答に誤答は無い
    assert report["runs"]["A6"]["selected"]["R3p"]["F"]["value"] == 0.0


def test_evaluate_fabrication_judges_h_f1_when_the_gate_passed(tmp_path: Path) -> None:
    rows, correct = _rows_for_report()
    a5, a6 = tmp_path / "a5", tmp_path / "a6"
    _write_run(a5, rows, correct)
    _write_run(a6, rows, correct)
    labels = tmp_path / "labels"
    _write_labels(labels, "a5", abstain_of_made_up=True)
    _write_labels(labels, "a6", abstain_of_made_up=False)

    _, verdict = fe.evaluate_fabrication(a5, a6, None, labels, {"passed": True})

    assert verdict["status"] == "judged"
    h1 = verdict["hypotheses"]["H-F1"]
    assert h1["difference"] == pytest.approx(0.5)
    assert h1["holds"] is True


def test_gate_requires_agreement_and_kappa_and_keeps_the_reference_model_out_of_the_gate() -> None:
    key = [
        {"id": f"m{i}", "run": "A6", "group": "non_owner", "model_label": label}
        for i, label in enumerate(["dont_know"] * 10 + ["fabricated"] * 10)
    ]
    manual = {f"m{i}": i < 10 for i in range(20)}  # 全件一致

    perfect = fe.gate_report(key, manual)
    assert perfect["agreement"] == 1.0
    assert perfect["kappa"] == 1.0
    assert perfect["passed"] is True

    manual_with_errors = {
        f"m{i}": (i < 10) != (i in (0, 1, 10, 11)) for i in range(20)
    }  # 4 件が不一致
    failed = fe.gate_report(key, manual_with_errors, {k["id"]: False for k in key})
    assert failed["agreement"] == pytest.approx(0.8)
    assert failed["passed"] is False
    assert "reference_qwen3_14b" in failed


def test_manual_table_hides_the_model_label_and_takes_only_model_decided_answers(
    tmp_path: Path,
) -> None:
    rows, correct = _rows_for_report()
    a5, a6 = tmp_path / "a5", tmp_path / "a6"
    _write_run(a5, rows, correct)
    _write_run(a6, rows, correct)
    labels = tmp_path / "labels"
    _write_labels(labels, "a5", abstain_of_made_up=True)
    _write_labels(labels, "a6", abstain_of_made_up=False)
    questions = {f"q{i}": f"Question of q{i}?" for i in range(4)}

    table, key = fe.build_manual_table(
        {"A5": a5, "A6": a6}, labels, [f"q{i}" for i in range(4)], questions
    )

    # UNKNOWN の字句の候補（判定モデルが分類していない）は含めない．A5 の 4 件は わからない，A6 の 4 件は 誤答
    assert len(table) == 8
    assert all(set(t) == {"id", "question", "answer"} for t in table)
    assert {(k["run"], k["model_label"]) for k in key} == {
        ("A5", "dont_know"),
        ("A6", "fabricated"),
    }
    assert [t["id"] for t in table] == [k["id"] for k in key]


def test_abstain_judge_leaves_an_answer_unjudged_when_the_resend_could_not_be_made(
    cfg: AtriumConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    _write_enron_questions(data, ["q0"])
    run = tmp_path / "run"
    _write_run(run, [_row("q0", ["Ann"])], {("q0", 0): False})

    async def fake_judge_items(
        items: list[JudgeItem], *args: object, **kwargs: object
    ) -> dict[str, tuple[bool | None, str]]:
        # 初回は取り出せず，送り直しの要求は Ollama の停止で失敗する（戻り値に入らない）
        return {i.key: (None, "x") for i in items if not i.key.endswith(fe.RETRY_SUFFIX)}

    monkeypatch.setattr(fe, "judge_items", fake_judge_items)
    summary = fe.abstain_judge_run(
        cfg, dataset_paths(data, "enronqa"), run, tmp_path / "labels", ["u"], "m"
    )

    assert summary["n_unjudged"] == 1
    assert not (tmp_path / "labels" / "run").exists()
