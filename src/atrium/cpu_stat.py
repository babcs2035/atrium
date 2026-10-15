"""デバイス全体の CPU 時間（/proc/stat）と cgroup の CPU 時間の読み方．質問者・負荷試験（atrium.requester_procs・atrium.node_bench）と専門家のノード（/healthz）が共有する．

コンテナの中でも /proc/stat はホスト全体の値を示すので，コンテナの外の docker・カーネルのネットワーク処理も含めた
デバイスの CPU 使用率を，区間の前後の 2 つの標本の差から求められる．
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROC_STAT = Path("/proc/stat")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")
CGROUP_ROOT = Path("/sys/fs/cgroup")
CGROUP_V2_PREFIX = "0::"
PROC_NET_SNMP = Path("/proc/net/snmp")
PROC_NET_NETSTAT = Path("/proc/net/netstat")
# 区間の前後の差を取る TCP の再送系の計数（/proc/net/snmp の Tcp: と /proc/net/netstat の TcpExt:）
TCP_COUNTERS: tuple[tuple[str, str], ...] = (
    ("Tcp", "RetransSegs"),
    ("TcpExt", "TCPSynRetrans"),
    ("TcpExt", "TCPTimeouts"),
)


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


def parse_cgroup_usage_s(text: str) -> float:
    """cgroup v2 の cpu.stat の usage_usec（cgroup の全プロセスの CPU 時間の累計）を秒で返す．"""
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            return int(value) / 1e6
    raise ValueError("cpu.stat has no usage_usec")


def read_cgroup_cpu_s() -> float | None:
    """このプロセスが属する cgroup（v2）の全プロセスの CPU 時間の累計（秒）．cgroup v2 でなければ None．

    専門家のノードを複数のプロセスで動かすとき（Iteration 10），/healthz に答えたプロセスの CPU 時間だけでは
    ノードの全体を測れないので，コンテナの cgroup の合計を使う．/proc/self/cgroup の "0::<path>" が v2 の所属で，
    コンテナの中（cgroup の名前空間が分かれている）では path が "/" になり，/sys/fs/cgroup/cpu.stat がコンテナの値になる．
    """
    if not PROC_SELF_CGROUP.exists():
        return None
    path = next(
        (
            line[len(CGROUP_V2_PREFIX) :]
            for line in PROC_SELF_CGROUP.read_text(encoding="utf-8").splitlines()
            if line.startswith(CGROUP_V2_PREFIX)
        ),
        None,
    )
    if path is None:
        return None
    stat = CGROUP_ROOT / path.lstrip("/") / "cpu.stat"
    if not stat.exists():
        return None
    return parse_cgroup_usage_s(stat.read_text(encoding="utf-8"))


def busy_fractions(before: CpuSample | None, after: CpuSample | None) -> tuple[float, float] | None:
    """2 つの標本の間の，全 CPU に対する忙しい時間と softirq の時間の割合（0〜1）．求められなければ None．"""
    if before is None or after is None or after.total <= before.total:
        return None
    total = after.total - before.total
    return (after.busy - before.busy) / total, (after.softirq - before.softirq) / total


def parse_proc_net_counters(text: str) -> dict[str, dict[str, int]]:
    """/proc/net/snmp・/proc/net/netstat の書式（同じ接頭辞の「名前の行」と「値の行」の対）を，接頭辞 → 名前 → 値に読む．"""
    lines = text.splitlines()
    parsed: dict[str, dict[str, int]] = {}
    for names_line, values_line in zip(lines[0::2], lines[1::2], strict=False):
        prefix, _, names = names_line.partition(":")
        values = values_line.partition(":")[2].split()
        parsed[prefix] = {name: int(v) for name, v in zip(names.split(), values, strict=False)}
    return parsed


def read_tcp_counters() -> dict[str, int] | None:
    """このプロセスのネットワークの名前空間の TCP の再送系の計数（TCP_COUNTERS）．読めなければ None．

    コンテナの中の /proc/net/* はそのコンテナの名前空間の値なので，host ネットワークのコンテナでは
    ホスト全体（他の通信を含む）の値になる．区間の前後の差を取って使う．
    """
    if not PROC_NET_SNMP.exists() or not PROC_NET_NETSTAT.exists():
        return None
    merged = parse_proc_net_counters(PROC_NET_SNMP.read_text(encoding="utf-8"))
    merged.update(parse_proc_net_counters(PROC_NET_NETSTAT.read_text(encoding="utf-8")))
    try:
        return {name: merged[prefix][name] for prefix, name in TCP_COUNTERS}
    except KeyError:
        return None


def diff_tcp_counters(
    before: dict[str, int] | None, after: dict[str, int] | None
) -> dict[str, int] | None:
    """2 つの標本の各計数の差．どちらかが読めていなければ None．"""
    if before is None or after is None:
        return None
    return {name: after[name] - before[name] for name in after}
