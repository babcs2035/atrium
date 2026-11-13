"""持続負荷中の monitor.csv から温度・ファン・クロック・電力のハードウェア特性を集約する．

計画書 §5 の「温度・クロック・速度の時系列」に加え，負荷中のファン応答・電力・熱減速を
ホストごとに特徴量（初期値・ピーク・定常値・上昇幅）として集約する．monitor.csv は 1 秒間隔で
数千行あるため，ホスト側で特徴量を計算し小さな JSON を返す（大きな CSV を転送しない）．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/analyze_hw_characterization.py \
        --run-id 20261113_hw_characterization --metric fp32 \
        --out results/20261113_hw_characterization
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import TypedDict

from analyze_benchmarks import HOSTS, REMOTE_BASE, SSH

# 初期値は先頭 60 標本，定常値は末尾 300 標本（5 分）で平均する
INITIAL_SAMPLES = 60
STEADY_SAMPLES = 300


class HwFeatures(TypedDict):
    """単一ホストのハードウェア特性特徴量．"""

    host: str
    n_samples: int
    duration_s: float
    temp_initial_c: float
    temp_peak_c: float
    temp_steady_c: float
    temp_rise_c: float
    fan_initial_pct: float
    fan_peak_pct: float
    fan_steady_pct: float
    sm_clock_initial: float
    sm_clock_min: float
    sm_clock_steady: float
    mem_clock_max: float
    power_initial_w: float
    power_peak_w: float
    power_steady_w: float
    util_avg_pct: float
    sw_thermal: bool
    hw_thermal: bool
    xid_count_max: int
    aborted: bool


COLLECT_SNIPPET = """
import json, csv
host = {host!r}
run_id = {run_id!r}
metric = {metric!r}
initial_n = {initial_n}
steady_n = {steady_n}
base = "{base}/runs/{{run_id}}".format(run_id=run_id)
p = f"{{base}}/round_1/{{host}}/{{metric}}/monitor.csv"
try:
    rows = list(csv.DictReader(open(p)))
except FileNotFoundError:
    rows = []
def avg(vals):
    vals = [float(v) for v in vals if v not in (None, "")]
    return sum(vals) / len(vals) if vals else None
def mx(vals):
    vals = [float(v) for v in vals if v not in (None, "")]
    return max(vals) if vals else None
def mn(vals):
    vals = [float(v) for v in vals if v not in (None, "")]
    return min(vals) if vals else None
out = {{"host": host, "n_samples": len(rows), "aborted": False}}
if rows:
    ts = [float(r["ts"]) for r in rows if r.get("ts")]
    out["duration_s"] = (max(ts) - min(ts)) if len(ts) >= 2 else 0.0
    temp = [r["temp_c"] for r in rows]
    fan = [r["fan_pct"] for r in rows]
    sm = [r["sm_clock"] for r in rows]
    mem = [r["mem_clock"] for r in rows]
    pw = [r["power_w"] for r in rows]
    util = [r["util_pct"] for r in rows]
    out["temp_initial_c"] = avg(temp[:initial_n])
    out["temp_peak_c"] = mx(temp)
    out["temp_steady_c"] = avg(temp[-steady_n:])
    out["temp_rise_c"] = (out["temp_peak_c"] or 0) - (out["temp_initial_c"] or 0)
    out["fan_initial_pct"] = avg(fan[:initial_n])
    out["fan_peak_pct"] = mx(fan)
    out["fan_steady_pct"] = avg(fan[-steady_n:])
    out["sm_clock_initial"] = avg(sm[:initial_n])
    out["sm_clock_min"] = mn(sm)
    out["sm_clock_steady"] = avg(sm[-steady_n:])
    out["mem_clock_max"] = mx(mem)
    out["power_initial_w"] = avg(pw[:initial_n])
    out["power_peak_w"] = mx(pw)
    out["power_steady_w"] = avg(pw[-steady_n:])
    out["util_avg_pct"] = avg(util)
    out["sw_thermal"] = any(r.get("sw_thermal", "").strip().lower() == "active" for r in rows)
    out["hw_thermal"] = any(r.get("hw_thermal", "").strip().lower() == "active" for r in rows)
    xids = [int(r["xid_count"]) for r in rows if r.get("xid_count", "").lstrip("-").isdigit()]
    out["xid_count_max"] = max(xids) if xids else 0
try:
    out["aborted"] = bool(json.loads(open(f"{{base}}/round_1/{{host}}/{{metric}}/status.json").read()).get("aborted"))
except FileNotFoundError:
    pass
print(json.dumps(out))
"""


def _as_float(value: object, default: float = 0.0) -> float:
    """object を float に安全に変換する（None・非数値は default）．"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _as_int(value: object, default: int = 0) -> int:
    """object を int に安全に変換する（None・非数値は default）．"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _to_features(rec: dict[str, object], host: str) -> HwFeatures:
    """集約レコード（dict）を HwFeatures に変換する．"""
    return {
        "host": str(rec.get("host", host)),
        "n_samples": _as_int(rec.get("n_samples")),
        "duration_s": _as_float(rec.get("duration_s")),
        "temp_initial_c": _as_float(rec.get("temp_initial_c")),
        "temp_peak_c": _as_float(rec.get("temp_peak_c")),
        "temp_steady_c": _as_float(rec.get("temp_steady_c")),
        "temp_rise_c": _as_float(rec.get("temp_rise_c")),
        "fan_initial_pct": _as_float(rec.get("fan_initial_pct")),
        "fan_peak_pct": _as_float(rec.get("fan_peak_pct")),
        "fan_steady_pct": _as_float(rec.get("fan_steady_pct")),
        "sm_clock_initial": _as_float(rec.get("sm_clock_initial")),
        "sm_clock_min": _as_float(rec.get("sm_clock_min")),
        "sm_clock_steady": _as_float(rec.get("sm_clock_steady")),
        "mem_clock_max": _as_float(rec.get("mem_clock_max")),
        "power_initial_w": _as_float(rec.get("power_initial_w")),
        "power_peak_w": _as_float(rec.get("power_peak_w")),
        "power_steady_w": _as_float(rec.get("power_steady_w")),
        "util_avg_pct": _as_float(rec.get("util_avg_pct")),
        "sw_thermal": bool(rec.get("sw_thermal", False)),
        "hw_thermal": bool(rec.get("hw_thermal", False)),
        "xid_count_max": _as_int(rec.get("xid_count_max")),
        "aborted": bool(rec.get("aborted", False)),
    }


def collect_host(run_id: str, metric: str, host: str) -> HwFeatures | None:
    """単一ホストの monitor.csv からハードウェア特性特徴量を集める．"""
    snippet = COLLECT_SNIPPET.format(
        host=host,
        run_id=run_id,
        metric=metric,
        initial_n=INITIAL_SAMPLES,
        steady_n=STEADY_SAMPLES,
        base=REMOTE_BASE,
    )
    command = f"cd {shlex.quote(REMOTE_BASE)} && python3 -c {shlex.quote(snippet)}"
    try:
        result = subprocess.run([*SSH, host, command], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    for line in result.stdout.splitlines():
        if line.strip().startswith("{"):
            rec = json.loads(line)
            if rec.get("host") == host:
                return _to_features(rec, host)
    return None


def _fmt(value: float | None, digits: int = 1) -> str:
    """数値を整形し，欠測は n/a とする．"""
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "n/a"


def main() -> None:
    """全ホストのハードウェア特性を集約し，JSON と markdown の報告を作る．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--metric", default="fp32")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    print("collecting hardware features...", flush=True)
    features: list[HwFeatures] = []
    for host in HOSTS:
        rec = collect_host(args.run_id, args.metric, host)
        if rec is not None:
            features.append(rec)
    features.sort(key=lambda f: f["host"])
    (args.out / "hw_features.json").write_text(json.dumps(features, indent=2, ensure_ascii=False) + "\n")

    lines = [
        "# ハードウェア特性（持続負荷中）",
        "",
        f"負荷: {args.metric}（warmup + 45 分），監視: monitor.py（1 秒間隔）",
        f"初期値 = 先頭 {INITIAL_SAMPLES} 標本（1 分）平均，定常値 = 末尾 {STEADY_SAMPLES} 標本（5 分）平均",
        "",
        "## 温度",
        "",
        "| host | 初期 (°C) | ピーク (°C) | 定常 (°C) | 上昇幅 (°C) | 中止 |",
        "|---|---|---|---|---|---|",
    ]
    for f in features:
        lines.append(
            f"| {f['host']} | {_fmt(f.get('temp_initial_c'))} | {_fmt(f.get('temp_peak_c'))} | "
            f"{_fmt(f.get('temp_steady_c'))} | {_fmt(f.get('temp_rise_c'))} | "
            f"{'yes' if f.get('aborted') else 'no'} |"
        )
    lines += [
        "",
        "## ファン",
        "",
        "| host | 初期 (%) | ピーク (%) | 定常 (%) |",
        "|---|---|---|---|",
    ]
    for f in features:
        lines.append(
            f"| {f['host']} | {_fmt(f.get('fan_initial_pct'), 0)} | {_fmt(f.get('fan_peak_pct'), 0)} | "
            f"{_fmt(f.get('fan_steady_pct'), 0)} |"
        )
    lines += [
        "",
        "## SM クロック・電力",
        "",
        "| host | SM 初期 (MHz) | SM 最小 (MHz) | SM 定常 (MHz) | 電力初期 (W) | 電力ピーク (W) | 電力定常 (W) | 利用率平均 (%) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in features:
        lines.append(
            f"| {f['host']} | {_fmt(f.get('sm_clock_initial'), 0)} | {_fmt(f.get('sm_clock_min'), 0)} | "
            f"{_fmt(f.get('sm_clock_steady'), 0)} | {_fmt(f.get('power_initial_w'))} | "
            f"{_fmt(f.get('power_peak_w'))} | {_fmt(f.get('power_steady_w'))} | {_fmt(f.get('util_avg_pct'), 0)} |"
        )
    lines += [
        "",
        "## 熱イベント",
        "",
        "| host | SW 熱減速 | HW 減速 | Xid 最大 | 観測時間 (分) |",
        "|---|---|---|---|---|",
    ]
    for f in features:
        duration_min = (f.get("duration_s") or 0) / 60
        lines.append(
            f"| {f['host']} | {'yes' if f.get('sw_thermal') else 'no'} | "
            f"{'yes' if f.get('hw_thermal') else 'no'} | {f.get('xid_count_max', 0)} | "
            f"{duration_min:.1f} |"
        )
    lines.append("")
    (args.out / "hw_characterization.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out / 'hw_characterization.md'}")


if __name__ == "__main__":
    main()
