"""全 10 台の構成情報を SSH で読み取り，ホスト別に保存する．"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

HOSTS = [f"wafl50{i}" for i in range(10)]
SSH_CONFIG = "/home/ktakahashi/.ssh/config"


def collect(host: str, out: Path, script: str) -> dict[str, str | int]:
    """単一ホストの出力と終了状態を保存する．"""
    command = [
        "ssh",
        "-F",
        SSH_CONFIG,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        host,
        "bash -s",
    ]
    try:
        result = subprocess.run(command, input=script, capture_output=True, text=True, timeout=180)
        (out / f"{host}.txt").write_text(result.stdout)
        (out / f"{host}.stderr.txt").write_text(result.stderr)
        return {
            "host": host,
            "returncode": result.returncode,
            "collected_at": datetime.now(UTC).isoformat(),
        }
    except subprocess.TimeoutExpired:
        return {"host": host, "returncode": 124, "error": "SSH inventory timeout"}


def main() -> None:
    """全ホストへ読み取り専用の収集を並行して実施する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    script = Path(__file__).with_name("inventory.sh").read_text()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(collect, host, args.out, script) for host in HOSTS]
        states = [future.result() for future in futures]
    (args.out / "status.json").write_text(json.dumps(states, indent=2) + "\n")
    print(json.dumps(states, indent=2))


if __name__ == "__main__":
    main()
