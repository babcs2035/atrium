"""p0005 T4：負荷試験の集計（H-L1〜H-L3 の判定）．既存の結果ファイルを読むだけで，何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261014_t4_load_summary/t4_summary.py > results/20261014_t4_load_summary/t4_summary.out

定義（p0005 §6.3・§6.4．固定）:
- 判定は EQ6・EQ10 と同じ規則：比べる 2 条件の 3 回の中央値（p50 または p95）の幅が重ならないときだけ「差がある」．
- 処理量 = 成功した問の数 ÷（run_meta.json の finished_at − started_at．質問の処理の開始から終了まで）．
- H-L1：parallel = 30 で，flood_score の e2e p95 が multi_centroid より大きい（3 回の幅が重ならない）．
- H-L2：parallel を 1 から 30 に上げたときの e2e p50 の増加の倍率（3 回の中央値どうしの比）が，flood_score の方が multi_centroid より大きい．
- H-L3：処理量が前の段より 10% 以上増えなくなる最小の parallel が，flood_score の方が小さい（無ければ「30 より上」）．
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from atrium.analysis import read_results

RESULTS = Path("results")
ROUTINGS = ("flood_score", "multi_centroid")
PARALLELS = (1, 4, 8, 16, 30)
REPEATS = (1, 2, 3)
SATURATION_GAIN = 1.10


def run_dir(routing: str, parallel: int, repeat: int) -> Path:
    """実行のディレクトリ（20261014_t4_<routing>_p<parallel>_r<repeat>）．"""
    return RESULTS / f"20261014_t4_{routing}_p{parallel}_r{repeat}"


def summarize(path: Path) -> dict[str, Any] | None:
    """1 回の実行の指標．実行が無ければ None．"""
    if not (path / "results.jsonl").exists():
        return None
    rows = read_results(path / "results.jsonl")
    meta = json.loads((path / "run_meta.json").read_text(encoding="utf-8"))
    ok = [r for r in rows if r["error"] is None]
    e2e = [r["timings"]["e2e_s"] for r in ok]
    probe = [r["timings"]["probe_s"] for r in ok if "probe_s" in r["timings"]]
    errors = Counter((r["error"] or "").split(":")[0] for r in rows if r["error"] is not None)
    elapsed = meta["finished_at"] - meta["started_at"]
    return {
        "n": len(rows),
        "failed": len(rows) - len(ok),
        "failure_rate": (len(rows) - len(ok)) / len(rows),
        "errors": dict(errors),
        "e2e_p50": float(np.percentile(e2e, 50)) if e2e else None,
        "e2e_p95": float(np.percentile(e2e, 95)) if e2e else None,
        "probe_p50": float(np.percentile(probe, 50)) if probe else None,
        "probe_p95": float(np.percentile(probe, 95)) if probe else None,
        "throughput": len(ok) / elapsed,
        "elapsed_s": elapsed,
        "query_recipients": float(np.mean([r.get("query_recipients", 0) for r in ok]))
        if ok
        else None,
    }


def spread(runs: list[dict[str, Any]], key: str) -> tuple[float, float, float] | None:
    """3 回の（最小，中央値，最大）．値が無い回があれば None．"""
    values = [r[key] for r in runs if r is not None and r[key] is not None]
    if len(values) != len(REPEATS):
        return None
    return min(values), float(median(values)), max(values)


def disjoint_above(a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
    """a の 3 回の幅が b の幅より上にある（重ならない）．"""
    return a[0] > b[2]


def saturation(throughputs: dict[int, float]) -> str:
    """処理量が前の段より SATURATION_GAIN 倍以上に増えなくなる最小の parallel（無ければ「30 より上」）．"""
    ps = [p for p in PARALLELS if p in throughputs]
    for prev, p in zip(ps, ps[1:], strict=False):
        if throughputs[p] < SATURATION_GAIN * throughputs[prev]:
            return str(p)
    return "30 より上"


def main() -> None:
    """全条件を集計し，表と H-L1〜H-L3 の判定を出す．"""
    table: dict[tuple[str, int], list[dict[str, Any] | None]] = {}
    print(
        "routing | parallel | 回 | 失敗率 | e2e p50 | e2e p95 | probe p50 | probe p95 | 処理量(問/秒) | 受信デバイス/問 | 誤りの種類"
    )
    for routing in ROUTINGS:
        for p in PARALLELS:
            runs = [summarize(run_dir(routing, p, k)) for k in REPEATS]
            table[(routing, p)] = runs
            for k, r in zip(REPEATS, runs, strict=True):
                if r is None:
                    print(f"{routing} | {p} | {k} | (実行なし)")
                    continue
                print(
                    f"{routing} | {p} | {k} | {r['failure_rate']:.3f} | {r['e2e_p50']} | {r['e2e_p95']} | "
                    f"{r['probe_p50']} | {r['probe_p95']} | {r['throughput']:.2f} | {r['query_recipients']} | {r['errors']}"
                )
    print()
    f30, m30 = (spread([r for r in table[(x, 30)] if r], "e2e_p95") for x in ROUTINGS)
    print(f"H-L1 (p=30 の e2e p95．flood_score {f30}，multi_centroid {m30})：", end="")
    print(
        "判定不能"
        if f30 is None or m30 is None
        else ("支持" if disjoint_above(f30, m30) else "支持されない")
    )
    ratios: dict[str, float | None] = {}
    for x in ROUTINGS:
        lo, hi = (
            spread([r for r in table[(x, 1)] if r], "e2e_p50"),
            spread([r for r in table[(x, 30)] if r], "e2e_p50"),
        )
        ratios[x] = hi[1] / lo[1] if lo and hi else None
    print(f"H-L2 (e2e p50 の p=1 → 30 の倍率．{ratios})：", end="")
    a, b = ratios[ROUTINGS[0]], ratios[ROUTINGS[1]]
    print("判定不能" if a is None or b is None else ("支持" if a > b else "支持されない"))
    sat = {}
    for x in ROUTINGS:
        thr = {}
        for p in PARALLELS:
            s = spread([r for r in table[(x, p)] if r], "throughput")
            if s:
                thr[p] = s[1]
        sat[x] = (saturation(thr), thr)
        print(f"  {x} の処理量（3 回の中央値）: { {p: round(v, 2) for p, v in thr.items()} }")
    fa, ma = sat[ROUTINGS[0]][0], sat[ROUTINGS[1]][0]
    value = {"30 より上": 99}
    print(f"H-L3 (処理量が頭打ちになる parallel．flood_score {fa}，multi_centroid {ma})：", end="")
    print("支持" if value.get(fa, int(fa)) < value.get(ma, int(ma)) else "支持されない")


if __name__ == "__main__":
    main()
