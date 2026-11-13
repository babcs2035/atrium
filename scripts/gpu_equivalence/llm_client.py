"""Ollama への固定プロンプト生成要求を送り，生成速度・入力処理速度・TTFT を記録する．

ホスト側で実行する（stdlib のみ）．GPU の負荷は Ollama コンテナが担い，本スクリプトは
HTTP クライアントとして動作する．temperature=0 で出力を決定論にし，出力 SHA-256 を記録して
ホスト間のモデル・精度・量子化の差を検出する（計画書 §3 の出力照合）．

使い方（ホスト上で，Ollama コンテナが起動していること）:
    python3 llm_client.py --url http://127.0.0.1:11434 --model llama3.1:8b \
        --prompt-kind short --num-predict 64 --concurrency 1 --requests 100 \
        --out /path/to/run_dir/llm_short_c1.jsonl
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import time
import urllib.request
from collections import Counter
from pathlib import Path

# 固定プロンプト．全ホストへ同一のスクリプトを配布するため，ホスト間で同一になる．
_BASE = (
    "The research team reviewed the experimental results and scheduled a follow-up "
    "meeting to discuss the next steps for the project. "
)
PROMPTS = {
    "short": _BASE * 3,  # 約 128 トークン
    "long": _BASE * 30,  # 約 1024 トークン
}


def _as_int(value: object, default: int = 0) -> int:
    """object から int を安全に取り出す（bool は数値として扱わない）．"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return default


def _as_float(value: object, default: float = 0.0) -> float:
    """object から float を安全に取り出す（bool は数値として扱わない）．"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def percentile(values: list[float], p: float) -> float:
    """線形補間のパーセンタイルを返す（空リストは 0）．"""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return ordered[int(k)]
    return ordered[lo] * (hi - k) + ordered[hi] * (k - lo)


def analyze_final(final: dict[str, object]) -> dict[str, float | int]:
    """Ollama の最終チャンクから生成速度・入力処理速度・トークン数を計算する．"""
    eval_count = _as_int(final.get("eval_count"))
    eval_ns = _as_int(final.get("eval_duration"))
    prompt_count = _as_int(final.get("prompt_eval_count"))
    prompt_ns = _as_int(final.get("prompt_eval_duration"))
    gen_speed = eval_count / (eval_ns / 1e9) if eval_ns > 0 else 0.0
    prompt_speed = prompt_count / (prompt_ns / 1e9) if prompt_ns > 0 else 0.0
    return {
        "eval_count": eval_count,
        "gen_tokens_per_s": gen_speed,
        "prompt_eval_count": prompt_count,
        "prompt_tokens_per_s": prompt_speed,
    }


def one_request(
    url: str, model: str, prompt: str, num_predict: int, temperature: float
) -> dict[str, object]:
    """1 つのストリーミング生成要求を送り，TTFT・総時間・速度・出力ハッシュを返す．"""
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {"num_predict": num_predict, "temperature": temperature},
        }
    ).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    start = time.monotonic()
    ttft: float | None = None
    final: dict[str, object] | None = None
    text_parts: list[str] = []
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            chunk = json.loads(line)
            if ttft is None:
                ttft = time.monotonic() - start
            if "response" in chunk:
                text_parts.append(str(chunk["response"]))
            if chunk.get("done"):
                final = chunk
                break
    total = time.monotonic() - start
    if final is None or ttft is None:
        return {"error": "no_done_chunk", "total_time_s": total}
    text = "".join(text_parts)
    return {
        "ttft_s": ttft,
        "total_time_s": total,
        "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "output_chars": len(text),
        **analyze_final(final),
    }


def run_condition(
    url: str,
    model: str,
    prompt: str,
    num_predict: int,
    concurrency: int,
    n_requests: int,
    temperature: float,
    stop_file: Path | None = None,
    max_seconds: float | None = None,
) -> list[dict[str, object]]:
    """指定の同時要求数で生成要求を送り，要求ごとの記録を返す．

    stop_file が出現したら新規要求の投入を止める（進行中の要求は完了を待つ）．
    max_seconds が設定されれば，要求数に達する前にその時間で停止する（持続負荷用）．
    """
    results: list[dict[str, object]] = []
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending: dict[concurrent.futures.Future[dict[str, object]], int] = {}
        submitted = 0
        while submitted < n_requests or pending:
            if stop_file is not None and stop_file.exists():
                break
            if max_seconds is not None and time.monotonic() - start >= max_seconds:
                break
            while len(pending) < concurrency and submitted < n_requests:
                future = pool.submit(one_request, url, model, prompt, num_predict, temperature)
                pending[future] = submitted
                submitted += 1
            finished = [future for future in pending if future.done()]
            for future in finished:
                index = pending.pop(future)
                record = future.result()
                record["request_index"] = index
                record["completed_at"] = time.time()
                results.append(record)
            if not finished:
                time.sleep(0.1)
    return results


def summarize(results: list[dict[str, object]]) -> dict[str, object]:
    """要求ごとの記録から中央値・p50/p95・出力ハッシュの一致状況を集約する．"""
    ok = [r for r in results if "error" not in r]
    gen_speeds = [_as_float(r.get("gen_tokens_per_s")) for r in ok]
    prompt_speeds = [_as_float(r.get("prompt_tokens_per_s")) for r in ok]
    totals = [_as_float(r.get("total_time_s")) for r in ok]
    ttfts = [_as_float(r.get("ttft_s")) for r in ok]
    hashes = Counter(str(r.get("output_sha256")) for r in ok)
    eval_counts = Counter(_as_int(r.get("eval_count")) for r in ok)
    return {
        "n_requests": len(results),
        "n_ok": len(ok),
        "n_error": len(results) - len(ok),
        "gen_tokens_per_s_median": sum(gen_speeds) / len(gen_speeds) if gen_speeds else 0.0,
        "prompt_tokens_per_s_median": sum(prompt_speeds) / len(prompt_speeds) if prompt_speeds else 0.0,
        "total_time_p50": percentile(totals, 50),
        "total_time_p95": percentile(totals, 95),
        "ttft_p50": percentile(ttfts, 50),
        "ttft_p95": percentile(ttfts, 95),
        "distinct_output_hashes": len(hashes),
        "dominant_output_sha256": hashes.most_common(1)[0][0] if hashes else None,
        "distinct_eval_counts": dict(eval_counts),
    }


def main() -> None:
    """引数から条件を読み，要求を送って JSONL と集約を出力する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt-kind", choices=sorted(PROMPTS), required=True)
    parser.add_argument("--num-predict", type=int, default=64)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--stop-file", type=Path, default=None)
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    prompt = PROMPTS[args.prompt_kind]
    results = run_condition(
        args.url,
        args.model,
        prompt,
        args.num_predict,
        args.concurrency,
        args.requests,
        args.temperature,
        args.stop_file,
        args.max_seconds,
    )
    aborted = args.stop_file is not None and args.stop_file.exists()
    summary = summarize(results)
    summary["aborted"] = aborted
    if aborted:
        summary["stop_reason"] = args.stop_file.read_text().strip() or "stopped_by_monitor"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as stream:
        for record in results:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.write(json.dumps({"kind": "summary", **summary}, ensure_ascii=False) + "\n")
    print(json.dumps({"out": str(args.out), **summary}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
