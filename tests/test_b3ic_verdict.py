"""scripts/d0009/b3ic_verdict.py（受信箱の中の文脈の選び方 H-IC の判定・守り・採否・対応ありの差）の単体テスト．"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "d0009" / "b3ic_verdict.py"


def load_script() -> ModuleType:
    """scripts/ はパッケージではないので，パスから読み込む（sys.modules に登録しないと dataclass の解決が失敗する）．"""
    spec = importlib.util.spec_from_file_location("b3ic_verdict", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["b3ic_verdict"] = module
    spec.loader.exec_module(module)
    return module


B3IC = load_script()


@pytest.mark.parametrize(
    ("ci", "expected"),
    [
        ([0.0001, 0.02], "improved"),
        ([0.0, 0.02], "undecided"),
        ([-0.01, 0.01], "undecided"),
        ([-0.02, 0.0], "undecided"),
        ([-0.02, -0.0001], "worse"),
    ],
)
def test_h_ic_boundaries_at_zero(ci: list[float], expected: str) -> None:
    assert B3IC.decide_h_ic(ci) == expected


@pytest.mark.parametrize(
    ("increase", "ok"), [(0.005, True), (0.0050001, False), (-0.01, True), (0.0, True)]
)
def test_fabrication_guard_allows_up_to_plus_0_005(increase: float, ok: bool) -> None:
    assert B3IC.decide_guard(increase) is ok


@pytest.mark.parametrize(
    ("h_ic", "guard", "gates", "expected"),
    [
        ("improved", True, True, "adopted"),
        ("improved", False, True, "undecided"),
        ("undecided", True, True, "undecided"),
        ("worse", True, True, "rejected"),
        ("worse", False, True, "rejected"),
        ("improved", True, False, "indeterminate_gate"),
    ],
)
def test_overall_verdict_combines_h_ic_guard_and_gates(
    h_ic: str, guard: bool, gates: bool, expected: str
) -> None:
    assert B3IC.decide_overall(h_ic, guard, gates) == expected


def test_paired_accuracy_counts_discordant_pairs_and_uses_seed_41() -> None:
    new = [True] * 60 + [False] * 10 + [True] * 30 + [False] * 900
    base = [False] * 60 + [True] * 10 + [True] * 30 + [False] * 900

    result = B3IC.paired_accuracy(new, base)

    assert (result["b_only_new"], result["c_only_base"]) == (60, 10)
    assert result["delta"] == pytest.approx(50 / 1000)
    assert result["bootstrap"] == {"samples": 10_000, "seed": 41, "paired": True}
    assert result["bootstrap_ci95"][0] > 0
    assert result["mcnemar_p"] < 1e-6


def test_split_of_reads_the_question_set_name_from_the_qid() -> None:
    assert B3IC.split_of("enronqa/holdout/23197-1") == "holdout"
    assert B3IC.split_of("enronqa/holdout2/20256-0") == "holdout2"
