"""EnronQA の回答の採点（atrium.judge）と，採点の結果の取り込みの仕様．"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atrium.analysis import accuracy_metrics, merge_judgements
from atrium.judge import JUDGE_PROMPT, cohen_kappa, lcs_ratio, parse_verdict


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Reasoning: same fact.\nAnswer: Correct", True),
        ("Reasoning: ...\nAnswer: Yes, the answer matches.", True),
        ("Reasoning: ...\nAnswer: Incorrect", False),
        ("Reasoning: ...\nAnswer: The answer is not correct.", False),
        ("Reasoning: ...\nAnswer: False", False),
        ("Reasoning: nothing to conclude", None),
    ],
)
def test_verdict_reads_the_last_answer_line(output: str, expected: bool | None) -> None:
    assert parse_verdict(output) is expected


def test_judge_prompt_follows_the_paper_format() -> None:
    text = JUDGE_PROMPT.format(email="E", question="Q", gold="G", answer="A")
    assert "Correct Answer: G\nStudent Answer: A\nReasoning: Let's think step by step" in text
    assert "${produce the correct}" in text


def test_lcs_ratio_is_the_share_of_answer_words_in_order_in_the_email() -> None:
    assert lcs_ratio("Ameren sent a notice", "Hi. Ameren then sent us a termination notice.") == 1.0
    assert lcs_ratio("notice Ameren", "Ameren notice") == 0.5
    assert lcs_ratio("", "anything") == 0.0


def test_cohen_kappa_is_one_for_identical_and_zero_for_chance_level_agreement() -> None:
    assert cohen_kappa([True, False, True], [True, False, True]) == 1.0
    assert cohen_kappa([True, True, False, False], [True, False, True, False]) == 0.0


def test_merged_judgements_set_correctness_and_count_unparsed(tmp_path: Path) -> None:
    path = tmp_path / "judgements.jsonl"
    lines = [
        {"qid": "q1", "correct": True, "answer_overlap_lcs": 0.5},
        {"qid": "q2", "correct": None, "answer_overlap_lcs": 0.0},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    rows = [{"qid": "q1", "bank": "a", "choice": None}, {"qid": "q2", "bank": "a", "choice": None}]
    merged = merge_judgements(rows, path)
    acc = accuracy_metrics(merged)
    assert acc is not None
    assert acc["overall"]["accuracy"] == 0.5
    assert acc["unparsed_choice_rate"] == 0.5
