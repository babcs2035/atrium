"""全 10 台の現在の稼働状態を読み取り，占有可否の判断材料を保存する．

読み取り専用で，GPU の温度・使用率・VRAM・消費電力，常駐コンテナ，compute 用プロセス，
負荷開始直前の熱による減速の累計を収集する．冷却・電源・埃などの現物でしか分からない項目は
ここでは扱わない（未確認とする）．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/check_state.py \
        --out results/20261111_gpu_equivalence/state
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

HOSTS = [f"wafl50{i}" for i in range(10)]
SSH = [
    "ssh",
    "-F",
    "/home/ktakahashi/.ssh/config",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=3",
]
REMOTE_CMD = (
    "hostname; "
    "nvidia-smi --query-gpu=uuid,temperature.gpu,utilization.gpu,memory.used,power.draw,clocks_event_reasons.hw_thermal_slowdown --format=csv,noheader; "
    "echo '--- containers ---'; docker ps --format '{{.Names}} {{.Image}}'; "
    "echo '--- compute ---'; nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader; "
    "echo '--- load ---'; cat /proc/loadavg"
)


@dataclass
class HostState:
    """単一ホストの負荷開始前の状態．"""

    host: str
    returncode: int
    checked_at: str
    gpu: list[str] = field(default_factory=list)
    containers: list[str] = field(default_factory=list)
    compute_apps: list[str] = field(default_factory=list)
    loadavg: list[str] = field(default_factory=list)
    error: str | None = None


def _section(lines: list[str], name: str) -> list[str]:
    """--- name --- で囲まれた非空行を返す．"""
    out: list[str] = []
    found = False
    for line in lines:
        if line.strip() == f"--- {name} ---":
            found = True
            continue
        if found:
            if line.startswith("--- "):
                break
            if line.strip():
                out.append(line)
    return out


def check(host: str) -> HostState:
    """単一ホストの状態を収集する．"""
    now = datetime.now(UTC).isoformat()
    try:
        result = subprocess.run(
            [*SSH, host, REMOTE_CMD], capture_output=True, text=True, timeout=60
        )
        lines = result.stdout.splitlines()
        gpu = lines[1].split(", ") if len(lines) > 1 else []
        return HostState(
            host=host,
            returncode=result.returncode,
            checked_at=now,
            gpu=gpu,
            containers=_section(lines, "containers"),
            compute_apps=_section(lines, "compute"),
            loadavg=_section(lines, "load"),
        )
    except subprocess.TimeoutExpired:
        return HostState(host=host, returncode=124, checked_at=now, error="state check timeout")


def main() -> None:
    """全ホストの状態を並行して収集し JSON と markdown で保存する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        states = list(pool.map(check, HOSTS))
    (args.out / "state.json").write_text(
        json.dumps([asdict(s) for s in states], indent=2, ensure_ascii=False) + "\n"
    )

    lines = [
        "# 負荷開始前の稼働状態（wafl500〜509）",
        "",
        f"確認時刻: {states[0].checked_at}",
        "",
        "| host | 温度(°C) | 使用率 | VRAM 使用 | 消費電力 | 熱減速累計 | コンテナ | compute | loadavg | 占有可否 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in states:
        if s.error is not None:
            lines.append(f"| {s.host} | 取得失敗(rc={s.returncode}) | | | | | | | | 不可 |")
            continue
        temp = s.gpu[1] if len(s.gpu) > 1 else "—"
        util = s.gpu[2] if len(s.gpu) > 2 else "—"
        vram = s.gpu[3] if len(s.gpu) > 3 else "—"
        power = s.gpu[4] if len(s.gpu) > 4 else "—"
        thermal = s.gpu[5] if len(s.gpu) > 5 else "—"
        containers = " | ".join(s.containers) if s.containers else "none"
        compute = " | ".join(s.compute_apps) if s.compute_apps else "none"
        load = " ".join(s.loadavg) if s.loadavg else "—"
        occupiable = (
            "可"
            if (not s.containers and not s.compute_apps and util.strip() == "0 %")
            else "要確認"
        )
        lines.append(
            f"| {s.host} | {temp} | {util} | {vram} | {power} | {thermal} | {containers} | {compute} | {load} | {occupiable} |"
        )
    (args.out / "state.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out / 'state.md'}")


if __name__ == "__main__":
    main()
