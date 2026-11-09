"""scripts/d0009/b2co_verdict.py（採点を生成と同じ GPU に載せる測定のゲートと H-C0・H-C1 の判定）の単体テスト．"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "d0009" / "b2co_verdict.py"


def load_script() -> ModuleType:
    """scripts/ はパッケージではないので，パスから読み込む（sys.modules に登録しないと dataclass の解決が失敗する）．"""
    spec = importlib.util.spec_from_file_location("b2co_verdict", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["b2co_verdict"] = module
    spec.loader.exec_module(module)
    return module


B2CO = load_script()


@pytest.mark.parametrize(
    ("e_values", "expected"),
    [
        ([0.90, 0.95, 1.2], "small_loss"),
        ([0.90, 0.8999], "undetermined"),
        ([0.7999, 0.5], "large_loss"),
        ([0.80, 0.7], "undetermined"),
        ([0.95, 0.7], "undetermined"),
        ([], "undetermined"),
    ],
)
def test_h_c1_boundaries(e_values: list[float], expected: str) -> None:
    """損が小さい（全ラウンドで 0.90 以上），損が大きい（全ラウンドで 0.80 未満）の境界．"""
    assert B2CO.decide_speed(e_values) == expected


@pytest.mark.parametrize(
    ("not_fit", "mem", "expected"),
    [
        ({"copass": [], "r1_b": []}, {"copass": 9000.0, "r1_b": 11776.0}, "fits"),
        ({"copass": [], "r1_b": []}, {"copass": 9000.0, "r1_b": 11777.0}, "undetermined"),
        (
            {"copass": [], "r1_b": ["oom_ollama=1"]},
            {"copass": 9000.0, "r1_b": 9000.0},
            "cannot_fit",
        ),
        (
            {"copass": ["ps_post not 100% GPU"], "r1_b": []},
            {"copass": 9000.0, "r1_b": 9000.0},
            "cannot_fit",
        ),
        ({"copass": ["gen_request_errors=1"]}, {"copass": 12000.0}, "cannot_fit"),
        ({"copass": []}, {"copass": None}, "undetermined"),
        ({}, {}, "undetermined"),
    ],
)
def test_h_c0_cannot_fit_beats_memory_margin(
    not_fit: dict[str, list[str]], mem: dict[str, float | None], expected: str
) -> None:
    """OOM・溢れ・失敗が 1 つでもあれば載せられない．全て無くてもメモリの最大が 11,776 MiB を超えれば決まらない．"""
    assert B2CO.decide_feasibility(not_fit, mem) == expected


def test_generation_ratio_uses_only_candidates_finished_in_both_runs() -> None:
    """r_g は両方に終わった候補の時間の和の比（B にしか無い・G にしか無い候補は入れない）．"""
    t_g = {0: 1.0, 1: 2.0, 2: 3.0}
    t_b = {1: 4.0, 2: 6.0, 9: 100.0}
    assert B2CO.generation_ratio(t_g, t_b) == pytest.approx(5.0 / 10.0)
    assert B2CO.generation_ratio({0: 1.0}, {1: 1.0}) is None


def test_round_orders_are_a_fixed_shuffle_of_g_s_b() -> None:
    """種 40 の順は毎回同じで，各ラウンドが G・S・B を 1 回ずつ含む．"""
    orders = B2CO.round_orders()
    assert orders == B2CO.round_orders()
    assert len(orders) == 3
    assert all(sorted(o) == ["b", "g", "s"] for o in orders)


def test_run_not_fit_reports_each_pre_registered_cause() -> None:
    """OOM・失敗の要求・Ollama の溢れのどれも理由に挙がり，材料の欠けも理由になる．"""
    ok = {"all_on_gpu": True}
    facts = {
        "oom_scorer": 0,
        "oom_ollama": 0,
        "score_request_errors": 0,
        "gen_request_errors": 0,
        "ps_pre": ok,
        "ps_post": ok,
    }
    assert B2CO.run_not_fit(facts) == []
    bad = {**facts, "oom_scorer": 2, "gen_request_errors": 1, "ps_post": {"all_on_gpu": False}}
    assert B2CO.run_not_fit(bad) == ["oom_scorer=2", "gen_request_errors=1", "ps_post not 100% GPU"]
    assert B2CO.run_not_fit({**facts, "oom_ollama": None}) == ["oom_ollama missing"]


def test_ps_status_detects_cpu_spill(tmp_path: Path) -> None:
    """size_vram < size は溢れ，size_vram == size は 100% GPU．モデルが無ければ 100% GPU とは言わない．"""
    path = tmp_path / "ps.json"
    path.write_text(
        json.dumps(
            {"models": [{"name": "m", "size": 10, "size_vram": 10, "context_length": 16384}]}
        )
    )
    assert B2CO.ps_status(path)["all_on_gpu"] is True
    path.write_text(
        json.dumps({"models": [{"name": "m", "size": 10, "size_vram": 9, "context_length": 16384}]})
    )
    status = B2CO.ps_status(path)
    assert status["all_on_gpu"] is False and status["spilled"] is True
    path.write_text(json.dumps({"models": []}))
    assert B2CO.ps_status(path)["all_on_gpu"] is False


def test_candidate_times_keeps_only_successful_requests_in_the_window(tmp_path: Path) -> None:
    """区間に終わった成功の要求だけが t = prefill_s + decode_s で数えられる．"""
    rows = [
        {"index": 0, "done_at": 10.0, "error": None, "prefill_s": 1.0, "decode_s": 0.5},
        {"index": 1, "done_at": 10.0, "error": "x", "prefill_s": None, "decode_s": None},
        {"index": 2, "done_at": 20.0, "error": None, "prefill_s": 1.0, "decode_s": 0.5},
        {"index": 3, "done_at": 9.9, "error": None, "prefill_s": 1.0, "decode_s": 0.5},
    ]
    path = tmp_path / "gen_requests.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    assert B2CO.candidate_times(path, (10.0, 20.0)) == {0: 1.5}


@pytest.mark.parametrize(
    ("gates", "h_c0", "h_c1", "rounds", "expected"),
    [
        ({"G_CO_feasible": True, "all_passed": True}, "fits", "small_loss", 3, "adopt"),
        ({"G_CO_feasible": True, "all_passed": True}, "fits", "large_loss", 3, "reject"),
        ({"G_CO_feasible": True, "all_passed": True}, "cannot_fit", "small_loss", 3, "reject"),
        (
            {"G_CO_feasible": True, "all_passed": True},
            "fits",
            "undetermined",
            3,
            "undetermined_closed_as_description",
        ),
        (
            {"G_CO_feasible": True, "all_passed": True},
            "fits",
            "small_loss",
            1,
            "undetermined_not_closed",
        ),
        (
            {"G_CO_feasible": True, "all_passed": False},
            "fits",
            "small_loss",
            3,
            "undetermined_not_closed",
        ),
        (
            {"G_CO_feasible": False, "all_passed": False},
            "undetermined",
            "undetermined",
            0,
            "reject_not_fit_in_g_co",
        ),
    ],
)
def test_mechanical_outcome_follows_the_success_condition(
    gates: dict[str, object], h_c0: str, h_c1: str, rounds: int, expected: str
) -> None:
    """採用は「載せられる」かつ「損が小さい」，棄却は「載せられない」か「損が大きい」，G-CO で載らなければ本番なしで棄却．"""
    assert B2CO.mechanical_outcome(gates, h_c0, h_c1, rounds) == expected


def test_balance_estimates_dedicated_and_colocated() -> None:
    """専用の 1 台は min(9 X_gen / 3, X_req)．同居の 1 台は E_co が大きいほど（同じ X の下で）上限が下がらない．"""
    est = B2CO.balance_estimates(x_gen_cps=0.5, x_req_qps=2.6, e_co=1.0)
    assert est["dedicated_one_scoring_only"] == pytest.approx(min(9 * 0.5 / 3, 2.6))
    assert est["colocated_one"] > 0
    assert (
        B2CO.balance_estimates(0.5, 2.6, 0.5)["colocated_one"]
        <= B2CO.balance_estimates(0.5, 2.6, 1.0)["colocated_one"]
    )
