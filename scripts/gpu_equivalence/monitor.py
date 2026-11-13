"""負荷中の 1 秒間隔監視と中止判定を行う（ホスト側で実行）．

nvidia-smi と /proc から GPU・CPU・RAM・競合プロセスを 1 秒ごとに記録し，中止条件
（温度 80 °C 以上，熱による減速，Xid）が起きたら run-dir の stop ファイルを書く．
heartbeat ファイルで自分の生存を毎秒知らせ，負荷側（コンテナ内）が監視喪失を検出できるようにする．
自分のコンテナの PID 以外の GPU プロセスは競合として記録する（計画書 §3・§4）．

dmesg は権限がないため Xid は journalctl -k で NVRM: Xid に限定して読む（NIC の "XID" への偽陽性を避ける）．

使い方（ホスト上で，負荷コンテナと並行して実行）:
    python3 monitor.py --run-dir <dir> --container <name> --duration <s>
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from safety import stop_reason

SAMPLE_INTERVAL_S = 1.0
GPU_QUERY = (
    "nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,"
    "clocks.current.sm,clocks.current.memory,power.draw,power.limit,fan.speed,"
    "clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_slowdown "
    "--format=csv,noheader,nounits"
)
XID_PATTERN = "NVRM: Xid"


@dataclass
class Sample:
    """1 秒ごとの監視標本．"""

    ts: float
    temp_c: float
    util_pct: int
    vram_mib: int
    sm_clock: int
    mem_clock: int
    power_w: float
    power_limit_w: float
    fan_pct: int
    sw_thermal: str
    hw_thermal: str
    hw_slowdown_us: int
    xid_count: int
    cpu_usage_pct: float
    ram_used_mib: int
    other_procs: int


def _run(cmd: list[str], timeout: float) -> str:
    """コマンドを実行して標準出力を返す（失敗は空文字列）．"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


def sample_gpu() -> tuple[float, int, int, int, int, float, float, int, str, str] | None:
    """nvidia-smi から GPU の主要項目を 1 行で読む．"""
    out = _run(GPU_QUERY.split(), 5)
    if not out.strip():
        return None
    fields = [f.strip() for f in out.split(",")]
    if len(fields) < 10:
        return None
    try:
        return (
            float(fields[0]),
            int(fields[1]),
            int(fields[2]),
            int(fields[3]),
            int(fields[4]),
            float(fields[5]),
            float(fields[6]),
            int(float(fields[7])),
            fields[8],
            fields[9],
        )
    except ValueError:
        return None


def hw_slowdown_counter_us() -> int:
    """SW Thermal Slowdown の起動からの累計（マイクロ秒）を読む（パイプを使わず Python で解析）．"""
    out = _run(["nvidia-smi", "-q", "-d", "PERFORMANCE"], 5)
    for line in out.splitlines():
        if "SW Thermal Slowdown" in line and line.rstrip().endswith("us"):
            fields = line.split()
            try:
                return int(fields[-2])
            except (ValueError, IndexError):
                return -1
    return -1


def xid_count() -> int:
    """journalctl -k 中の NVRM: Xid 行の数を数える（実行不能な場合は -1）．"""
    out = _run(["journalctl", "-k", "--no-pager"], 10)
    if not out:
        return -1
    return sum(1 for line in out.splitlines() if "NVRM: Xid" in line)


def _cpu_usage_pct(prev_idle: int, prev_total: int) -> float:
    """/proc/stat の前後差から CPU 使用率（%）を計算する．"""
    line = Path("/proc/stat").read_text().splitlines()[0]
    fields = [int(x) for x in line.split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    total = sum(fields)
    d_idle, d_total = idle - prev_idle, total - prev_total
    if d_total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - d_idle / d_total)))


def _ram_used_mib() -> int:
    """/proc/meminfo から使用済み RAM（MiB）を返す．"""
    info: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        info[key.strip()] = int(parts[0]) if parts else 0
    return (info.get("MemTotal", 0) - info.get("MemAvailable", 0)) // (1024 * 1024)


def our_container_pids(container: str) -> set[int]:
    """自分のコンテナの PID 集合を返す（docker が使えない場合は空集合）．"""
    out = _run(["docker", "top", container, "-eo", "pid"], 5)
    pids: set[int] = set()
    for token in out.split():
        try:
            pids.add(int(token))
        except ValueError:
            continue
    return pids


def other_gpu_procs(container: str) -> int:
    """自分のコンテナ以外の GPU の compute プロセス数を返す（競合の検出）．"""
    mine = our_container_pids(container)
    out = _run(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"], 5)
    count = 0
    for token in out.split():
        try:
            pid = int(token)
        except ValueError:
            continue
        if pid not in mine:
            count += 1
    return count


def decide_abort(sample: Sample, prev_xid: int) -> str | None:
    """中止条件を判定する．safety.stop_reason に Xid の増加分を追加する．"""
    reason = stop_reason(sample.temp_c, sample.sw_thermal, sample.hw_thermal)
    if reason is None and sample.xid_count > prev_xid >= 0:
        reason = "xid_error_detected"
    return reason


def run(run_dir: Path, container: str, duration: float) -> str | None:
    """1 秒間隔で監視し，中止条件が起きたら stop ファイルを書いて理由を返す．"""
    run_dir.mkdir(parents=True, exist_ok=True)
    stop_file = run_dir / "stop"
    heartbeat = run_dir / "heartbeat"
    csv_path = run_dir / "monitor.csv"
    columns = [
        "ts",
        "temp_c",
        "util_pct",
        "vram_mib",
        "sm_clock",
        "mem_clock",
        "power_w",
        "power_limit_w",
        "fan_pct",
        "sw_thermal",
        "hw_thermal",
        "hw_slowdown_us",
        "xid_count",
        "cpu_usage_pct",
        "ram_used_mib",
        "other_procs",
    ]
    prev_xid = xid_count()
    stat_line = Path("/proc/stat").read_text().splitlines()[0]
    prev_fields = [int(x) for x in stat_line.split()[1:]]
    prev_idle = prev_fields[3] + (prev_fields[4] if len(prev_fields) > 4 else 0)
    prev_total = sum(prev_fields)
    start = time.monotonic()
    reason: str | None = None
    with csv_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        while time.monotonic() - start < duration:
            iter_start = time.monotonic()
            gpu = sample_gpu()
            now = time.time()
            if gpu is None:
                # GPU の読み取り失敗は温度を検証できないため，そのまま中止とする
                reason = "gpu_read_failed"
                stop_file.write_text(reason)
                break
            (
                temp_c,
                util_pct,
                vram_mib,
                sm_clock,
                mem_clock,
                power_w,
                power_limit_w,
                fan_pct,
                sw_thermal,
                hw_thermal,
            ) = gpu
            sample = Sample(
                ts=now,
                temp_c=temp_c,
                util_pct=util_pct,
                vram_mib=vram_mib,
                sm_clock=sm_clock,
                mem_clock=mem_clock,
                power_w=power_w,
                power_limit_w=power_limit_w,
                fan_pct=fan_pct,
                sw_thermal=sw_thermal,
                hw_thermal=hw_thermal,
                hw_slowdown_us=hw_slowdown_counter_us(),
                xid_count=prev_xid,
                cpu_usage_pct=_cpu_usage_pct(prev_idle, prev_total),
                ram_used_mib=_ram_used_mib(),
                other_procs=other_gpu_procs(container),
            )
            reason = decide_abort(sample, prev_xid)
            writer.writerow(sample.__dict__)
            stream.flush()
            # ハートビートはアトミックに書く（一時ファイル＋rename）．
            # write_text は truncate→write なので，読み手が書き込み中に読むと空ファイルになり
            # workload 側が heartbeat_unreadable で誤中止する競合を避ける．
            tmp_heartbeat = run_dir / "heartbeat.tmp"
            tmp_heartbeat.write_text(str(now))
            tmp_heartbeat.replace(heartbeat)
            if reason is not None:
                stop_file.write_text(reason)
                break
            prev_xid = xid_count()
            stat_line = Path("/proc/stat").read_text().splitlines()[0]
            fields = [int(x) for x in stat_line.split()[1:]]
            prev_idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            prev_total = sum(fields)
            time.sleep(max(0.0, SAMPLE_INTERVAL_S - (time.monotonic() - iter_start)))
    return reason


def main() -> None:
    """引数から run-dir・コンテナ名・監視時間を読み，監視を実行する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--duration", required=True, type=float)
    args = parser.parse_args()
    reason = run(args.run_dir, args.container, args.duration)
    print(f"monitor finished, reason={reason}", flush=True)


if __name__ == "__main__":
    main()
