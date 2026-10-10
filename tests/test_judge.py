"""EnronQA の回答の採点（atrium.judge）と，採点の結果の取り込みの仕様．"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from atrium.analysis import accuracy_metrics, merge_judgements
from atrium.config import AtriumConfig
from atrium.judge import JUDGE_PROMPT, JudgeItem, cohen_kappa, judge_items, lcs_ratio, parse_verdict
from tests.conftest import HostDispatchTransport, ollama_mock


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


async def test_judge_items_retries_a_failed_request_on_another_ollama(cfg: AtriumConfig) -> None:
    # 1 台が 500 を返しても，その要求は別の Ollama へ振り替えられ，全件が採点される（2026-10-10 の κ の中断）
    broken = httpx.MockTransport(lambda _: httpx.Response(500, text="llama runner crashed"))
    healthy = ollama_mock(lambda _: "Reasoning: same.\nAnswer: Correct")
    transport = HostDispatchTransport({"broken": broken, "healthy": healthy})
    items = [JudgeItem(f"k{i}", "E", "Q", "G", "A") for i in range(4)]
    out = await judge_items(
        items, ["http://broken:11434", "http://healthy:11434"], "m", cfg, transport=transport
    )
    assert {k: v[0] for k, v in out.items()} == {f"k{i}": True for i in range(4)}


async def test_judge_items_raises_when_every_ollama_fails(cfg: AtriumConfig) -> None:
    broken = httpx.MockTransport(lambda _: httpx.Response(500, text="down"))
    with pytest.raises(httpx.HTTPStatusError):
        await judge_items(
            [JudgeItem("k", "E", "Q", "G", "A")],
            ["http://broken:11434"],
            "m",
            cfg,
            transport=broken,
        )
