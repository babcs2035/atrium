"""固定入力で CUDA・埋め込み・採点を測り，区間ごとの一次記録を出力する．

監視（host 側の monitor.py）が書く stop ファイルと heartbeat ファイルを読み，中止条件の発動
または監視喪失（心拍の鮮度喪失）を検出したら測定を止める．run-dir はコンテナ内で /run に
バインドマウントされることを想定する．

使い方（コンテナ内で）:
    python3 workload.py <metric> --run-dir /run --warmup <s> --duration <s>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import torch

MIB = 1024 * 1024
MATRIX_SIZE = 4096
COPY_BYTES = 256 * MIB
VRAM_BYTES = 1024 * MIB
EMBED_PATH = "/embed/models--Snowflake--snowflake-arctic-embed-m-v1.5/snapshots/e58a8f756156a1293d763f17e3aae643474e9b8a"
SCORE_PATH = (
    "/score/models--BAAI--bge-reranker-v2-m3/snapshots/953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
)
HEARTBEAT_WAIT_S = 15.0
HEARTBEAT_MAX_STALE_S = 5.0


def should_stop(run_dir: Path, max_stale_s: float) -> tuple[bool, str | None]:
    """stop ファイルの存在と心拍の鮮度から停止を判定する．"""
    stop_file = run_dir / "stop"
    if stop_file.exists():
        return True, stop_file.read_text().strip() or "stopped_by_monitor"
    heartbeat = run_dir / "heartbeat"
    if heartbeat.exists():
        try:
            last = float(heartbeat.read_text().strip())
        except ValueError:
            return True, "heartbeat_unreadable"
        if time.time() - last > max_stale_s:
            return True, "monitoring_lost"
    return False, None


def wait_for_heartbeat(run_dir: Path) -> str | None:
    """監視の起動を待って心拍ファイルの出現を確認する．出現しなければ理由を返す．"""
    deadline = time.monotonic() + HEARTBEAT_WAIT_S
    while time.monotonic() < deadline:
        if (run_dir / "heartbeat").exists():
            return None
        time.sleep(0.5)
    return "monitor_not_started"


def emit(record: dict[str, object]) -> None:
    """標準出力へ読み取り可能な測定レコードを即時出力する．"""
    print(json.dumps({"timestamp": time.time(), **record}), flush=True)


def temperature() -> float:
    """実機の温度を安定判定用に取得する．"""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return float(result.stdout.strip())


def interval(operation: Callable[[], None], units: float, seconds: float) -> tuple[float, float]:
    """同期した GPU 時間と単位量を返す．ホスト側の投入時間は別に残す．"""
    start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
    wall = time.monotonic()
    elapsed, count = 0.0, 0
    while time.monotonic() - wall < seconds:
        start.record()
        operation()
        end.record()
        end.synchronize()
        elapsed += start.elapsed_time(end) / 1000
        count += 1
    return elapsed, count * units


def micro_operation(metric: str) -> tuple[Callable[[], None], float, str]:
    """GPU に常駐する演算または転送を構築する．"""
    if metric in {"fp32", "fp16"}:
        dtype = torch.float32 if metric == "fp32" else torch.float16
        a = torch.randn((MATRIX_SIZE, MATRIX_SIZE), dtype=dtype, device="cuda")
        b = torch.randn_like(a)
        result = torch.empty_like(a)
        reference = a[:4].float() @ b[:, :4].float()
        torch.mm(a, b, out=result)
        torch.cuda.synchronize()
        if not torch.allclose(
            result[:4, :4].float(),
            reference,
            atol=0.15 if metric == "fp16" else 0.002,
            rtol=0.02 if metric == "fp16" else 0.002,
        ):
            raise RuntimeError("matrix output verification failed")

        def operation() -> None:
            """事前確保した出力へ行列積を計算する．"""
            torch.mm(a, b, out=result)

        return operation, 2.0 * MATRIX_SIZE**3, "FLOP/s"
    if metric == "vram_check":
        data = torch.empty(VRAM_BYTES // 4, dtype=torch.int32, device="cuda")
        pattern = 0x12345678

        def operation() -> None:
            """1 GiB を書き込み，異なるビットパターンを検証する．"""
            data.fill_(pattern)
            if int(torch.count_nonzero(data != pattern).item()) != 0:
                raise RuntimeError("VRAM pattern verification failed")
            data.fill_(-pattern)
            if int(torch.count_nonzero(data != -pattern).item()) != 0:
                raise RuntimeError("VRAM inverse pattern verification failed")

        return operation, float(VRAM_BYTES * 4), "verified_byte/s"
    a = torch.full((COPY_BYTES,), 37, dtype=torch.uint8, device="cuda")
    b = torch.empty_like(a)
    host = torch.empty(COPY_BYTES, dtype=torch.uint8, pin_memory=True)
    host.fill_(37)
    if metric == "vram_bandwidth":

        def operation() -> None:
            """キャッシュ容量を超える GPU 内コピーを行う．"""
            b.copy_(a)

        units = float(COPY_BYTES * 2)
    elif metric == "h2d":

        def operation() -> None:
            """pinned memory から GPU へ転送する．"""
            b.copy_(host, non_blocking=True)

        units = float(COPY_BYTES)
    elif metric == "d2h":

        def operation() -> None:
            """GPU から pinned memory へ転送する．"""
            host.copy_(a, non_blocking=True)

        units = float(COPY_BYTES)
    else:
        raise ValueError(metric)
    operation()
    torch.cuda.synchronize()
    checked = host if metric == "d2h" else b
    if int(torch.count_nonzero(checked != 37).item()):
        raise RuntimeError("copy verification failed")
    return operation, units, "byte/s"


def model_operation(metric: str) -> tuple[Callable[[], None], float, str]:
    """オフラインの固定 revision から実際のモデルを読み込む．"""
    from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

    path = EMBED_PATH if metric == "embed" else SCORE_PATH
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    batch_size, length = (16, 512) if metric == "embed" else (8, 1024)
    text = "The research team reviewed the experimental results and scheduled a meeting. " * 120
    texts = [text] * batch_size
    if metric == "embed":
        model = AutoModel.from_pretrained(path, local_files_only=True).to("cuda").eval()
        batch = tokenizer(
            texts, padding="max_length", truncation=True, max_length=length, return_tensors="pt"
        ).to("cuda")
    else:
        model = (
            AutoModelForSequenceClassification.from_pretrained(
                path, local_files_only=True, torch_dtype=torch.float16
            )
            .to("cuda")
            .eval()
        )
        batch = tokenizer(
            ["What did the team review?"] * batch_size,
            texts,
            padding="max_length",
            truncation=True,
            max_length=length,
            return_tensors="pt",
        ).to("cuda")
    with torch.inference_mode():
        output = model(**batch)
    values = output.last_hidden_state[:, 0].float() if metric == "embed" else output.logits.float()
    if not bool(torch.isfinite(values).all()):
        raise RuntimeError("model output verification failed")
    emit(
        {
            "kind": "output_reference",
            "metric": metric,
            "values": values.flatten()[:32].tolist(),
            "input_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "batch": batch_size,
            "length": length,
        }
    )

    def operation() -> None:
        """同じトークン化済み入力を GPU 上で評価する．"""
        with torch.inference_mode():
            model(**batch)

    return operation, float(batch_size), "item/s"


def main() -> None:
    """ウォームアップと本測定を分離して実行する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "metric",
        choices=[
            "info",
            "fp32",
            "fp16",
            "vram_bandwidth",
            "h2d",
            "d2h",
            "vram_check",
            "embed",
            "score",
        ],
    )
    parser.add_argument("--warmup", type=int, default=180)
    parser.add_argument("--duration", type=int, default=20)
    parser.add_argument("--stability", action="store_true")
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()
    torch.set_num_threads(4)
    torch.manual_seed(20261111)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    properties = torch.cuda.get_device_properties(0)
    emit(
        {
            "kind": "device",
            "torch": str(torch.__version__),
            "cuda": torch.version.cuda,
            "name": properties.name,
            "capability": [properties.major, properties.minor],
            "sm_count": properties.multi_processor_count,
            "total_memory": properties.total_memory,
            "tf32": False,
        }
    )
    if args.metric == "info":
        return
    operation, units, unit = (
        model_operation(args.metric)
        if args.metric in {"embed", "score"}
        else micro_operation(args.metric)
    )
    abort_reason: str | None = None
    if args.run_dir is not None:
        wait_reason = wait_for_heartbeat(args.run_dir)
        if wait_reason is not None:
            emit({"kind": "result", "metric": args.metric, "aborted": True, "reason": wait_reason})
            return
    warmup_start = time.monotonic()
    warm_samples: list[tuple[float, float, float]] = []
    stable = False
    while time.monotonic() - warmup_start < (600 if args.stability else args.warmup):
        if args.run_dir is not None:
            stopped, reason = should_stop(args.run_dir, HEARTBEAT_MAX_STALE_S)
            if stopped:
                abort_reason = reason
                break
        elapsed, amount = interval(operation, units, 1)
        wall_elapsed = time.monotonic() - warmup_start
        temp = temperature()
        warm_samples.append((wall_elapsed, amount / elapsed, temp))
        emit(
            {
                "kind": "warmup",
                "metric": args.metric,
                "elapsed_s": wall_elapsed,
                "rate": amount / elapsed,
                "temperature_c": temp,
                "unit": unit,
            }
        )
        if wall_elapsed >= args.warmup:
            if not args.stability:
                break
            recent = [row for row in warm_samples if row[0] >= wall_elapsed - 180]
            minute_rates = []
            for minute in range(3):
                rates = [
                    row[1]
                    for row in recent
                    if wall_elapsed - (minute + 1) * 60 <= row[0] < wall_elapsed - minute * 60
                ]
                if rates:
                    minute_rates.append(sum(rates) / len(rates))
            if len(minute_rates) == 3:
                mean = sum(minute_rates) / 3
                stable = max(row[2] for row in recent) - min(row[2] for row in recent) <= 2 and all(
                    abs(rate / mean - 1) <= 0.03 for rate in minute_rates
                )
                if stable:
                    break
    emit(
        {
            "kind": "warmup_end",
            "metric": args.metric,
            "stable": stable,
            "stability_required": args.stability,
            "elapsed_s": time.monotonic() - warmup_start,
        }
    )
    measured_start = time.monotonic()
    total_s, total_units = 0.0, 0.0
    while time.monotonic() - measured_start < args.duration:
        if args.run_dir is not None:
            stopped, reason = should_stop(args.run_dir, HEARTBEAT_MAX_STALE_S)
            if stopped:
                abort_reason = reason
                break
        elapsed, amount = interval(operation, units, 1)
        total_s += elapsed
        total_units += amount
        emit(
            {
                "kind": "sample",
                "metric": args.metric,
                "rate": amount / elapsed,
                "unit": unit,
                "elapsed_s": time.monotonic() - measured_start,
                "gpu_seconds": elapsed,
            }
        )
    if abort_reason is not None:
        emit({"kind": "result", "metric": args.metric, "aborted": True, "reason": abort_reason})
        return
    emit(
        {
            "kind": "result",
            "metric": args.metric,
            "rate": total_units / total_s,
            "unit": unit,
            "gpu_seconds": total_s,
            "stable": stable,
            "peak_memory_bytes": torch.cuda.max_memory_allocated(),
        }
    )


if __name__ == "__main__":
    main()
