"""プロンプトの書式と，LLM の出力からの選択肢の抽出の仕様（RAGRoute と同じ規則）．"""

from __future__ import annotations

import hashlib
import json

import pytest

from atrium.config import LlmConfig
from atrium.prompts import (
    ENRON_DIRECT_TEMPLATE,
    build_messages,
    extract_choice,
    extract_free_answer,
    format_options,
)
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


def test_free_answer_takes_the_last_answer_line() -> None:
    from atrium.prompts import extract_free_answer

    out = "Reasoning: Answer: is not here\nAnswer: Ameren sent a termination notice."
    assert extract_free_answer(out) == "Ameren sent a termination notice."
    assert extract_free_answer("No marker at all.") == "No marker at all."


_ENRON_DOCS = [
    RetrievedDoc(doc_id="d0", title="Heart", content="pumps blood", score=1.0),
    RetrievedDoc(doc_id="d1", title="", content="second body", score=0.5),
]
# Iteration 27 の前の fill_in の messages（json.dumps）の sha256．既定の挙動がバイト単位で変わらないことの固定
_FILL_IN_MESSAGES_SHA256 = "2c9630f606883802900f06f681849c212bc02c274b85c618d07ddacdac315c20"
_DIRECT_TEMPLATE_SHA256 = "7a59a3ac559ecb5676868443491061c547bae44b5bcabb1394590667fd08222b"


def test_default_enronqa_messages_are_byte_identical_to_fill_in() -> None:
    messages = build_messages("enronqa", "Who?", _ENRON_DOCS, {})
    assert hashlib.sha256(json.dumps(messages).encode()).hexdigest() == _FILL_IN_MESSAGES_SHA256
    assert build_messages("enronqa", "Who?", _ENRON_DOCS, {}, answer_format="fill_in") == messages


def test_answer_prompt_format_defaults_to_fill_in() -> None:
    assert LlmConfig.model_fields["answer_prompt_format"].default == "fill_in"


def test_direct_template_text_is_fixed() -> None:
    digest = hashlib.sha256(ENRON_DIRECT_TEMPLATE.encode()).hexdigest()
    assert digest == _DIRECT_TEMPLATE_SHA256


def test_direct_messages_keep_system_and_place_context_and_question() -> None:
    fill_in = build_messages("enronqa", "Who?", _ENRON_DOCS, {})
    direct = build_messages("enronqa", "Who?", _ENRON_DOCS, {}, answer_format="direct")
    assert direct[0] == fill_in[0]
    assert (
        "Emails:\nDocument [0] (Title: Heart) pumps blood\nDocument [1] (Title: Doc 1)"
        in (direct[1]["content"])
    )
    assert "Question: Who?\n\nFirst explain your reasoning" in direct[1]["content"]
    assert "Follow the following format" not in direct[1]["content"]


def test_direct_with_allow_unknown_is_rejected() -> None:
    with pytest.raises(ValueError, match="answer_format=direct"):
        build_messages("enronqa", "Q?", _ENRON_DOCS, {}, allow_unknown=True, answer_format="direct")


def test_direct_for_other_datasets_is_rejected() -> None:
    with pytest.raises(ValueError, match="answer_format=direct"):
        build_messages("medrag", "Q?", [], {"A": "x"}, answer_format="direct")


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Because the email says so.\nAnswer: Bob sent it.", "Bob sent it."),
        ("Bob sent it.", "Bob sent it."),
    ],
)
def test_extract_free_answer_from_direct_output(output: str, expected: str) -> None:
    assert extract_free_answer(output) == expected
