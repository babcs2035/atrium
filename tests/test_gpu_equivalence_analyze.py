"""マイクロベンチマークの統計集計（analyze_benchmarks）の判定ロジックを検証する．

SSH と GPU を使わない pair_stats / simultaneous_ci / analyze_metric のみテストし，
計画書 §5 の「全組の比が 0.95〜1.05 に収まるか」の判定境界を確認する．
"""

import importlib.util
import math
import sys
import types
from pathlib import Path

import pytest
from scipy import stats

SCRIPTS = Path(__file__).parents[1] / "scripts/gpu_equivalence"


def _load(name: str, path: Path) -> types.ModuleType:
    """ファイルからモジュールを読み，sys.modules に登録する．"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ANALYZE = _load("gpu_eq_analyze", SCRIPTS / "analyze_benchmarks.py")


def test_simultaneous_ci_matches_bonferroni() -> None:
    """45 組の Bonferroni 補正で z≈3.26 の区間になることを確認する．"""
    lo, hi = ANALYZE.simultaneous_ci(0.0, 0.1, 45)
    z = stats.norm.ppf(1.0 - 0.05 / (2 * 45))
    assert (lo, hi) == pytest.approx((-z * 0.1, z * 0.1))
    assert z == pytest.approx(3.2608, abs=1e-3)


def test_pair_stats_identical_rates() -> None:
    """同じレートなら対数比の平均と標準誤差は 0 になる．"""
    rates = [100.0, 100.0, 100.0, 100.0, 100.0]
    assert ANALYZE.pair_stats(rates, list(rates)) == (0.0, 0.0, 5)


def test_pair_stats_constant_ratio() -> None:
    """一定の比率 1.1 なら平均対数比は log(1/1.1)，標準誤差は 0 になる．"""
    a = [100.0] * 5
    b = [110.0] * 5
    mean, se, n = ANALYZE.pair_stats(a, b)
    assert mean == pytest.approx(math.log(100.0 / 110.0))
    assert se == 0.0
    assert n == 5


def test_pair_stats_insufficient_rounds() -> None:
    """有効ラウンドが 3 未満なら判定不能（None）を返す．"""
    assert ANALYZE.pair_stats([1.0, None, None, None], [1.0, 1.0, None, None]) is None


def test_analyze_metric_equivalent() -> None:
    """全組が同じレートなら equivalent と判定される．"""
    per_metric = {
        "h1": [100.0] * 5,
        "h2": [100.0] * 5,
        "h3": [100.0] * 5,
    }
    result = ANALYZE.analyze_metric(["h1", "h2", "h3"], per_metric, 3)
    assert result["metric_verdict"] == "equivalent"
    assert len(result["pairs"]) == 3
    for pair in result["pairs"]:
        assert pair["mean_ratio"] == pytest.approx(1.0)
        assert pair["verdict"] == "equivalent"


def test_analyze_metric_performance_difference() -> None:
    """一定で 15% 遅いホストは performance_difference と判定される．"""
    per_metric = {
        "h1": [100.0] * 5,
        "h2": [115.0] * 5,
    }
    result = ANALYZE.analyze_metric(["h1", "h2"], per_metric, 1)
    assert result["metric_verdict"] == "performance_difference"
    pair = result["pairs"][0]
    assert pair["mean_ratio"] == pytest.approx(100.0 / 115.0)
    # 標準誤差 0 なので CI は点区間であり，許容幅 [0.95, 1.05] を完全に外れる
    assert pair["ci"][1] < 0.95


def test_analyze_metric_undetermined() -> None:
    """変動が大きいと CI が許容幅の境界をまたぎ undetermined になる．"""
    per_metric = {
        "h1": [100.0] * 5,
        "h2": [100.0, 106.0, 110.0, 104.0, 102.0],
    }
    result = ANALYZE.analyze_metric(["h1", "h2"], per_metric, 1)
    assert result["metric_verdict"] == "undetermined"
    pair = result["pairs"][0]
    assert pair["ci"][0] < 0.95 < pair["ci"][1]


def test_analyze_metric_no_data() -> None:
    """データが一切なければ no_data になる．"""
    per_metric = {
        "h1": [None] * 5,
        "h2": [None] * 5,
    }
    result = ANALYZE.analyze_metric(["h1", "h2"], per_metric, 1)
    assert result["metric_verdict"] == "no_data"
    assert result["pairs"] == []
