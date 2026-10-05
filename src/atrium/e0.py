"""E0（実機の性能実測）のうち Python で測るものと，結果の集約．

ホストで直接測るもの（メモリの装着・ストレージ・llama-bench・RTT・iperf3）は
scripts/tasks/start.sh が測り，結果を results/<run>/e0/<host>/ に置く．ここでは次を担う．

- faiss: fp16 平坦索引（専門家と同じ IndexScalarQuantizer）の 1 クエリあたりの検索時間
- medcpt: MedCPT のクエリ埋め込みの CPU での所要時間（入力は長さだけを揃えた文字列．
  所要時間はトークン数で決まり，内容には依存しないため）
- summarize: 上記とホストでの実測をまとめて e0_summary.md にする
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import faiss
import numpy as np

DIM = 768
K = 50
ADD_CHUNK = 100_000
TOKEN_LENGTHS = (16, 32, 64, 128)


def _percentiles(values: list[float]) -> dict[str, float]:
    return {
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "mean": float(np.mean(values)),
    }


def faiss_bench(n_vectors: list[int], n_queries: int, seed: int) -> list[dict[str, Any]]:
    """乱数ベクトルの fp16 平坦索引で，1 クエリずつ上位 50 件を検索する時間を測る．"""
    rng = np.random.default_rng(seed)
    out: list[dict[str, Any]] = []
    for n in n_vectors:
        index = faiss.IndexScalarQuantizer(
            DIM, faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT
        )
        start = time.perf_counter()
        for begin in range(0, n, ADD_CHUNK):
            index.add(rng.standard_normal((min(ADD_CHUNK, n - begin), DIM), dtype=np.float32))
        build_s = time.perf_counter() - start
        queries = rng.standard_normal((n_queries, DIM), dtype=np.float32)
        index.search(queries[:1], K)  # 初回のページインを計測から除く
        times = []
        for q in queries:
            start = time.perf_counter()
            index.search(q.reshape(1, -1), K)
            times.append(time.perf_counter() - start)
        out.append(
            {
                "n_vectors": n,
                "index_bytes": n * DIM * 2,
                "build_s": build_s,
                "search_s": _percentiles(times),
                "omp_threads": faiss.omp_get_max_threads(),
            }
        )
        del index
    return out


def medcpt_bench(query_encoder: str, article_encoder: str, repeats: int) -> list[dict[str, Any]]:
    """MedCPT のクエリ側モデルで，入力長ごとに 1 件ずつ埋め込む時間を測る（GPU のないホストでは CPU）．

    質問者が使うのと同じ MedcptEncoder で測る．
    """
    import torch

    from atrium.encoders import MedcptEncoder

    encoder = MedcptEncoder(query_encoder, article_encoder, load_article=False)
    out: list[dict[str, Any]] = []
    for n_tokens in TOKEN_LENGTHS:
        text = " ".join(["patient"] * n_tokens)
        encoder.encode_queries([text])  # 初回の遅延初期化を計測から除く
        times = []
        for _ in range(repeats):
            start = time.perf_counter()
            encoder.encode_queries([text])
            times.append(time.perf_counter() - start)
        out.append(
            {
                "n_tokens": n_tokens,
                "embed_s": _percentiles(times),
                "torch_threads": torch.get_num_threads(),
            }
        )
    return out


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def summarize(e0_dir: Path) -> str:
    """e0/<host>/ 以下の実測結果を Markdown の表にまとめる．"""
    lines = ["# E0 実測結果", ""]
    hosts = sorted(p for p in e0_dir.iterdir() if p.is_dir())

    lines += [
        "## メモリとストレージ",
        "",
        "| ホスト | DIMM | 合計 (GB) | / の空き (GB) |",
        "|---|---|---|---|",
    ]
    for h in hosts:
        dimms = (
            (h / "dimm.txt").read_text(encoding="utf-8").strip()
            if (h / "dimm.txt").exists()
            else "-"
        )
        mem = _read_json(h / "host.json") or {}
        lines.append(
            f"| {h.name} | {dimms.replace(chr(10), ', ')} | {mem.get('mem_total_gb', '-')} | "
            f"{mem.get('root_avail_gb', '-')} |"
        )

    lines += [
        "",
        "## llama-bench（tokens/s）",
        "",
        "| ホスト | モデル | 試験 | tokens/s | 標準偏差 |",
        "|---|---|---|---|---|",
    ]
    for h in hosts:
        for path in sorted(h.glob("llama-bench-*.json")):
            for row in _read_json(path) or []:
                test = f"pp{row['n_prompt']}" if row.get("n_gen", 0) == 0 else f"tg{row['n_gen']}"
                lines.append(
                    f"| {h.name} | {path.stem.removeprefix('llama-bench-')} | {test} | "
                    f"{row['avg_ts']:.1f} | {row['stddev_ts']:.1f} |"
                )

    lines += [
        "",
        "## FAISS fp16 平坦索引（1 クエリ，上位 50 件）",
        "",
        "| ホスト | ベクトル数 | 索引 (GB) | p50 (ms) | p95 (ms) |",
        "|---|---|---|---|---|",
    ]
    for h in hosts:
        for row in _read_json(h / "faiss.json") or []:
            lines.append(
                f"| {h.name} | {row['n_vectors']:,} | {row['index_bytes'] / 1e9:.2f} | "
                f"{row['search_s']['p50'] * 1e3:.1f} | {row['search_s']['p95'] * 1e3:.1f} |"
            )

    lines += [
        "",
        "## MedCPT クエリ埋め込み（CPU）",
        "",
        "| ホスト | トークン数 | p50 (ms) | p95 (ms) |",
        "|---|---|---|---|",
    ]
    for h in hosts:
        for row in _read_json(h / "medcpt.json") or []:
            lines.append(
                f"| {h.name} | {row['n_tokens']} | {row['embed_s']['p50'] * 1e3:.1f} | "
                f"{row['embed_s']['p95'] * 1e3:.1f} |"
            )

    failures = [
        f"| {h.name} | {line} |"
        for h in hosts
        if (h / "errors.txt").exists()
        for line in (h / "errors.txt").read_text(encoding="utf-8").splitlines()
    ]
    if failures:
        lines += ["", "## 失敗した計測", "", "| ホスト | 内容 |", "|---|---|", *failures]

    lines += [
        "",
        "## ネットワーク",
        "",
        "| 組 | RTT 平均 (ms) | スループット (Mbit/s) |",
        "|---|---|---|",
    ]
    for path in sorted((e0_dir / "net").glob("*.json")) if (e0_dir / "net").exists() else []:
        row = _read_json(path) or {}
        lines.append(
            f"| {path.stem} | {row.get('rtt_avg_ms', '-')} | {row.get('throughput_mbps', '-')} |"
        )
    return "\n".join(lines) + "\n"
