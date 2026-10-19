"""freshness-run・failure-run・join-run のコマンドの引数が，測定の条件（Params）へ渡ることの仕様（実機・DB なし）．"""

from __future__ import annotations

import math
from typing import Any

import pytest

from atrium.cli import build_parser
from atrium.config import AtriumConfig


def _run(
    monkeypatch: pytest.MonkeyPatch, cfg: AtriumConfig, module: str, command: str, argv: list[str]
) -> dict[str, Any]:
    """コマンドを流し，run_*_command へ渡された条件（最後の引数）を返す．実機へは接続しない．"""
    captured: dict[str, Any] = {}

    async def stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["params"] = args[-1]
        return {}

    monkeypatch.setattr(f"atrium.{module}.{command}", stub)
    parsed = build_parser().parse_args(argv)
    assert parsed.func(parsed, cfg) == 0
    return captured


COMMON = ["--data-dir", "/data", "--placement", "p.json", "--out-dir", "/out"]


def test_freshness_run_arguments_reach_the_params(
    monkeypatch: pytest.MonkeyPatch, cfg: AtriumConfig
) -> None:
    captured = _run(
        monkeypatch,
        cfg,
        "freshness_run",
        "run_freshness_command",
        ["freshness-run", *COMMON, "--intervals", "0,60,inf", "--n-arrivals", "20", "--no-routed"],
    )
    params = captured["params"]
    assert params.intervals == (0.0, 60.0, math.inf)
    assert params.n_arrivals == 20 and params.routed is False
    assert (params.fraction, params.seed) == (0.10, 12)


def test_failure_run_arguments_reach_the_params(
    monkeypatch: pytest.MonkeyPatch, cfg: AtriumConfig
) -> None:
    captured = _run(
        monkeypatch,
        cfg,
        "failure_run",
        "run_failure_command",
        [
            "failure-run",
            *COMMON,
            "--ks",
            "1,3",
            "--modes",
            "availability",
            "--down-s",
            "15",
            "--post-s",
            "10",
            "--stream-parallel",
            "2",
            "--answer-deadline-s",
            "3",
        ],
    )
    params = captured["params"]
    assert params.ks == (1, 3) and params.modes == ("availability",)
    assert (params.down_s, params.post_s) == (15.0, 10.0)
    assert (params.stream_parallel, params.answer_deadline_s) == (2, 3.0)


def test_join_run_arguments_reach_the_params(
    monkeypatch: pytest.MonkeyPatch, cfg: AtriumConfig
) -> None:
    captured = _run(
        monkeypatch,
        cfg,
        "join_run",
        "run_join_command",
        [
            "join-run",
            *COMMON,
            "--n-devices",
            "1",
            "--delays",
            "0,10",
            "--initial-devices",
            "3",
            "--summary-max-docs",
            "40",
        ],
    )
    params = captured["params"]
    assert params.n_devices == 1 and params.delays == (0, 10)
    assert (params.initial_devices, params.summary_max_docs) == (3, 40)
