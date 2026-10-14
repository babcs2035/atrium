"""Iteration 4 の phase 3：段ごとの時間（timings）を分けて，centroid_probe（K = 75）が flood_score より遅い理由を確かめる．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261014_iter4_load_summary/iter4_timing_breakdown.py > results/20261014_iter4_load_summary/iter4_timing_breakdown.out

出す量（条件ごとに 3 回の問をまとめた中央値．flood_score・multi_centroid は T4 の 3 回）:
- 段ごとの時間 probe_s・route_s・retrieve_s・merge_s と，e2e_s からそれらを引いた残り（埋め込みのスレッドの待ちなど）
- probe の待ち＝probe_s − probe_node_s_max（ノードの計算以外．通信・待ち行列）．retrieve の待ち＝retrieve_s − 本照会のノードの計算の最大
- 質問者のイベントループの上の同期の 1 段目の採点が占める割合＝処理量 × route_s の平均（1 秒あたりにループを塞ぐ秒数）
- リトルの法則の検算：処理量 × e2e_s の平均（並列の数と近ければ，問は常に parallel 個が処理中）
- merge_s は asyncio.to_thread の中の軽い計算（上位 5 件の並べ替え）なので，その大きさはスレッドとイベントループの待ちの目安になる
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from atrium.analysis import read_results

RESULTS = Path("results")
PARALLELS = (1, 4, 8, 16, 30)
REPEATS = (1, 2, 3)
# 条件の名前 → run_id の接頭辞（{p} と {r} を埋める）
CONDITIONS = {
    "flood_score(T4)": "20261014_t4_flood_score_p{p}_r{r}",
    "multi_centroid(T4)": "20261014_t4_multi_centroid_p{p}_r{r}",
    "cp K=75": "20261014_iter4_cp75_p{p}_r{r}",
    "cp K=30": "20261014_iter4_cp30_p{p}_r{r}",
}
PHASES = ("probe_s", "route_s", "retrieve_s", "merge_s")


def median_or_none(values: list[float]) -> float | None:
    """中央値（値が無ければ None）．"""
    return float(np.median(values)) if values else None


def fmt(value: float | None) -> str:
    """表の 1 セル（None は -）．"""
    return "-" if value is None else f"{value:.3f}"


def breakdown(run_ids: list[str]) -> dict[str, float | None]:
    """同じ条件の複数回の実行の，失敗の無い問をまとめた段ごとの時間の中央値と負荷の量．"""
    rows: list[dict] = []
    throughputs: list[float] = []
    for run_id in run_ids:
        path = RESULTS / run_id
        recs = read_results(path / "results.jsonl")
        meta = json.loads((path / "run_meta.json").read_text(encoding="utf-8"))
        ok = [r for r in recs if r["error"] is None]
        rows.extend(ok)
        throughputs.append(len(ok) / (meta["finished_at"] - meta["started_at"]))
    t = [r["timings"] for r in rows]
    out: dict[str, float | None] = {}
    for phase in PHASES:
        out[phase] = median_or_none([x[phase] for x in t if phase in x])
    out["rest_s"] = median_or_none([x["e2e_s"] - sum(x.get(p, 0.0) for p in PHASES) for x in t])
    out["e2e_s"] = median_or_none([x["e2e_s"] for x in t])
    out["probe_wait_s"] = median_or_none(
        [r["timings"]["probe_s"] - r["probe_node_s_max"] for r in rows if r.get("probe_node_s_max") is not None]
    )
    out["retrieve_wait_s"] = median_or_none(
        [
            r["timings"]["retrieve_s"] - max(s["node_s"] for s in r["shard_stats"])
            for r in rows
            if r.get("shard_stats")
        ]
    )
    thr = float(np.median(throughputs))
    out["throughput"] = thr
    out["loop_busy_route"] = thr * float(np.mean([x.get("route_s", 0.0) for x in t]))
    out["little_L"] = thr * float(np.mean([x["e2e_s"] for x in t]))
    out["probe_reqs_per_s"] = thr * float(np.mean([r.get("probe_recipients", 0) for r in rows]))
    return out


def main() -> None:
    """条件 × parallel の段ごとの時間の表を出す．"""
    cols = (*PHASES, "rest_s", "e2e_s", "probe_wait_s", "retrieve_wait_s", "throughput", "loop_busy_route", "little_L", "probe_reqs_per_s")
    print("条件 | parallel | " + " | ".join(cols))
    for name, pattern in CONDITIONS.items():
        for p in PARALLELS:
            b = breakdown([pattern.format(p=p, r=r) for r in REPEATS])
            print(f"{name} | {p} | " + " | ".join(fmt(b[c]) for c in cols))


if __name__ == "__main__":
    main()
