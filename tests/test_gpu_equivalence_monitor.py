"""監視の中止判定（monitor.decide_abort）と負荷側の停止検出（workload.should_stop）を検証する．

両方ともファイルと時刻のみの純粋な判定なので，GPU なし・DB なしで単体テストする．
"""

import importlib.util
import sys
import time
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts/gpu_equivalence"


def _load(name: str, path: Path) -> types.ModuleType:
    """ファイルからモジュールを読み，sys.modules に登録する（相互 import を満たす）．"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# monitor.py は `from safety import stop_reason` するため，先に safety を "safety" として登録する
_load("safety", SCRIPTS / "safety.py")
MONITOR = _load("gpu_eq_monitor", SCRIPTS / "monitor.py")
WORKLOAD = _load("gpu_eq_workload", SCRIPTS / "workload.py")


def _sample(temp: float, sw: str, hw: str, xid: int) -> object:
    """判定に必要な項目だけ変えた Sample を作る．"""
    return MONITOR.Sample(
        ts=time.time(),
        temp_c=temp,
        util_pct=0,
        vram_mib=0,
        sm_clock=0,
        mem_clock=0,
        power_w=0.0,
        power_limit_w=0.0,
        fan_pct=0,
        sw_thermal=sw,
        hw_thermal=hw,
        hw_slowdown_us=0,
        xid_count=xid,
        cpu_usage_pct=0.0,
        ram_used_mib=0,
        other_procs=0,
    )


@pytest.mark.parametrize(
    ("temp", "sw", "hw", "xid", "prev_xid", "expected"),
    [
        (80, "Not Active", "Not Active", 0, 0, "temperature_at_or_above_80C"),
        (79, "Not Active", "Not Active", 0, 0, None),
        (65, "Active", "Not Active", 0, 0, "thermal_slowdown_active"),
        (65, "Not Active", "Active", 0, 0, "thermal_slowdown_active"),
        (40, "Not Active", "Not Active", 5, 3, "xid_error_detected"),
        (40, "Not Active", "Not Active", 3, 3, None),
        # Xid の読み取り不可（-1）は誤検知で中止しない
        (40, "Not Active", "Not Active", -1, -1, None),
    ],
)
def test_decide_abort(
    temp: float, sw: str, hw: str, xid: int, prev_xid: int, expected: str | None
) -> None:
    """温度・熱減速・Xid の増加分を正確に中止理由として返す．"""
    assert MONITOR.decide_abort(_sample(temp, sw, hw, xid), prev_xid) == expected


@pytest.mark.parametrize(
    ("stop_text", "heartbeat_age_s", "expected"),
    [
        (None, None, (False, None)),
        ("temperature_at_or_above_80C", None, (True, "temperature_at_or_above_80C")),
        (None, 0.0, (False, None)),
        (None, 10.0, (True, "monitoring_lost")),
        # stop ファイルが心拍より優先される
        ("xid_error_detected", 0.0, (True, "xid_error_detected")),
    ],
)
def test_should_stop(
    tmp_path: Path,
    stop_text: str | None,
    heartbeat_age_s: float | None,
    expected: tuple[bool, str | None],
) -> None:
    """stop ファイルと心拍の鮮度から停止を正しく判定する．"""
    run_dir = tmp_path
    if stop_text is not None:
        (run_dir / "stop").write_text(stop_text)
    if heartbeat_age_s is not None:
        (run_dir / "heartbeat").write_text(str(time.time() - heartbeat_age_s))
    assert WORKLOAD.should_stop(run_dir, 5.0) == expected
