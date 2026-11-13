"""LLM 生成クライアントの分析関数（analyze_final/percentile/summarize）を検証する．

Ollama の応答から速度を計算する純粋関数なので，GPU・ネットワークなしで単体テストする．
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts/gpu_equivalence"


def _load(name: str, path: Path) -> types.ModuleType:
    """ファイルからモジュールを読み，sys.modules に登録する．"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LLM = _load("gpu_eq_llm", SCRIPTS / "llm_client.py")


def test_analyze_final_computes_speeds() -> None:
    """eval_count/eval_duration から生成速度を正しく計算する．"""
    final = {
        "eval_count": 64,
        "eval_duration": 2_000_000_000,  # 2 秒
        "prompt_eval_count": 128,
        "prompt_eval_duration": 400_000_000,  # 0.4 秒
    }
    result = LLM.analyze_final(final)
    assert result["eval_count"] == 64
    assert result["gen_tokens_per_s"] == pytest.approx(32.0)
    assert result["prompt_eval_count"] == 128
    assert result["prompt_tokens_per_s"] == pytest.approx(320.0)


def test_analyze_final_zero_duration() -> None:
    """duration が 0 の場合は速度を 0 で返し，ゼロ除算しない．"""
    result = LLM.analyze_final({"eval_count": 0, "eval_duration": 0, "prompt_eval_count": 0, "prompt_eval_duration": 0})
    assert result["gen_tokens_per_s"] == 0.0
    assert result["prompt_tokens_per_s"] == 0.0


def test_percentile_linear_interpolation() -> None:
    """パーセンタイルは線形補間する．"""
    values = [1.0, 2.0, 3.0, 4.0]
    assert LLM.percentile(values, 50) == pytest.approx(2.5)
    assert LLM.percentile(values, 0) == pytest.approx(1.0)
    assert LLM.percentile(values, 100) == pytest.approx(4.0)
    assert LLM.percentile([], 50) == 0.0


def test_summarize_aggregates_and_detects_output_divergence() -> None:
    """集約が速度・p50/p95・出力ハッシュの一致状況を正しく出す．"""
    results = [
        {"gen_tokens_per_s": 30.0, "prompt_tokens_per_s": 300.0, "total_time_s": 2.0, "ttft_s": 0.5, "output_sha256": "a", "eval_count": 64},
        {"gen_tokens_per_s": 32.0, "prompt_tokens_per_s": 300.0, "total_time_s": 2.2, "ttft_s": 0.5, "output_sha256": "a", "eval_count": 64},
    ]
    summary = LLM.summarize(results)
    assert summary["n_ok"] == 2
    assert summary["n_error"] == 0
    assert summary["gen_tokens_per_s_median"] == pytest.approx(31.0)
    assert summary["distinct_output_hashes"] == 1
    assert summary["dominant_output_sha256"] == "a"


def test_summarize_counts_errors() -> None:
    """エラー記録は n_error に集計され，速度の集計には含まれない．"""
    results = [
        {"gen_tokens_per_s": 30.0, "prompt_tokens_per_s": 300.0, "total_time_s": 2.0, "ttft_s": 0.5, "output_sha256": "a", "eval_count": 64},
        {"error": "no_done_chunk", "total_time_s": 5.0},
    ]
    summary = LLM.summarize(results)
    assert summary["n_ok"] == 1
    assert summary["n_error"] == 1
