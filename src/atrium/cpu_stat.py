"""デバイス全体の CPU 時間（/proc/stat）の読み方．質問者・負荷試験（atrium.requester_procs・atrium.node_bench）と専門家のノード（/healthz）が共有する．

コンテナの中でも /proc/stat はホスト全体の値を示すので，コンテナの外の docker・カーネルのネットワーク処理も含めた
デバイスの CPU 使用率を，区間の前後の 2 つの標本の差から求められる．
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROC_STAT = Path("/proc/stat")


@dataclass(frozen=True)
class CpuSample:
    """/proc/stat の先頭の行（全 CPU の合計．単位は jiffies）．"""

    busy: int
    total: int
    softirq: int


def parse_proc_stat(text: str) -> CpuSample:
    """/proc/stat の先頭の cpu 行から，忙しい時間・全体の時間・softirq の時間を読む．

    列は user nice system idle iowait irq softirq steal guest guest_nice．guest は user に含まれるので足さない．
    """
    first = text.splitlines()[0].split()
    if first[0] != "cpu":
        raise ValueError(f"unexpected first line of /proc/stat: {first[:2]}")
    fields = [int(x) for x in first[1:9]]
    idle = fields[3] + fields[4]
    total = sum(fields)
    return CpuSample(busy=total - idle, total=total, softirq=fields[6])


def read_device_cpu() -> CpuSample | None:
    """このデバイス全体の CPU 時間の累計．Linux 以外では None．"""
    if not PROC_STAT.exists():
        return None
    return parse_proc_stat(PROC_STAT.read_text(encoding="utf-8"))


def busy_fractions(before: CpuSample | None, after: CpuSample | None) -> tuple[float, float] | None:
    """2 つの標本の間の，全 CPU に対する忙しい時間と softirq の時間の割合（0〜1）．求められなければ None．"""
    if before is None or after is None or after.total <= before.total:
        return None
    total = after.total - before.total
    return (after.busy - before.busy) / total, (after.softirq - before.softirq) / total
