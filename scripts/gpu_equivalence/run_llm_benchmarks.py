"""全 10 台で Ollama による LLM 生成試験をラウンド制で実行する（制御点側で実行）．

計画書 §4 の 10 ラウンド構造を LLM 生成に適用する．各ラウンドでホスト順を seed とラウンド番号
から決定的にシャッフルし，10 台を並行して各ホストで 1 ラウンド（ウォームアップ＋短／長入力・
同時要求 1/4 の 4 条件）を測定する．中止したホストは以降のラウンドでもスキップする．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/run_llm_benchmarks.py \
        --run-id 20261111_gpu_equivalence --rounds 10 --out results/20261111_gpu_equivalence/llm
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
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
ROUND_TIMEOUT_S = 2400


def host_order(seed: int, round_no: int, hosts: list[str]) -> list[str]:
    """seed とラウンド番号から決定的にホスト順をシャッフルする．"""
    rng = random.Random(seed + round_no)
    order = list(hosts)
    rng.shuffle(order)
    return order


def run_host_round(host: str, run_id: str, round_no: int) -> dict[str, object]:
    """単一ホストで LLM 生成の 1 ラウンドを実行する（1 つの SSH セッション）．"""
    command = (
        f"cd {shlex.quote(REMOTE_BASE)} && "
        f"python3 run_llm_host.py --run-id {shlex.quote(run_id)} --round {round_no}"
    )
    try:
        result = subprocess.run([*SSH, host, command], capture_output=True, text=True, timeout=ROUND_TIMEOUT_S)
        statuses = [json.loads(line) for line in result.stdout.splitlines() if line.strip().startswith("{")]
        return {"host": host, "round": round_no, "returncode": result.returncode, "status": statuses[-1] if statuses else None}
    except subprocess.TimeoutExpired:
        return {"host": host, "round": round_no, "returncode": 124, "error": "round timeout", "status": None}


def main() -> None:
    """ラウンドごとにホストを並行して測定し，結果を保存する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20261111)
    parser.add_argument("--hosts", nargs="+", default=HOSTS)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    all_results: list[dict[str, object]] = []
    for round_no in range(1, args.rounds + 1):
        round_file = args.out / f"round_{round_no}.json"
        if round_file.exists():
            all_results.extend(json.loads(round_file.read_text()))
            print(f"round {round_no}: already done, skipped", flush=True)
            continue
        order = host_order(args.seed, round_no, args.hosts)
        print(f"round {round_no}: order={order}", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(args.hosts)) as pool:
            futures = {pool.submit(run_host_round, host, args.run_id, round_no): host for host in order}
            round_results = [future.result() for future in concurrent.futures.as_completed(futures)]
        round_results.sort(key=lambda r: HOSTS.index(r["host"]) if isinstance(r["host"], str) else len(HOSTS))
        all_results.extend(round_results)
        (args.out / f"round_{round_no}.json").write_text(json.dumps(round_results, indent=2, ensure_ascii=False) + "\n")
    summary = {
        "run_id": args.run_id,
        "finished_at": datetime.now(UTC).isoformat(),
        "rounds": args.rounds,
        "results": all_results,
    }
    (args.out / "llm_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.out / 'llm_summary.json'}")


if __name__ == "__main__":
    main()
