"""固定 digest の測定イメージと測定用スクリプトを全台に配布する．"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shlex
import subprocess
import tarfile
from pathlib import Path

HOSTS = [f"wafl50{i}" for i in range(10)]
IMAGE = "localhost:5000/atrium-full@sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f"
REMOTE = "/home/denjo/workspace/ktakahashi/atrium/gpu-equivalence-20261111"
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


def prepare(host: str, out: Path, archive: Path) -> dict[str, str | int]:
    """既存のタグを上書きせず，固定 digest だけを取得する．"""
    command = f"mkdir -p {shlex.quote(REMOTE)} && tar -xf - -C {shlex.quote(REMOTE)}"
    with archive.open("rb") as stream:
        copy = subprocess.run([*SSH, host, command], stdin=stream, capture_output=True, timeout=120)
    if copy.returncode:
        return {"host": host, "returncode": copy.returncode, "stage": "copy"}
    command = (
        f"docker image inspect {shlex.quote(IMAGE)} >/dev/null 2>&1 || "
        f"docker pull {shlex.quote(IMAGE)}; "
        f"docker image inspect --format '{{{{.Id}}}} {{{{json .RepoDigests}}}}' {shlex.quote(IMAGE)}"
    )
    try:
        result = subprocess.run(
            [*SSH, "-n", host, command], capture_output=True, text=True, timeout=3600
        )
        (out / f"{host}.prepare.log").write_text(result.stdout + result.stderr)
        return {"host": host, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"host": host, "returncode": 124}


def main() -> None:
    """配布結果をホストごとに保存する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    archive = args.out / "measurement_sources.tar"
    with tarfile.open(archive, "w") as tar:
        for path in Path(__file__).parent.glob("*.py"):
            tar.add(path, arcname=path.name)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(prepare, host, args.out, archive) for host in HOSTS]
        states = []
        for future in concurrent.futures.as_completed(futures):
            states.append(future.result())
            print(json.dumps(states[-1]), flush=True)
            (args.out / "prepare_status.json").write_text(json.dumps(states, indent=2) + "\n")


if __name__ == "__main__":
    main()
