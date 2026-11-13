"""単一ホストで 30 分の持続負荷を監視付きで実行する（ホスト側で実行）．

計画書 §4 の持続負荷を実装する．「生成」は Ollama で連続生成を，「採点」は embed/score の
モデル forward を 30 分間続け，1 秒間隔の monitor.py で温度・クロック・減速を記録する．
開始直後と末尾 10 分の速度比較で熱による減速を検出する．中止条件（80 °C 以上，熱減速，Xid）
で停止し，中止を未完遂として記録する．

使い方（ホスト上で）:
    python3 run_sustained_host.py --run-id <id> --kind generation --rep <n>
    python3 run_sustained_host.py --run-id <id> --kind scoring --rep <n>
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

BASE = Path("/home/denjo/workspace/ktakahashi/atrium/gpu-equivalence-20261111")
PY = sys.executable or "python3"
OLLAMA_IMAGE = "localhost:5000/mirror/ollama:0.35.1"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_MODELS_PARENT = "/home/denjo/workspace/ktakahashi/atrium/ollama"
OLLAMA_PORT = 11434
SUSTAINED_S = 1800.0  # 30 分
MONITOR_BUFFER_S = 120.0
WARMUP_S = 180.0


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    """docker を実行して結果を返す（自分のコンテナ・ネットワークに対する操作）．"""
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)


def _container_ip(name: str) -> str:
    """コンテナの IP を返す．"""
    result = _docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name)
    return result.stdout.strip()


def _ollama_ready(url: str, timeout: float) -> bool:
    """Ollama の /api/tags が応答するまで待つ．"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/tags", timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(1.0)
    return False


def _run_scoring(host: str, run_id: str, rep: int, metric: str, run_dir: Path) -> str | None:
    """embed/score のモデル forward を 30 分実行し，中止理由（なければ None）を返す．"""
    container = f"gpu-eq-sust-{host}-{run_id}-{metric}-r{rep}"
    _docker("rm", "-f", container)
    model_mounts = (
        ["-v", "/home/denjo/workspace/ktakahashi/atrium/hf-cache/hub:/embed:ro"]
        if metric == "embed"
        else ["-v", "/home/denjo/.cache/huggingface/hub:/score:ro"]
    )
    monitor_proc = subprocess.Popen(
        [
            PY,
            str(BASE / "monitor.py"),
            "--run-dir",
            str(run_dir),
            "--container",
            container,
            "--duration",
            str(WARMUP_S + SUSTAINED_S + MONITOR_BUFFER_S),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with (run_dir / "workload.jsonl").open("w") as out:
            subprocess.run(
                [
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
                    f"{BASE}:/work/scripts:ro",
                    "-v",
                    f"{run_dir}:/run",
                    *model_mounts,
                    "localhost:5000/atrium-full@sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f",
                    "python3",
                    "/work/scripts/workload.py",
                    metric,
                    "--run-dir",
                    "/run",
                    "--warmup",
                    str(int(WARMUP_S)),
                    "--duration",
                    str(int(SUSTAINED_S)),
                ],
                stdout=out,
                stderr=subprocess.DEVNULL,
                timeout=int(WARMUP_S + SUSTAINED_S + MONITOR_BUFFER_S + 60),
            )
    finally:
        monitor_proc.terminate()
        try:
            monitor_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            monitor_proc.kill()
        _docker("rm", "-f", container)
    stop_file = run_dir / "stop"
    return stop_file.read_text().strip() if stop_file.exists() else None


def _run_generation(host: str, run_id: str, rep: int, run_dir: Path) -> str | None:
    """Ollama で 30 分の連続生成を実行し，中止理由（なければ None）を返す．"""
    network = f"gpu-eq-sust-{run_id}-r{rep}"
    container = f"gpu-eq-sust-ollama-{host}-{run_id}-r{rep}"
    _docker("rm", "-f", container)
    _docker("network", "rm", network)
    _docker("network", "create", network)
    started = _docker(
        "run",
        "-d",
        "--name",
        container,
        "--network",
        network,
        "--gpus",
        "all",
        "-e",
        "HOME=/ollama",
        "-e",
        "OLLAMA_MODELS=/ollama/models",
        "-e",
        "OLLAMA_KEEP_ALIVE=-1",
        "-e",
        "OLLAMA_NUM_PARALLEL=1",
        "-v",
        f"{OLLAMA_MODELS_PARENT}:/ollama",
        OLLAMA_IMAGE,
    )
    if started.returncode != 0:
        _docker("network", "rm", network)
        return "ollama_start_failed"
    monitor_proc = subprocess.Popen(
        [
            PY,
            str(BASE / "monitor.py"),
            "--run-dir",
            str(run_dir),
            "--container",
            container,
            "--duration",
            str(WARMUP_S + SUSTAINED_S + MONITOR_BUFFER_S),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ip = _container_ip(container)
        url = f"http://{ip}:{OLLAMA_PORT}"
        if not _ollama_ready(url, 180):
            return "ollama_not_ready"
        # 小さな要求でモデルを VRAM に読み込む
        payload = json.dumps({"model": OLLAMA_MODEL, "prompt": "Hi", "stream": False, "options": {"num_predict": 1}}).encode()
        req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
        subprocess.run(
            [
                PY,
                str(BASE / "llm_client.py"),
                "--url",
                url,
                "--model",
                OLLAMA_MODEL,
                "--prompt-kind",
                "short",
                "--num-predict",
                "64",
                "--concurrency",
                "1",
                "--requests",
                "100000",
                "--max-seconds",
                str(SUSTAINED_S),
                "--stop-file",
                str(run_dir / "stop"),
                "--out",
                str(run_dir / "generation.jsonl"),
            ],
            capture_output=True,
            text=True,
            timeout=int(SUSTAINED_S + MONITOR_BUFFER_S + 120),
        )
    finally:
        monitor_proc.terminate()
        try:
            monitor_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            monitor_proc.kill()
        _docker("rm", "-f", container)
        _docker("network", "rm", network)
    stop_file = run_dir / "stop"
    return stop_file.read_text().strip() if stop_file.exists() else None


def run(host: str, run_id: str, kind: str, rep: int) -> dict[str, object]:
    """1 ホストで持続負荷を 1 回実行し，状態を返す．"""
    abort_marker = BASE / "runs" / run_id / "aborted" / host
    if abort_marker.exists():
        return {"host": host, "kind": kind, "rep": rep, "status": "refused", "reason": "host_already_aborted"}
    if kind == "generation":
        run_dir = BASE / "runs" / run_id / "sustained" / host / f"generation_r{rep}"
        run_dir.mkdir(parents=True, exist_ok=True)
        reason = _run_generation(host, run_id, rep, run_dir)
    elif kind in {"embed", "score"}:
        run_dir = BASE / "runs" / run_id / "sustained" / host / f"{kind}_r{rep}"
        run_dir.mkdir(parents=True, exist_ok=True)
        reason = _run_scoring(host, run_id, rep, kind, run_dir)
    else:
        return {"host": host, "kind": kind, "rep": rep, "status": "error", "reason": "unknown_kind"}
    if reason is not None:
        abort_marker.parent.mkdir(parents=True, exist_ok=True)
        abort_marker.write_text(
            json.dumps({"host": host, "metric": kind, "reason": reason, "at": datetime.now(UTC).isoformat()}, ensure_ascii=False) + "\n"
        )
    status = {
        "host": host,
        "kind": kind,
        "rep": rep,
        "aborted": reason is not None,
        "reason": reason,
        "status": "aborted" if reason is not None else "ok",
        "finished_at": datetime.now(UTC).isoformat(),
    }
    (run_dir / "status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n")
    return status


def main() -> None:
    """ホスト名を hostname から取得し，持続負荷を 1 回実行する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--kind", choices=["generation", "embed", "score"], required=True)
    parser.add_argument("--rep", type=int, default=1)
    args = parser.parse_args()
    host = socket.gethostname()
    status = run(host, args.run_id, args.kind, args.rep)
    print(json.dumps(status, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
