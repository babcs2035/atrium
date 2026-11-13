"""測定監視で共有する中止条件を純粋関数として定義する．"""

from __future__ import annotations


def stop_reason(temperature: float, thermal: str, hardware_thermal: str) -> str | None:
    """温度または熱による減速が中止条件を満たすか判定する．"""
    if temperature >= 80:
        return "temperature_at_or_above_80C"
    if thermal.strip().lower() == "active" or hardware_thermal.strip().lower() == "active":
        return "thermal_slowdown_active"
    return None
