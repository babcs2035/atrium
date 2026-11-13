"""GPU マイクロベンチマークの結果から全組の性能比と同時信頼区間を集計する．

計画書 §5 の「全 45 組の比が 0.95〜1.05 に収まるか」を，ラウンドごとの対数性能比の
Bonferroni 補正同時 95% 信頼区間で判定する．中止したホストは性能比の対象から外し，
「熱による中止」として個別に報告する（残った台だけの結論を全体へ広げない）．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/analyze_benchmarks.py \
        --run-id 20261111_gpu_equivalence --rounds 10 \
        --metrics fp32 fp16 vram_bandwidth h2d d2h vram_check \
        --out results/20261111_gpu_equivalence/benchmarks
"""

from __future__ import annotations

import argparse
import json
import math
import shlex
import statistics
import subprocess
from itertools import combinations
from pathlib import Path
from typing import TypedDict

from scipy import stats

HOSTS = [f"wafl50{i}" for i in range(10)]
SSH = ["ssh", "-F", "/home/ktakahashi/.ssh/config", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
REMOTE_BASE = "/home/denjo/workspace/ktakahashi/atrium/gpu-equivalence-20261111"
MAIN_TOLERANCE = (0.95, 1.05)


class PairResult(TypedDict):
    """ホスト 1 組の性能比と判定．"""

    host_a: str
    host_b: str
    mean_ratio: float
    ci: list[float]
    n_rounds: int
    verdict: str


class MetricAnalysis(TypedDict):
    """単一指標の全組の判定結果．"""

    pairs: list[PairResult]
    metric_verdict: str
COLLECT_SNIPPET = """
import json
host = {host!r}
run_id = {run_id!r}
rounds = {rounds}
metrics = {metrics!r}
base = "{base}/runs/{{run_id}}".format(run_id=run_id)
for r in range(1, rounds + 1):
    for m in metrics:
        p = f"{{base}}/round_{{r}}/{{host}}/{{m}}/workload.jsonl"
        try:
            lines = [json.loads(l) for l in open(p) if l.strip()]
        except FileNotFoundError:
            continue
        results = [l for l in lines if l.get("kind") == "result"]
        if not results:
            continue
        d = results[-1]
        print(json.dumps({{"round": r, "metric": m, "rate": d.get("rate"),
                           "aborted": d.get("aborted", False), "reason": d.get("reason")}}))
import os
abort_path = f"{{base}}/aborted/{{host}}"
aborted = os.path.exists(abort_path)
reason = ""
if aborted:
    try:
        reason = json.loads(open(abort_path).read()).get("reason", "")
    except Exception:
        reason = ""
print(json.dumps({{"__abort__": True, "aborted": aborted, "reason": reason}}))
"""


def collect_rates(
    run_id: str, rounds: int, metrics: list[str]
) -> tuple[dict[str, dict[str, list[float | None]]], dict[str, bool]]:
    """ホスト・指標ごとにラウンド順のレート（中止・欠測は None）と中止マーカーを集める．"""
    out: dict[str, dict[str, list[float | None]]] = {}
    aborted: dict[str, bool] = {}
    for host in HOSTS:
        snippet = COLLECT_SNIPPET.format(host=host, run_id=run_id, rounds=rounds, metrics=metrics, base=REMOTE_BASE)
        command = f"cd {shlex.quote(REMOTE_BASE)} && python3 -c {shlex.quote(snippet)}"
        try:
            result = subprocess.run([*SSH, host, command], capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            out[host] = {}
            aborted[host] = False
            continue
        per_metric: dict[str, list[float | None]] = {m: [None] * rounds for m in metrics}
        host_aborted = False
        for line in result.stdout.splitlines():
            if not line.strip().startswith("{"):
                continue
            rec = json.loads(line)
            if rec.get("__abort__"):
                host_aborted = bool(rec.get("aborted"))
                continue
            metric = rec["metric"]
            round_no = int(rec["round"])
            rate = rec.get("rate")
            per_metric[metric][round_no - 1] = float(rate) if (rate is not None and not rec.get("aborted")) else None
        out[host] = per_metric
        aborted[host] = host_aborted
    return out, aborted


def pair_stats(rates_i: list[float | None], rates_j: list[float | None]) -> tuple[float, float, int] | None:
    """2 ホストのラウンドごとの対数比から (平均, 標準誤差, サンプル数) を返す．"""
    pairs = [
        (ri, rj)
        for ri, rj in zip(rates_i, rates_j, strict=True)
        if ri is not None and rj is not None and ri > 0 and rj > 0
    ]
    if len(pairs) < 3:
        return None
    log_ratios = [math.log(ri / rj) for ri, rj in pairs]
    mean = statistics.fmean(log_ratios)
    se = statistics.stdev(log_ratios) / (len(pairs) ** 0.5) if len(pairs) > 1 else 0.0
    return mean, se, len(pairs)


def simultaneous_ci(mean: float, se: float, n_pairs: int) -> tuple[float, float]:
    """比較数に応じた Bonferroni 補正の同時 95% 信頼区間を返す．"""
    alpha = 0.05 / max(1, n_pairs)
    z = stats.norm.ppf(1.0 - alpha / 2.0)
    return (mean - z * se, mean + z * se)


def analyze_metric(
    hosts: list[str], per_metric: dict[str, list[float | None]], n_pairs: int
) -> MetricAnalysis:
    """単一指標について全組の比と判定を返す．"""
    pairs: list[PairResult] = []
    for hi, hj in combinations(hosts, 2):
        st = pair_stats(per_metric[hi], per_metric[hj])
        if st is None:
            continue
        mean_log_ratio, se, n_rounds = st
        lo, hi_ci = simultaneous_ci(mean_log_ratio, se, n_pairs)
        ratio_lo, ratio_hi = math.exp(lo), math.exp(hi_ci)
        tol_lo, tol_hi = MAIN_TOLERANCE
        if ratio_lo >= tol_lo and ratio_hi <= tol_hi:
            verdict = "equivalent"
        elif ratio_lo > tol_hi or ratio_hi < tol_lo:
            verdict = "performance_difference"
        else:
            verdict = "undetermined"
        pairs.append(
            {
                "host_a": hi,
                "host_b": hj,
                "mean_ratio": math.exp(mean_log_ratio),
                "ci": [ratio_lo, ratio_hi],
                "n_rounds": n_rounds,
                "verdict": verdict,
            }
        )
    verdicts = [p["verdict"] for p in pairs]
    if not pairs:
        metric_verdict = "no_data"
    elif all(v == "equivalent" for v in verdicts):
        metric_verdict = "equivalent"
    elif all(v in ("equivalent", "undetermined") for v in verdicts) and any(v == "undetermined" for v in verdicts):
        metric_verdict = "undetermined"
    else:
        metric_verdict = "performance_difference"
    return {"pairs": pairs, "metric_verdict": metric_verdict}


def main() -> None:
    """全指標を集計し，JSON と markdown の報告を作る．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--metrics", nargs="+", default=["fp32", "fp16", "vram_bandwidth", "h2d", "d2h", "vram_check"])
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    print("collecting rates...", flush=True)
    rates, aborted = collect_rates(args.run_id, args.rounds, args.metrics)
    # 中止マーカーがあるホストは「熱による中止」として扱い，性能比の対象から外す．
    # 部分データ（中止前のラウンド）があっても，中止ホストの結論を全体へ広げない．
    measured_hosts = [
        h
        for h in HOSTS
        if not aborted.get(h, False)
        and any(any(v is not None for v in rates.get(h, {}).get(m, [])) for m in args.metrics)
    ]
    aborted_hosts = [h for h in HOSTS if aborted.get(h, False) or h not in measured_hosts]
    n_pairs = len(list(combinations(measured_hosts, 2)))
    results: dict[str, MetricAnalysis] = {}
    for metric in args.metrics:
        per_metric = {h: rates.get(h, {}).get(metric, [None] * args.rounds) for h in measured_hosts}
        results[metric] = analyze_metric(measured_hosts, per_metric, n_pairs)
    report = {
        "run_id": args.run_id,
        "measured_hosts": measured_hosts,
        "aborted_hosts": aborted_hosts,
        "n_pairs": n_pairs,
        "tolerance": list(MAIN_TOLERANCE),
        "metrics": results,
    }
    (args.out / "analysis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    lines = [
        "# GPU マイクロベンチマークの同等性判定",
        "",
        f"対象: {len(measured_hosts)} 台（{', '.join(measured_hosts)}）",
        f"熱による中止: {len(aborted_hosts)} 台（{', '.join(aborted_hosts) if aborted_hosts else 'なし'}）",
        f"組数: {n_pairs}，許容幅: {MAIN_TOLERANCE[0]}〜{MAIN_TOLERANCE[1]}（Bonferroni 同時 95% CI）",
        "",
    ]
    for metric in args.metrics:
        res = results[metric]
        lines.append(f"## {metric}: {res['metric_verdict']}")
        lines.append("")
        lines.append("| host_a | host_b | 平均比 | 同時 95% CI | ラウンド数 | 判定 |")
        lines.append("|---|---|---|---|---|---|")
        for p in res["pairs"]:
            ci = p["ci"]
            lines.append(
                f"| {p['host_a']} | {p['host_b']} | {p['mean_ratio']:.3f} | "
                f"[{ci[0]:.3f}, {ci[1]:.3f}] | {p['n_rounds']} | {p['verdict']} |"
            )
        lines.append("")
    (args.out / "analysis.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out / 'analysis.md'}")


if __name__ == "__main__":
    main()
