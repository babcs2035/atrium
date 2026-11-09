"""d0009 B2（Iteration 37）の事後の記述：区間の中の完了の数で数えた X(G) が，閉じた系の同期した完了の塊でどれだけ偏るかを出す．

判定（verdict.json の H_B2a・H_B2b）には使わない．phase 3 の verdict_revised の根拠の数値だけを書く．
使い方（リポジトリの直下．GPU・実機は使わない）:
    uv run python scripts/d0009/b2_window_bias.py   # → results/d0009_b2_split_scoring/window_bias.json
- 1 枚の GPU（G = 1）は lock で直列なので，同時の問 30 が同じ順に進み，30 問がほぼ同時に終わる塊になる．
  測る区間 120 秒に塊がいくつ入るかで X(1) が決まるので，塊の間隔から長い目の処理量 X_period(1) = 30 / 間隔 を出す．
- 比べる値として，区間に終わった採点の要求の数 ÷ 3（1 問 = 3 要求）の X_req も出す（候補ごとの長さの違いで偏りうる）．
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

RESULTS = Path("results")
BASE = "d0009_b2_split_scoring"
GRID = (1, 2, 4, 8)
ROUNDS = (1, 2, 3)
REQUESTS_PER_QUESTION = 3
CONCURRENCY = 30
BIN_S = 10.0
# 同じ塊とみなす完了の間隔の上限（秒）．G = 1 の塊の中の間隔は 1 秒未満，塊の間は約 40 秒．
BURST_GAP_S = 8.0
LAST_CANDIDATE = REQUESTS_PER_QUESTION - 1


class Burst(TypedDict):
    """同時に終わった問の塊．"""

    center_s: float
    n: int


class RunSummary(TypedDict):
    """1 つの実行の処理量の 3 つの数え方と，完了の時間の分布．"""

    valid: bool
    x_count_qps: float
    x_req_qps: float
    x_period_qps: float | None
    completions_per_10s: list[int]
    empty_bin_fraction: float
    bursts: list[Burst]


def burst_centers(done_times: list[float]) -> list[Burst]:
    """完了の時刻を BURST_GAP_S 以内の塊に分け，塊ごとの平均の時刻と数を返す．"""
    groups: list[list[float]] = []
    for t in done_times:
        if groups and t - groups[-1][-1] < BURST_GAP_S:
            groups[-1].append(t)
        else:
            groups.append([t])
    return [Burst(center_s=sum(g) / len(g), n=len(g)) for g in groups]


def period_throughput(bursts: list[Burst]) -> float | None:
    """CONCURRENCY 問の塊が 2 つ以上あるとき，最初と最後の塊の間隔から長い目の処理量を出す．G ≥ 2 では多くは None．"""
    full = [b for b in bursts if b["n"] == CONCURRENCY]
    if len(full) < 2:
        return None
    return CONCURRENCY * (len(full) - 1) / (full[-1]["center_s"] - full[0]["center_s"])


def summarize_run(run_dir: Path) -> RunSummary | None:
    """実行の score_bench.json・requests.jsonl・valid.json を読み，処理量を数える．score_bench.json が無ければ None（打ち切り）．"""
    bench_path = run_dir / "score_bench.json"
    if not bench_path.exists():
        return None
    bench = json.loads(bench_path.read_text())
    valid = json.loads((run_dir / "valid.json").read_text())
    with (run_dir / "requests.jsonl").open() as fh:
        requests = [json.loads(line) for line in fh]
    ws = float(bench["window_start"])
    we = float(bench["window_end"])
    span = we - ws
    done_times = sorted(
        float(r["done_at"]) - ws
        for r in requests
        if r["candidate"] == LAST_CANDIDATE and r["done_at"] is not None
    )
    n_bins = int(span // BIN_S)
    bins = [0] * n_bins
    for t in done_times:
        if 0.0 <= t < span:
            bins[min(int(t // BIN_S), n_bins - 1)] += 1
    n_req = sum(1 for r in requests if r["done_at"] is not None and ws <= float(r["done_at"]) < we)
    bursts = burst_centers(done_times)
    return RunSummary(
        valid=bool(valid["valid"]),
        x_count_qps=float(bench["throughput_qps"]),
        x_req_qps=n_req / REQUESTS_PER_QUESTION / span,
        x_period_qps=period_throughput(bursts),
        completions_per_10s=bins,
        empty_bin_fraction=sum(1 for b in bins if b == 0) / n_bins,
        bursts=bursts,
    )


def main() -> None:
    """全ての実行を読み，有効な実行の 3 つの数え方の E(G) を window_bias.json へ書く．"""
    runs: dict[str, RunSummary] = {}
    for r in ROUNDS:
        for g in GRID:
            summary = summarize_run(RESULTS / f"{BASE}_r{r}_g{g}")
            if summary is not None:
                runs[f"r{r}_g{g}"] = summary
    efficiency: dict[str, dict[str, float]] = {}
    for r in ROUNDS:
        base = runs.get(f"r{r}_g1")
        if base is None or base["x_period_qps"] is None:
            continue
        for g in GRID:
            s = runs.get(f"r{r}_g{g}")
            if s is None or not s["valid"]:
                continue
            efficiency[f"r{r}_g{g}"] = {
                "E_count": s["x_count_qps"] / (g * base["x_count_qps"]),
                "E_req": s["x_req_qps"] / (g * base["x_req_qps"]),
                "E_period": s["x_count_qps"] / (g * base["x_period_qps"]),
            }
    path = RESULTS / BASE / "window_bias.json"
    path.write_text(
        json.dumps({"runs": runs, "efficiency": efficiency}, ensure_ascii=False, indent=1) + "\n"
    )
    for key, e in efficiency.items():
        print(key, {k: round(v, 3) for k, v in e.items()})


if __name__ == "__main__":
    main()
