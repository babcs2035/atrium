"""experiment.sniffio_lookup（sniffio の import の扱い）の仕様：blocked は探索だけを省き，httpcore の返す値と probe の得点は search と同じ．"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from httpcore._synchronization import current_async_library

from atrium.config import AtriumConfig
from atrium.requester import apply_sniffio_lookup
from atrium.routing.advert import FloodScoreRouter
from tests.conftest import with_experiment
from tests.test_requester import DIM, _cfg, _questions, _requester, corpus  # noqa: F401

_ABSENT = object()


@pytest.fixture(autouse=True)
def restore_sniffio_entry() -> Iterator[None]:
    """各テストの後に sys.modules の sniffio の項目（無い・None・モジュール）を元へ戻す．"""
    before = sys.modules.get("sniffio", _ABSENT)
    sys.modules.pop("sniffio", None)
    yield
    sys.modules.pop("sniffio", None)
    if before is not _ABSENT:
        sys.modules["sniffio"] = before  # type: ignore[assignment]


class CountingFinder(MetaPathFinder):
    """sniffio について呼ばれた回数を数えるだけの探索器（何も見つけない）．"""

    def __init__(self) -> None:
        self.sniffio_calls = 0

    def find_spec(
        self, fullname: str, path: object = None, target: object = None
    ) -> ModuleSpec | None:
        if fullname == "sniffio":
            self.sniffio_calls += 1
        return None


@pytest.fixture
def counting_finder(monkeypatch: pytest.MonkeyPatch) -> CountingFinder:
    finder = CountingFinder()
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    return finder


def test_default_sniffio_lookup_is_search(cfg: AtriumConfig) -> None:
    assert cfg.experiment.sniffio_lookup == "search"


def test_search_leaves_sys_modules_unchanged() -> None:
    apply_sniffio_lookup("search")
    assert "sniffio" not in sys.modules


def test_blocked_without_sniffio_returns_asyncio_and_skips_the_path_search(
    counting_finder: CountingFinder,
) -> None:
    installed = apply_sniffio_lookup("blocked")
    counting_finder.sniffio_calls = 0  # apply_sniffio_lookup 自身の find_spec の分を除く
    assert installed is False
    assert current_async_library() == "asyncio"
    assert counting_finder.sniffio_calls == 0


def test_search_without_sniffio_returns_asyncio_but_searches_every_call(
    counting_finder: CountingFinder,
) -> None:
    apply_sniffio_lookup("search")
    counting_finder.sniffio_calls = 0  # apply_sniffio_lookup 自身の find_spec の分を除く
    assert current_async_library() == "asyncio"
    assert current_async_library() == "asyncio"
    assert counting_finder.sniffio_calls == 2


def test_blocked_does_not_replace_a_loaded_sniffio() -> None:
    fake = ModuleType("sniffio")
    sys.modules["sniffio"] = fake
    installed = apply_sniffio_lookup("blocked")
    assert installed is True
    assert sys.modules["sniffio"] is fake


def test_blocked_twice_still_reports_not_installed() -> None:
    apply_sniffio_lookup("blocked")
    assert apply_sniffio_lookup("blocked") is False
    assert sys.modules["sniffio"] is None


async def test_probe_scores_with_blocked_equal_search(
    cfg: AtriumConfig,
    tmp_path: Any,
    corpus: Any,  # noqa: F811
) -> None:
    scores = {}
    for mode in ("search", "blocked"):
        sys.modules.pop("sniffio", None)
        apply_sniffio_lookup(mode)
        question = _questions(1)[0]
        requester = await _requester(
            with_experiment(_cfg(cfg, "retrieval_only"), sniffio_lookup=mode),
            tmp_path,
            FloodScoreRouter(1),
            {question.qid: (np.arange(1, DIM + 1) / DIM).astype(np.float32)},
        )
        embeddings = requester._embed(question, {"timings": {}})  # noqa: SLF001
        scores[mode] = await requester._probe(embeddings, {"timings": {}})  # noqa: SLF001
    assert scores["blocked"] == scores["search"]
