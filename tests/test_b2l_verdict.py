"""scripts/d0009/b2l_verdict.py（採点の最大長 1,024 の測定のゲートと H-L1・H-L2 の判定）の単体テスト．"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "d0009" / "b2l_verdict.py"


def load_script() -> ModuleType:
    """scripts/ はパッケージではないので，パスから読み込む（sys.modules に登録しないと dataclass の解決が失敗する）．"""
    spec = importlib.util.spec_from_file_location("b2l_verdict", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["b2l_verdict"] = module
    spec.loader.exec_module(module)
    return module


B2L = load_script()


@pytest.mark.parametrize(
    ("ratios", "expected"),
    [
        ([1.08, 1.10, 1.2], "faster"),
        ([1.08, 1.0799], "undetermined"),
        ([1.0299, 1.02, 1.0], "not_faster"),
        ([1.03, 1.02], "undetermined"),
        ([1.02, 1.5], "undetermined"),
        ([], "undetermined"),
    ],
)
def test_h_l2_boundaries(ratios: list[float], expected: str) -> None:
    """速くなる（全ラウンドで 1.08 以上），速くならない（全ラウンドで 1.03 未満）の境界．"""
    assert B2L.decide_speed(ratios) == expected


def test_round_orders_are_a_fixed_shuffle_of_both_max_lengths() -> None:
    """種 39 の順は毎回同じで，各ラウンドが 8,192 と 1,024 を 1 回ずつ含む．"""
    orders = B2L.round_orders()
    assert orders == B2L.round_orders()
    assert len(orders) == 3
    assert all(sorted(o) == ["max1024", "max8192"] for o in orders)


@pytest.mark.parametrize(
    ("short", "long", "expected"),
    [
        ([1e-5, 2e-5, 1e-4], [0.0, 2e-3], True),
        ([1e-5, 2e-4, 3e-4], [0.0, 2e-3], False),
        ([1e-5, 2e-5], [0.0, 1e-3], False),
        ([], [2e-3], False),
    ],
)
def test_g_tr_needs_stable_short_pairs_and_moved_long_pairs(
    short: list[float], long: list[float], expected: bool
) -> None:
    """1,024 以下の組の中央値が 1e-4 以下で，超える組に 1e-3 を超えて動いた組が 1 つ以上あるときだけ通る．"""
    assert B2L.decide_truncation_effect(short, long) is expected


def test_split_diffs_by_length_uses_the_1024_boundary() -> None:
    """長さがちょうど 1,024 の組は「以下」の側に入る．"""
    lengths = {("q", 0, "a"): 1024, ("q", 0, "b"): 1025}
    new = {("q", 0, "a"): 0.5, ("q", 0, "b"): 0.5}
    base = {("q", 0, "a"): 0.4, ("q", 0, "b"): 0.2}
    short, long = B2L.split_diffs_by_length(new, base, lengths)
    assert short == pytest.approx([0.1])
    assert long == pytest.approx([0.3])


def test_gate_at_requires_the_effective_max_lengths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dtype・attention の実装が同じで，効いている最大長が 8,192 と 1,024 のときだけ通る．"""
    monkeypatch.setattr(B2L, "RESULT_DIR", tmp_path)
    good8 = tmp_path / "h8.json"
    good8.write_text('{"dtype":"float16","attn_implementation":"sdpa","max_length":8192}')
    good1 = tmp_path / "h1.json"
    good1.write_text('{"dtype":"float16","attn_implementation":"sdpa","max_length":1024}')
    assert B2L.gate_at(good8, good1)["pass"] is True
    bad1 = tmp_path / "b1.json"
    bad1.write_text('{"dtype":"float16","attn_implementation":"sdpa","max_length":8192}')
    assert B2L.gate_at(good8, bad1)["pass"] is False
    eager = tmp_path / "e1.json"
    eager.write_text('{"dtype":"float16","attn_implementation":"eager","max_length":1024}')
    assert B2L.gate_at(good8, eager)["pass"] is False
