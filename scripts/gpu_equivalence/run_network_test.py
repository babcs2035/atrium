"""制御点から各ホストへの RTT と転送帯域を測る（制御点側で実行）．

計画書 §3 の「制御点から各ホストへの往復」を実装する．GPU を使わないため，GPU 負荷試験と
並行しても競合しないが，測定順の公平性のために別フェーズとして実行する．RTT は SSH の
往復時間，帯域は固定サイズファイルの scp 転送時間で測る．各ホストを複数回繰り返し中央値を
取る．P2P の全 45 組は，本試験で差が疑われた場合のみ別途実施する．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/run_network_test.py \
        --out results/20261111_gpu_equivalence/network
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

HOSTS = [f"wafl50{i}" for i in range(10)]
SSH = ["ssh", "-F", "/home/ktakahashi/.ssh/config", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
TRANSFER_BYTES = 100 * 1024 * 1024  # 100 MiB
RTT_REPEATS = 5
BANDWIDTH_REPEATS = 3


def measure_rtt(host: str) -> float:
    """SSH の往復時間（秒）を 1 回測る．"""
    start = time.monotonic()
    subprocess.run([*SSH, host, "true"], capture_output=True, timeout=30)
    return time.monotonic() - start


def measure_bandwidth(host: str, local_file: Path) -> float:
    """固定ファイルをホストへ scp 転送して帯域（MiB/s）を 1 回測る．"""
    remote = f"/tmp/nettest_{int(time.time())}.bin"
    start = time.monotonic()
    subprocess.run(
        ["scp", "-F", "/home/ktakahashi/.ssh/config", "-o", "BatchMode=yes", str(local_file), f"{host}:{remote}"],
        capture_output=True,
        timeout=300,
    )
    elapsed = time.monotonic() - start
    subprocess.run([*SSH, host, f"rm -f {remote}"], capture_output=True, timeout=30)
    return (TRANSFER_BYTES / (1024 * 1024)) / elapsed if elapsed > 0 else 0.0


def test_host(host: str, local_file: Path) -> dict[str, object]:
    """単一ホストの RTT と帯域を複数回測り，中央値を返す．"""
    rtt_samples = [measure_rtt(host) for _ in range(RTT_REPEATS)]
    bw_samples = [measure_bandwidth(host, local_file) for _ in range(BANDWIDTH_REPEATS)]
    return {
        "host": host,
        "rtt_ms_median": statistics.median(rtt_samples) * 1000,
        "rtt_ms_samples": [s * 1000 for s in rtt_samples],
        "bandwidth_mib_s_median": statistics.median(bw_samples),
        "bandwidth_mib_s_samples": bw_samples,
    }


def main() -> None:
    """全ホストのネットワークを測定し，結果を保存する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts", nargs="+", default=HOSTS)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
        tmp.write(b"\x00" * TRANSFER_BYTES)
        local_file = Path(tmp.name)
    try:
        results = [test_host(host, local_file) for host in args.hosts]
    finally:
        local_file.unlink(missing_ok=True)
    report = {
        "finished_at": datetime.now(UTC).isoformat(),
        "transfer_bytes": TRANSFER_BYTES,
        "hosts": results,
    }
    (args.out / "network.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    for row in results:
        print(
            f"{row['host']}: rtt={row['rtt_ms_median']:.1f}ms bw={row['bandwidth_mib_s_median']:.1f}MiB/s",
            flush=True,
        )
    print(f"wrote {args.out / 'network.json'}")


if __name__ == "__main__":
    main()
