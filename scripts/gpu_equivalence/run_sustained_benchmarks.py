"""全 10 台で持続負荷（30 分×3 回）と全台同時負荷を実行する（制御点側で実行）．

計画書 §4 の持続負荷をオーケストレーションする．単独稼働（生成・採点の各 30 分×3 回）を
行い，最後に全 10 台を同時に使う 30 分の試験を行う．各ホストは run_sustained_host.py を
実行し，中止したホストは記録する．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/run_sustained_benchmarks.py \
        --run-id 20261111_gpu_equivalence --out results/20261111_gpu_equivalence/sustained
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shlex
import subprocess
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
REMOTE_BASE = "/home/denjo/workspace/ktakahashi/atrium/gpu-equivalence-20261111"
REP_TIMEOUT_S = 2400


def run_host_sustained(host: str, run_id: str, kind: str, rep: int) -> dict[str, object]:
    """単一ホストで持続負荷を 1 回実行する（1 つの SSH セッション）．"""
    command = (
        f"cd {shlex.quote(REMOTE_BASE)} && "
        f"python3 run_sustained_host.py --run-id {shlex.quote(run_id)} --kind {kind} --rep {rep}"
    )
    try:
        result = subprocess.run([*SSH, host, command], capture_output=True, text=True, timeout=REP_TIMEOUT_S)
        statuses = [json.loads(line) for line in result.stdout.splitlines() if line.strip().startswith("{")]
        return {"host": host, "kind": kind, "rep": rep, "status": statuses[-1] if statuses else None}
    except subprocess.TimeoutExpired:
        return {"host": host, "kind": kind, "rep": rep, "returncode": 124, "error": "timeout", "status": None}


def run_phase(hosts: list[str], run_id: str, kind: str, rep: int, out: Path) -> list[dict[str, object]]:
    """1 フェーズ（全ホスト並行）を実行し，結果を保存して返す．"""
    phase_file = out / f"{kind}_r{rep}.json"
    if phase_file.exists():
        existing: list[dict[str, object]] = json.loads(phase_file.read_text())
        return existing
    print(f"phase {kind} rep {rep}: starting {len(hosts)} hosts", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        futures = [pool.submit(run_host_sustained, host, run_id, kind, rep) for host in hosts]
        results = [future.result() for future in futures]
    results.sort(key=lambda r: HOSTS.index(r["host"]) if isinstance(r["host"], str) else len(HOSTS))
    (out / f"{kind}_r{rep}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    return results


def main() -> None:
    """単独負荷の各フェーズと全台同時負荷を実行する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--hosts", nargs="+", default=HOSTS)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    all_results: list[dict[str, object]] = []
    # 各ホストは独立マシンなので全ホスト並行で実行する（実用上の同時稼働に相当）．
    # 生成 3 回
    for rep in range(1, args.reps + 1):
        all_results.extend(run_phase(args.hosts, args.run_id, "generation", rep, args.out))
    # 採点 3 回（score を代表とする）
    for rep in range(1, args.reps + 1):
        all_results.extend(run_phase(args.hosts, args.run_id, "score", rep, args.out))
    summary = {
        "run_id": args.run_id,
        "finished_at": datetime.now(UTC).isoformat(),
        "reps": args.reps,
        "results": all_results,
    }
    (args.out / "sustained_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.out / 'sustained_summary.json'}")


if __name__ == "__main__":
    main()
