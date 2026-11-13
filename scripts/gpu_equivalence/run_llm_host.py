"""単一ホストで Ollama による LLM 生成試験を監視付きで実行する（ホスト側で実行）．

固定タグの Ollama イメージで専用コンテナを起動し，専用 docker ネットワーク上で llm_client.py
が短／長入力・同時要求 1/4 の各条件を測る．host 側で monitor.py を並行して走らせ，中止条件
（80 °C 以上，熱減速，Xid）で Ollama とクライアントを止める．既存の研究コンテナや他人の
プロセスには触れず，自分のコンテナとネットワークだけを作る・除く．

使い方（ホスト上で，最新スクリプトが配置された base ディレクトリに対して）:
    python3 run_llm_host.py --run-id <id> --round <n>
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

BASE = Path("/home/denjo/workspace/ktakahashi/atrium/gpu-equivalence-20261111")
OLLAMA_IMAGE = "localhost:5000/mirror/ollama:0.35.1"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_MODELS_PARENT = "/home/denjo/workspace/ktakahashi/atrium/ollama"
OLLAMA_PORT = 11434
PY = sys.executable or "python3"
CONDITIONS: list[tuple[str, int]] = [("short", 1), ("short", 4), ("long", 1), ("long", 4)]
REQUESTS_PER_CONDITION = 100
NUM_PREDICT = 64
WARMUP_REQUESTS = 120
# Ollama 起動待ち＋プリロード＋ウォームアップ＋4 条件の合計（最悪 ~1560s）にバッファを足した値
MONITOR_DURATION_S = 2000.0
OLLAMA_READY_TIMEOUT_S = 180.0


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    """docker を実行して結果を返す（自分のコンテナ・ネットワークに対する操作）．"""
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)


def _container_ip(name: str) -> str:
    """コンテナの IP を返す（専用ネットワーク上のクライアント接続先）．"""
    result = _docker(
        "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name
    )
    return result.stdout.strip()


def _ollama_ready(url: str, timeout: float) -> bool:
    """Ollama の /api/tags が応答するまで待つ．"""
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


def _preload(url: str) -> None:
    """小さな生成要求でモデルを VRAM に読み込む（初回要求の遅さを本測定から除く）．"""
    payload = json.dumps(
        {"model": OLLAMA_MODEL, "prompt": "Hi", "stream": False, "options": {"num_predict": 1}}
    ).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/api/generate", data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        resp.read()


def _run_client(url: str, kind: str, concurrency: int, requests: int, out: Path, stop_file: Path) -> int:
    """llm_client.py をサブプロセスで実行して終了コードを返す．"""
    cmd = [
        PY,
        str(BASE / "llm_client.py"),
        "--url",
        url,
        "--model",
        OLLAMA_MODEL,
        "--prompt-kind",
        kind,
        "--num-predict",
        str(NUM_PREDICT),
        "--concurrency",
        str(concurrency),
        "--requests",
        str(requests),
        "--stop-file",
        str(stop_file),
        "--out",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if result.returncode != 0:
        print(f"llm_client failed: {result.stderr[-500:]}", file=sys.stderr, flush=True)
    return result.returncode


def run(host: str, run_id: str, round_no: int) -> dict[str, object]:
    """1 ホストで LLM 生成試験を 1 ラウンド実行し，状態を返す．"""
    run_dir = BASE / "runs" / run_id / f"round_{round_no}" / host / "llm"
    abort_marker = BASE / "runs" / run_id / "aborted" / host
    if abort_marker.exists():
        return {"host": host, "round": round_no, "status": "refused", "reason": "host_already_aborted"}
    run_dir.mkdir(parents=True, exist_ok=True)
    network = f"gpu-eq-{run_id}-r{round_no}"
    container = f"gpu-eq-ollama-{host}-{run_id}-r{round_no}"
    stop_file = run_dir / "stop"
    _docker("rm", "-f", container)
    _docker("network", "rm", network)
    create = _docker("network", "create", network)
    if create.returncode != 0:
        return {"host": host, "round": round_no, "status": "error", "reason": "network_create_failed"}
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
        "OLLAMA_NUM_PARALLEL=4",
        "-v",
        f"{OLLAMA_MODELS_PARENT}:/ollama",
        OLLAMA_IMAGE,
    )
    if started.returncode != 0:
        _docker("network", "rm", network)
        return {"host": host, "round": round_no, "status": "error", "reason": "ollama_start_failed"}
    monitor_proc = subprocess.Popen(
        [
            PY,
            str(BASE / "monitor.py"),
            "--run-dir",
            str(run_dir),
            "--container",
            container,
            "--duration",
            str(MONITOR_DURATION_S),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    aborted = False
    reason: str | None = None
    try:
        ip = _container_ip(container)
        url = f"http://{ip}:{OLLAMA_PORT}"
        if not _ollama_ready(url, OLLAMA_READY_TIMEOUT_S):
            reason = "ollama_not_ready"
            aborted = True
        else:
            _preload(url)
            _run_client(url, "short", 1, WARMUP_REQUESTS, run_dir / "warmup.jsonl", stop_file)
            if stop_file.exists():
                aborted = True
                reason = stop_file.read_text().strip() or "stopped_by_monitor"
            for kind, concurrency in CONDITIONS:
                if aborted:
                    break
                out = run_dir / f"llm_{kind}_c{concurrency}.jsonl"
                _run_client(url, kind, concurrency, REQUESTS_PER_CONDITION, out, stop_file)
                if stop_file.exists():
                    aborted = True
                    reason = stop_file.read_text().strip() or "stopped_by_monitor"
    finally:
        monitor_proc.terminate()
        try:
            monitor_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            monitor_proc.kill()
        _docker("rm", "-f", container)
        _docker("network", "rm", network)
    if reason is None and (run_dir / "stop").exists():
        reason = (run_dir / "stop").read_text().strip() or "stopped_by_monitor"
        aborted = True
    if reason is not None:
        abort_marker.parent.mkdir(parents=True, exist_ok=True)
        abort_marker.write_text(
            json.dumps({"host": host, "metric": "llm", "reason": reason, "at": datetime.now(UTC).isoformat()}, ensure_ascii=False) + "\n"
        )
    status = {
        "host": host,
        "round": round_no,
        "container": container,
        "aborted": aborted,
        "reason": reason,
        "status": "aborted" if aborted else "ok",
        "finished_at": datetime.now(UTC).isoformat(),
    }
    (run_dir / "status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n")
    return status


def main() -> None:
    """ホスト名を hostname から取得し，LLM 生成試験を 1 ラウンド実行する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--round", type=int, default=0)
    args = parser.parse_args()
    host = socket.gethostname()
    status = run(host, args.run_id, args.round)
    print(json.dumps(status, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
