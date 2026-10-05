"""プロンプトの書式と，LLM の出力からの選択肢の抽出の仕様（RAGRoute と同じ規則）．"""

from __future__ import annotations

import pytest

from atrium.prompts import build_messages, extract_choice, format_options
from atrium.store import RetrievedDoc


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ('{"step_by_step_thinking": "...", "answer_choice": "B"}', "B"),
        ('{"answer_choice": "C. Aspirin"}', "C"),
        ("The correct one is option D", "D"),
        ("Answer: A", "A"),
        ("I cannot decide", None),
    ],
)
def test_extract_choice(output: str, expected: str | None) -> None:
    assert extract_choice(output) == expected


def test_format_options_sorts_keys_like_medrag() -> None:
    assert format_options({"B": "beta", "A": "alpha"}) == "A. alpha\nB. beta"


def test_build_messages_places_documents_question_and_options() -> None:
    docs = [RetrievedDoc(doc_id="d0", title="Heart", content="pumps blood", score=1.0)]
    messages = build_messages("medrag", "What pumps blood?", docs, {"A": "heart", "B": "lung"})
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "Document [0] (Title: Heart) pumps blood" in user
    assert "What pumps blood?" in user
    assert "A. heart\nB. lung" in user


def test_build_messages_without_documents_keeps_template() -> None:
    messages = build_messages("medrag", "Q?", [], {"A": "x"})
    assert "Here are the relevant documents:\n\n" in messages[1]["content"]
