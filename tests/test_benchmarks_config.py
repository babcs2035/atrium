"""質問の変換・読み込みと，config.yaml の検証の仕様．"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from atrium.benchmarks import (
    feb4rag_to_questions,
    load_questions,
    mirage_to_questions,
    write_questions,
)
from atrium.config import AtriumConfig
from tests.conftest import REPO_ROOT


def test_mirage_questions_get_bank_prefixed_ids() -> None:
    raw = {"medqa": {"0001": {"question": "q", "options": {"A": "a"}, "answer": "A"}}}
    (q,) = mirage_to_questions(raw)
    assert (q.qid, q.bank, q.source_qid, q.answer) == ("medqa/0001", "medqa", "0001", "A")


def test_feb4rag_questions_have_no_options_or_answer() -> None:
    (q,) = feb4rag_to_questions(['{"_id": "7", "text": "Is milk good?", "metadata": {}}'])
    assert (q.qid, q.options, q.answer) == ("feb4rag/7", {}, None)


def test_load_questions_limits_each_bank(tmp_path: Path) -> None:
    raw = {
        bank: {f"{i}": {"question": "q", "options": {"A": "a"}, "answer": "A"} for i in range(3)}
        for bank in ("bioasq", "medqa")
    }
    path = tmp_path / "questions.jsonl"
    write_questions(path, mirage_to_questions(raw))
    loaded = load_questions(path, limit_per_bank=2)
    assert [q.qid for q in loaded] == ["bioasq/0", "bioasq/1", "medqa/0", "medqa/1"]


def test_repository_config_is_valid() -> None:
    raw = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    AtriumConfig.model_validate(raw)


def test_config_rejects_unknown_keys() -> None:
    raw = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["experiment"]["routnig"] = "all"
    with pytest.raises(ValidationError):
        AtriumConfig.model_validate(raw)
