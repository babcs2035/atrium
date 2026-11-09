"""scripts/d0009/b2c_verdict.py（採点の fp16 の測定のゲートと H-F1・H-F2 の判定）の単体テスト．"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "d0009" / "b2c_verdict.py"


def load_script() -> ModuleType:
    """scripts/ はパッケージではないので，パスから読み込む（sys.modules に登録しないと dataclass の解決が失敗する）．"""
    spec = importlib.util.spec_from_file_location("b2c_verdict", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["b2c_verdict"] = module
    spec.loader.exec_module(module)
    return module


B2C = load_script()


@pytest.mark.parametrize(
    ("n_nonfinite", "n_r3", "n_v2", "d_r3", "d_v2", "expected"),
    [
        (0, 0, 0, 0, 0, "agree"),
        (0, 2, 2, -1, -1, "agree"),
        (0, 3, 0, 0, 0, "undetermined"),
        (0, 0, 5, 0, 0, "undetermined"),
        (0, 0, 0, -2, 0, "undetermined"),
        (0, 6, 0, 0, 0, "disagree"),
        (0, 0, 6, 0, 0, "disagree"),
        (0, 0, 0, -3, 0, "disagree"),
        (0, 0, 0, 0, -3, "disagree"),
        (1, 0, 0, 0, 0, "disagree"),
    ],
)
def test_h_f1_boundaries(
    n_nonfinite: int, n_r3: int, n_v2: int, d_r3: int, d_v2: int, expected: str
) -> None:
    """一致する（2 問以下・純 −1 以上），一致しない（6 問以上・純 −3 以下・非有限）の境界．"""
    assert B2C.decide_agreement(n_nonfinite, n_r3, n_v2, d_r3, d_v2) == expected


@pytest.mark.parametrize(
    ("ratios", "expected"),
    [
        ([1.5, 1.8, 2.0], "faster"),
        ([1.5, 1.49], "undetermined"),
        ([1.14, 1.0, 1.149], "not_faster"),
        ([1.14, 1.15], "undetermined"),
        ([], "undetermined"),
    ],
)
def test_h_f2_boundaries(ratios: list[float], expected: str) -> None:
    """全てで S ≥ 1.5 なら速くなる，全てで S < 1.15 なら速くならない，それ以外は決まらない．"""
    assert B2C.decide_speed(ratios) == expected


def test_round_orders_are_a_seeded_permutation_of_both_dtypes() -> None:
    orders = B2C.round_orders()
    assert len(orders) == 3
    assert all(sorted(o) == ["float16", "float32"] for o in orders)
    assert orders == B2C.round_orders()


def test_selection_changes_separate_ties_and_count_correctness_change() -> None:
    """基準の上位 2 候補の差が tie_gap 未満の問の食い違いは同点として別に数え，正誤の純の変化は全ての変更を数える．"""
    result = B2C.selection_changes(
        ["a", "b", "c"],
        {"a": 0, "b": 0, "c": 1},
        {"a": 1, "b": 1, "c": 1},
        {"a": [0.9, 0.5], "b": [0.9000, 0.8999], "c": [0.1, 0.2]},
        2e-3,
        {"a": (True, False), "b": (False, True), "c": (True, True)},
    )
    assert result["n_mismatch"] == 1
    assert result["n_mismatch_tie"] == 1
    assert result["delta_correct"] == 0
    assert result["delta_correct_excluding_ties"] == -1
    assert result["tie_qids"] == ["b"]


def test_gate_at_requires_same_attention_and_tokenizer_length(tmp_path: Path) -> None:
    base = {"attn_implementation": "sdpa", "tokenizer_max_length": 8192}
    f32, f16, other = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "c.json"
    f32.write_text(json.dumps({"dtype": "float32", **base}), encoding="utf-8")
    f16.write_text(json.dumps({"dtype": "float16", **base}), encoding="utf-8")
    other.write_text(
        json.dumps({"dtype": "float16", **base, "attn_implementation": "eager"}), encoding="utf-8"
    )
    B2C.RESULT_DIR = tmp_path
    assert B2C.gate_at(f32, f16)["pass"] is True
    assert B2C.gate_at(f32, other)["pass"] is False


def test_describe_diffs_reports_nonfinite_separately() -> None:
    stats = B2C.describe_diffs([0.0, 1.0, float("nan")])
    assert stats["n"] == 3
    assert stats["n_nonfinite"] == 1
    assert stats["max"] == 1.0
