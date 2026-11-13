"""ホスト側スクリプトを全台へ再配布し，manifest の source hash と照合する．

制御点で動くスクリプト（collect/prepare/analyze/check_state/make_manifest）はノードに置かず，
ノードで実行する 4 ファイル（workload/safety/monitor/run_host）だけを対象にする．
既存の結果ログは上書きせず，配布記録は新しいファイルに保存する．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/distribute.py \
        --manifest results/20261111_gpu_equivalence/manifest_20261111_gpu_equivalence.json \
        --out results/20261111_gpu_equivalence
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shlex
import subprocess
import tarfile
from datetime import UTC, datetime
from pathlib import Path

HOSTS = [f"wafl50{i}" for i in range(10)]
REMOTE_BASE = "/home/denjo/workspace/ktakahashi/atrium/gpu-equivalence-20261111"
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
HOST_SCRIPTS = [
    "workload.py",
    "safety.py",
    "monitor.py",
    "run_host.py",
    "llm_client.py",
    "run_llm_host.py",
    "run_sustained_host.py",
]


def _sha256(path: Path) -> str:
    """ファイルの sha256 を返す．"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribute(host: str, archive: Path, expected: dict[str, str]) -> dict[str, object]:
    """単一ホストへ配布し，hash を照合する．"""
    command = f"mkdir -p {shlex.quote(REMOTE_BASE)} && tar -xf - -C {shlex.quote(REMOTE_BASE)}"
    with archive.open("rb") as stream:
        copy = subprocess.run([*SSH, host, command], stdin=stream, capture_output=True, timeout=120)
    if copy.returncode:
        return {"host": host, "status": "copy_failed", "returncode": copy.returncode}
    # 配布後の各ファイルの hash を確認する
    command = " && ".join(
        f"sha256sum {shlex.quote(REMOTE_BASE + '/' + name)}" for name in HOST_SCRIPTS
    )
    result = subprocess.run([*SSH, "-n", host, command], capture_output=True, text=True, timeout=60)
    hashes: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            hashes[parts[1].rsplit("/", 1)[-1]] = parts[0]
    mismatches = [name for name in HOST_SCRIPTS if hashes.get(name) != expected.get(name)]
    return {
        "host": host,
        "status": "ok" if not mismatches else "hash_mismatch",
        "mismatches": mismatches,
        "hashes": hashes,
    }


def main() -> None:
    """tar を作り全台へ配布し，配布記録を保存する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    expected = manifest["source_hashes"]
    scripts_dir = Path(__file__).parent
    # 配布前のローカル hash が manifest と一致することを確認する
    local = {name: _sha256(scripts_dir / name) for name in HOST_SCRIPTS}
    if local != {k: v for k, v in expected.items() if k in local}:
        raise SystemExit(f"local scripts do not match manifest: {local} vs {expected}")
    archive = args.out / "host_scripts.tar"
    with tarfile.open(archive, "w") as tar:
        for name in HOST_SCRIPTS:
            tar.add(scripts_dir / name, arcname=name)
    args.out.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(distribute, host, archive, expected) for host in HOSTS]
        states = [future.result() for future in futures]
    record = {
        "distributed_at": datetime.now(UTC).isoformat(),
        "image": manifest["image"],
        "expected_hashes": expected,
        "hosts": states,
        "all_ok": all(s["status"] == "ok" for s in states),
    }
    path = args.out / "distribute_status.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {"all_ok": record["all_ok"], "statuses": {s["host"]: s["status"] for s in states}},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
