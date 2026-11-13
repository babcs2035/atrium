"""測定 manifest を固定する．

計画書 §5 の「測定開始前に manifest に固定し，結果を見て広げない」を満たすため，対象ホスト，
固定 digest，各指標のパラメータ，反復順の seed，許容幅，中止値，測定スクリプトの source hash，
実行環境を 1 つの JSON に書き出す．測定はこの manifest を読む．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/make_manifest.py \
        --run-id <id> --out results/20261111_gpu_equivalence
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

HOSTS = [f"wafl50{i}" for i in range(10)]
IMAGE = "localhost:5000/atrium-full@sha256:97f37fc180a50fc0012112588d3a203bbc0aceb0c50e545d112f3f42ab706e7f"
OLLAMA_IMAGE = "localhost:5000/mirror/ollama:0.35.1"
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_BINARY_SHA256 = "8fd655c8fde7b524dc3a64ae1006f9e8d0344dec93b1796fc9f87b7c5179fbb9"
OLLAMA_MODEL_MANIFEST_SHA256 = "46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e"
SEED = 20261111
ROUNDS = 10
# 各指標の固定パラメータ（workload.py の定数と一致させる）
METRIC_PARAMS = {
    "fp32": {"matrix_size": 4096, "dtype": "float32", "tf32": False},
    "fp16": {"matrix_size": 4096, "dtype": "float16", "tf32": False},
    "vram_bandwidth": {"copy_bytes": 268435456},
    "h2d": {"copy_bytes": 268435456, "pinned": True},
    "d2h": {"copy_bytes": 268435456, "pinned": True},
    "vram_check": {"vram_bytes": 1073741824},
    "embed": {
        "model": "Snowflake/snowflake-arctic-embed-m-v1.5",
        "revision": "e58a8f756156a1293d763f17e3aae643474e9b8a",
        "batch": 16,
        "length": 512,
    },
    "score": {
        "model": "BAAI/bge-reranker-v2-m3",
        "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        "batch": 8,
        "length": 1024,
        "dtype": "float16",
    },
}
# LLM 生成の固定パラメータ（llm_client.py / run_llm_host.py と一致させる）
LLM_PARAMS = {
    "image": OLLAMA_IMAGE,
    "model": OLLAMA_MODEL,
    "binary_sha256": OLLAMA_BINARY_SHA256,
    "model_manifest_sha256": OLLAMA_MODEL_MANIFEST_SHA256,
    "num_predict": 64,
    "temperature": 0.0,
    "concurrency": [1, 4],
    "prompt_kinds": ["short", "long"],
    "requests_per_condition": 100,
    "ollama_num_parallel": 4,
}
# 計画書 §5 の提案基準（測定前に固定）
TOLERANCE = {"main_ratio": [0.95, 1.05], "p95_ratio": [0.90, 1.10], "sustained_min": 0.95}
ABORT = {
    "temperature_c": 80,
    "thermal_slowdown": True,
    "xid": True,
    "verification_mismatch": True,
}


def source_hashes(scripts: Path) -> dict[str, str]:
    """測定に関わるホスト側スクリプトの sha256 を返す．"""
    names = [
        "workload.py",
        "safety.py",
        "monitor.py",
        "run_host.py",
        "llm_client.py",
        "run_llm_host.py",
        "run_sustained_host.py",
    ]
    return {name: hashlib.sha256((scripts / name).read_bytes()).hexdigest() for name in names}


def main() -> None:
    """manifest を JSON で書き出す．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    scripts = Path(__file__).parent
    manifest = {
        "run_id": args.run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "hosts": HOSTS,
        "image": IMAGE,
        "seed": SEED,
        "rounds": ROUNDS,
        "metrics": METRIC_PARAMS,
        "llm": LLM_PARAMS,
        "warmup_s": 180,
        "duration_s": 20,
        "tolerance": TOLERANCE,
        "abort": ABORT,
        "source_hashes": source_hashes(scripts),
        "python": sys.version.split()[0],
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"manifest_{args.run_id}.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
