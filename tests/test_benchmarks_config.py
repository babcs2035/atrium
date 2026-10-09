"""質問の変換・読み込みと，config.yaml の検証の仕様．"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _repository_config_dict() -> dict[str, Any]:
    """config.yaml を辞書のまま読む（一部のキーを書き換えた設定の検証に使う）．"""
    raw: dict[str, Any] = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    return raw


def test_config_rejects_host_with_two_roles() -> None:
    raw = _repository_config_dict()
    raw["cluster"]["gpu_workers"] = [{"host": raw["cluster"]["requester"]["host"]}]
    with pytest.raises(ValidationError, match="only one role"):
        AtriumConfig.model_validate(raw)


def test_config_rejects_duplicate_host_within_a_role() -> None:
    raw = _repository_config_dict()
    experts = raw["cluster"]["experts"]
    raw["cluster"]["experts"] = [experts[0], experts[0]]
    with pytest.raises(ValidationError, match="only one role"):
        AtriumConfig.model_validate(raw)


def test_config_rejects_e0_host_that_is_not_an_expert() -> None:
    raw = _repository_config_dict()
    raw["e0"]["hosts"] = [raw["cluster"]["requester"]["host"]]
    with pytest.raises(ValidationError, match="not in cluster.experts"):
        AtriumConfig.model_validate(raw)


def test_config_rejects_removed_hugepages_key() -> None:
    raw = _repository_config_dict()
    raw["cluster"]["release_hugepages"] = True
    with pytest.raises(ValidationError):
        AtriumConfig.model_validate(raw)


def test_device_ssh_user_overrides_cluster_default() -> None:
    raw = _repository_config_dict()
    raw["cluster"]["experts"][0]["ssh_user"] = "alice"
    cluster = AtriumConfig.model_validate(raw).cluster
    assert cluster.ssh_user_of(cluster.experts[0]) == "alice"
    assert cluster.ssh_user_of(cluster.experts[1]) == cluster.ssh_user


@pytest.mark.parametrize("key", ["remote_dir", "data_dir"])
@pytest.mark.parametrize("value", ["~/atrium", "atrium"])
def test_config_rejects_non_absolute_directories(key: str, value: str) -> None:
    raw = _repository_config_dict()
    raw["cluster"][key] = value
    with pytest.raises(ValidationError, match="absolute path"):
        AtriumConfig.model_validate(raw)


def test_requester_llm_defaults_to_the_requester_host() -> None:
    raw = _repository_config_dict()
    raw["cluster"].pop("requester_llm", None)
    cluster = AtriumConfig.model_validate(raw).cluster
    assert cluster.requester_llm_host() == cluster.requester.host


def test_requester_llm_host_may_not_have_another_role() -> None:
    raw = _repository_config_dict()
    raw["cluster"]["requester_llm"] = {"host": raw["cluster"]["experts"][0]["host"]}
    with pytest.raises(ValidationError, match="only one role"):
        AtriumConfig.model_validate(raw)


def test_config_without_enronqa_section_still_loads() -> None:
    raw = _repository_config_dict()
    raw["data"].pop("enronqa", None)
    cfg = AtriumConfig.model_validate(raw)
    assert cfg.data.enronqa is None
    with pytest.raises(ValueError, match="data.enronqa is required"):
        cfg.data.sources_of("enronqa")


def test_enronqa_sources_are_the_150_inboxes(cfg: AtriumConfig) -> None:
    sources = cfg.data.sources_of("enronqa")
    assert len(sources) == len(set(sources)) == 150
