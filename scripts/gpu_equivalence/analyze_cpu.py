"""CPU 予備測定の JSONL をホストごとに要約する．

計画書の「10 台の順序を入れ替えた 10 ラウンド」とは異なる，ホスト単位で 10 回連続実行した
予備測定であるため，同等性判定には使わない．中央値と範囲を比較材料として残すだけである．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/analyze_cpu.py \
        --cpu results/20261111_gpu_equivalence/cpu \
        --out results/20261111_gpu_equivalence/cpu
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

HOSTS = [f"wafl50{i}" for i in range(10)]
METRICS = ["cpu_sha256", "ram_copy", "disk_write_fsync", "disk_read_advised_cold"]


def _stat(values: list[float]) -> dict[str, float | int]:
    """中央値・最小・最大・サンプル数を返す．"""
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "n": len(values),
    }


def _bytes_per_s(row: dict[str, object]) -> float | None:
    """JSON の bytes_per_s が数値なら float で返し，そうでなければ None を返す．"""
    value = row.get("bytes_per_s")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def summarize(host: str, path: Path) -> tuple[dict[str, object], dict[str, list[float]]]:
    """ホストのメタ情報と，指標ごとの生の byte/s サンプルを返す．"""
    rows: list[dict[str, object]] = [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]
    env = next((row for row in rows if row.get("kind") == "environment"), {})
    meta: dict[str, object] = {
        "host": host,
        "python": env.get("python"),
        "affinity": env.get("affinity"),
    }
    samples: dict[str, list[float]] = {}
    for metric in METRICS:
        values: list[float] = []
        for row in rows:
            if row.get("kind") == metric:
                value = _bytes_per_s(row)
                if value is not None:
                    values.append(value)
        samples[metric] = values
    return meta, samples


def main() -> None:
    """ホストごとの要約を JSON と markdown で保存する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    records: list[tuple[dict[str, object], dict[str, list[float]]]] = [
        summarize(host, args.cpu / f"{host}.jsonl") for host in HOSTS
    ]
    json_rows = [
        {
            **meta,
            **{metric: (_stat(samples[metric]) if samples[metric] else None) for metric in METRICS},
        }
        for meta, samples in records
    ]
    (args.out / "cpu_summary.json").write_text(
        json.dumps(json_rows, indent=2, ensure_ascii=False) + "\n"
    )

    lines = [
        "# CPU 予備測定要約（wafl500〜509）",
        "",
        "ホスト単位で 10 回連続実行した予備測定であり，計画書の 10 ラウンドの乱数入れ替えとは異なる．同等性判定には使わない（引き継ぎ文書）．単位は byte/s．",
        "",
    ]
    for metric in METRICS:
        lines.append(f"## {metric}")
        lines.append("")
        lines.append("| host | median | min | max | n |")
        lines.append("|---|---|---|---|---|")
        for meta, samples in records:
            values = samples[metric]
            if not values:
                lines.append(f"| {meta['host']} | — | — | — | 0 |")
            else:
                stat = _stat(values)
                lines.append(
                    f"| {meta['host']} | {stat['median']:.0f} | {stat['min']:.0f} | {stat['max']:.0f} | {stat['n']} |"
                )
        lines.append("")
    (args.out / "cpu_summary.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out / 'cpu_summary.md'}")


if __name__ == "__main__":
    main()
