"""GPU 同等性調査の中止境界と減速検出を検証する．"""

import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).parents[1] / "scripts/gpu_equivalence/safety.py"
SPEC = importlib.util.spec_from_file_location("gpu_equivalence_safety", SOURCE)
assert SPEC is not None and SPEC.loader is not None
SAFETY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SAFETY)


@pytest.mark.parametrize(
    ("temperature", "software", "hardware", "expected"),
    [
        (79, "Not Active", "Not Active", None),
        (80, "Not Active", "Not Active", "temperature_at_or_above_80C"),
        (81, "Not Active", "Not Active", "temperature_at_or_above_80C"),
        (65, "Active", "Not Active", "thermal_slowdown_active"),
        (65, "Not Active", "Active", "thermal_slowdown_active"),
    ],
)
def test_stop_threshold_and_thermal_reasons(
    temperature: float, software: str, hardware: str, expected: str | None
) -> None:
    """80 °C と熱による減速の両方を確実に中止として検出する．"""
    assert SAFETY.stop_reason(temperature, software, hardware) == expected
