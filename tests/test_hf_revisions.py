"""Hugging Face のモデルの版の固定（使うモデルがすべて表にあり，表に無いモデルは読ませない）．"""

from __future__ import annotations

import re

import pytest

from atrium.cli import models_for
from atrium.config import AtriumConfig
from atrium.hf_revisions import HF_MODEL_REVISIONS, pinned_revision


@pytest.mark.parametrize("dataset", ["medrag", "enronqa", "feb4rag"])
def test_every_model_in_use_is_pinned(cfg: AtriumConfig, dataset: str) -> None:
    for name in models_for(cfg, dataset):
        assert name in HF_MODEL_REVISIONS


def test_pins_are_full_commit_hashes() -> None:
    # ブランチ名や短縮形では固定にならない
    for revision in HF_MODEL_REVISIONS.values():
        assert re.fullmatch(r"[0-9a-f]{40}", revision)


def test_unpinned_model_is_rejected() -> None:
    with pytest.raises(KeyError, match="no pinned revision"):
        pinned_revision("example/not-pinned")
