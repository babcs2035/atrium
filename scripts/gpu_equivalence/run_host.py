"""単一ホストで 1 指標の測定を監視付きで実行する（ホスト側で実行）．

固定 digest のイメージで負荷コンテナを起動し，host 側で monitor.py を並行して走らせる．
負荷の終了または中止（stop ファイル・コンテナの異常終了）で監視を止め，自分のコンテナだけ
を停止する．中止はホスト単位のマーカーとして残し，原因が解消するまで同じ負荷を繰り返さない．
既存の研究コンテナや他人のプロセスには触れない．

使い方（ホスト上で，最新スクリプトが配置された base ディレクトリに対して）:
    python3 run_host.py --metric fp32 --run-id <id> --warmup 60 --duration 20
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

BASE = Path("/home/denjo/workspace/ktakahashi/atrium/gpu-equivalence-20261111")
IMAGE = "localhost:5000/atrium-full@sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f"
MONITOR_BUFFER_S = 120.0
PY = sys.executable or "python3"
# embed/score はコンテナ内の固定パスからモデルを読むため，ホストのモデルディレクトリをマウントする
EMBED_MOUNT = ("/home/denjo/workspace/ktakahashi/atrium/hf-cache/hub", "/embed")
SCORE_MOUNT = ("/home/denjo/.cache/huggingface/hub", "/score")


def _docker(container: str, *args: str) -> int:
    """自分のコンテナ名に対してだけ docker を実行し終了コードを返す．"""
    result = subprocess.run(
        ["docker", *args, container], capture_output=True, text=True, timeout=120
    )
    return result.returncode


def run_metric(
    host: str, metric: str, run_id: str, round_no: int, warmup: int, duration: int, scripts: Path
) -> dict[str, object]:
    """監視付きで 1 指標を測定し，結果と中止状態を返す．"""
    run_dir = BASE / "runs" / run_id / f"round_{round_no}" / host / metric
    abort_marker = BASE / "runs" / run_id / "aborted" / host
    if abort_marker.exists():
        return {
            "host": host,
            "metric": metric,
            "round": round_no,
            "aborted": True,
            "reason": "host_already_aborted",
            "status": "refused",
        }
    run_dir.mkdir(parents=True, exist_ok=True)
    container = f"gpu-eq-{host}-{run_id}-r{round_no}-{metric}"
    # 同名の古いコンテナ（自分のものだけ）を先に除去する
    _docker(container, "rm", "-f")
    monitor_duration = float(warmup + duration + MONITOR_BUFFER_S)
    monitor_proc = subprocess.Popen(
        [
            PY,
            str(scripts / "monitor.py"),
            "--run-dir",
            str(run_dir),
            "--container",
            container,
            "--duration",
            str(monitor_duration),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    model_mounts: list[str] = []
    if metric == "embed":
        model_mounts = ["-v", f"{EMBED_MOUNT[0]}:{EMBED_MOUNT[1]}:ro"]
    elif metric == "score":
        model_mounts = ["-v", f"{SCORE_MOUNT[0]}:{SCORE_MOUNT[1]}:ro"]
    workload_cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        container,
        "--gpus",
        "all",
        "--ipc",
        "host",
        "-v",
        f"{scripts}:/work/scripts:ro",
        "-v",
        f"{run_dir}:/run",
        *model_mounts,
        IMAGE,
        "python3",
        "/work/scripts/workload.py",
        metric,
        "--run-dir",
        "/run",
        "--warmup",
        str(warmup),
        "--duration",
        str(duration),
    ]
    exit_code = -1
    with (
        (run_dir / "workload.jsonl").open("w") as out,
        (run_dir / "workload.stderr").open("w") as err,
    ):
        try:
            done = subprocess.run(
                workload_cmd,
                stdout=out,
                stderr=err,
                timeout=warmup + duration + MONITOR_BUFFER_S + 60,
            )
            exit_code = done.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
    monitor_proc.terminate()
    try:
        monitor_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        monitor_proc.kill()
    # 中止の判定：stop ファイル（監視の中止）またはコンテナの異常終了
    stop_file = run_dir / "stop"
    if stop_file.exists():
        reason = stop_file.read_text().strip() or "stopped_by_monitor"
    elif exit_code != 0:
        reason = f"workload_exit_{exit_code}"
    else:
        reason = None
    if reason is not None:
        abort_marker.parent.mkdir(parents=True, exist_ok=True)
        abort_marker.write_text(
            json.dumps(
                {
                    "host": host,
                    "metric": metric,
                    "reason": reason,
                    "at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    _docker(container, "rm", "-f")
    status = {
        "host": host,
        "metric": metric,
        "round": round_no,
        "container": container,
        "exit_code": exit_code,
        "aborted": reason is not None,
        "reason": reason,
        "status": "aborted" if reason is not None else "ok",
        "finished_at": datetime.now(UTC).isoformat(),
    }
    (run_dir / "status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n")
    return status


def main() -> None:
    """ホスト名を hostname から取得し，1 指標を測定する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--round", type=int, default=0)
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--duration", type=int, default=20)
    args = parser.parse_args()
    host = socket.gethostname()
    scripts = BASE
    status = run_metric(
        host, args.metric, args.run_id, args.round, args.warmup, args.duration, scripts
    )
    print(json.dumps(status, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
