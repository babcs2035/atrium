"""持続負荷の結果から熱による減速を検出する．

計画書 §5 の持続性能基準「末尾 10 分の速度／最初の安定した 5 分の速度が 0.95 以上」を検証する．
各ホスト・各 rep の 1 分ごとの速度と温度を集計し，開始 5 分と末尾 10 分の速度比，温度のピークを
計算する．比が 0.95 未満，または熱減速・80 °C 到達なら減速ありと判定する．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/analyze_sustained.py \
        --run-id 20261111_gpu_equivalence --reps 3 \
        --out results/20261111_gpu_equivalence/sustained
"""

from __future__ import annotations

import argparse
import json
import shlex
import statistics
import subprocess
from pathlib import Path

from analyze_benchmarks import HOSTS, REMOTE_BASE, SSH

SUSTAINED_MIN = 30
START_MINUTES = 5
END_MINUTES = 10
SUSTAINED_MIN_RATIO = 0.95
COLLECT_SNIPPET = """
import json, csv, statistics
host = {host!r}
run_id = {run_id!r}
kind = {kind!r}
reps = {reps}
base = "{base}/runs/{{run_id}}".format(run_id=run_id)
for rep in range(1, reps + 1):
    d = f"{{base}}/sustained/{{host}}/{{kind}}_r{{rep}}"
    # 速度の 1 分ごとの中央値
    rates_by_min = {{}}
    if kind == "generation":
        p = d + "/generation.jsonl"
        try:
            lines = [json.loads(l) for l in open(p) if l.strip()]
        except FileNotFoundError:
            lines = []
        recs = [l for l in lines if "gen_tokens_per_s" in l and "completed_at" in l]
        if recs:
            t0 = min(l["completed_at"] for l in recs)
            for l in recs:
                m = int((l["completed_at"] - t0) // 60)
                rates_by_min.setdefault(m, []).append(l["gen_tokens_per_s"])
    else:
        p = d + "/workload.jsonl"
        try:
            lines = [json.loads(l) for l in open(p) if l.strip()]
        except FileNotFoundError:
            lines = []
        recs = [l for l in lines if l.get("kind") == "sample" and "rate" in l]
        for l in recs:
            m = int(l.get("elapsed_s", 0)) // 60
            rates_by_min.setdefault(m, []).append(l["rate"])
    minute_rates = [statistics.median(v) for _, v in sorted(rates_by_min.items()) if v]
    # 温度のピーク（monitor.csv）
    max_temp = None
    try:
        with open(d + "/monitor.csv") as f:
            temps = [float(row["temp_c"]) for row in csv.DictReader(f) if row.get("temp_c")]
        max_temp = max(temps) if temps else None
    except (FileNotFoundError, ValueError):
        max_temp = None
    aborted = None
    try:
        aborted = json.loads(open(d + "/status.json").read()).get("aborted")
    except FileNotFoundError:
        aborted = None
    print(json.dumps({{"kind": kind, "rep": rep, "minute_rates": minute_rates,
                       "max_temp_c": max_temp, "aborted": aborted}}))
import os
abort_path = f"{{base}}/aborted/{{host}}"
print(json.dumps({{"__abort__": True, "aborted": os.path.exists(abort_path)}}))
"""


def _ratio_from_minute_rates(minute_rates: list[float]) -> float | None:
    """1 分ごとの速度から末尾 10 分／開始 5 分の比を返す（データ不足は None）．"""
    if len(minute_rates) < START_MINUTES + END_MINUTES:
        return None
    start = statistics.fmean(minute_rates[:START_MINUTES])
    end = statistics.fmean(minute_rates[-END_MINUTES:])
    if start <= 0:
        return None
    return end / start


def collect_sustained(
    run_id: str, kind: str, reps: int
) -> tuple[dict[str, list[dict[str, object]]], dict[str, bool]]:
    """ホストごとに rep 順の集約（1 分ごとの速度・温度ピーク・中止）と中止マーカーを集める．"""
    out: dict[str, list[dict[str, object]]] = {}
    aborted: dict[str, bool] = {}
    for host in HOSTS:
        snippet = COLLECT_SNIPPET.format(host=host, run_id=run_id, kind=kind, reps=reps, base=REMOTE_BASE)
        command = f"cd {shlex.quote(REMOTE_BASE)} && python3 -c {shlex.quote(snippet)}"
        try:
            result = subprocess.run([*SSH, host, command], capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            out[host] = []
            aborted[host] = False
            continue
        records: list[dict[str, object]] = []
        host_aborted = False
        for line in result.stdout.splitlines():
            if line.strip().startswith("{"):
                rec = json.loads(line)
                if rec.get("__abort__"):
                    host_aborted = bool(rec.get("aborted"))
                    continue
                records.append(rec)
        out[host] = records
        aborted[host] = host_aborted
    return out, aborted


def analyze_kind(run_id: str, kind: str, reps: int) -> dict[str, object]:
    """単一 kind（generation/score）の全ホストの持続性能を判定する．"""
    data, aborted = collect_sustained(run_id, kind, reps)
    hosts: list[dict[str, object]] = []
    for host in HOSTS:
        records = data.get(host, [])
        if aborted.get(host, False):
            continue
        if not records:
            continue
        ratios: list[float | None] = []
        for rec in records:
            minute_rates_raw = rec.get("minute_rates", [])
            if not isinstance(minute_rates_raw, list):
                ratios.append(None)
                continue
            minute_rates = [float(x) for x in minute_rates_raw if isinstance(x, (int, float))]
            ratios.append(_ratio_from_minute_rates(minute_rates))
        valid_ratios = [r for r in ratios if r is not None]
        temps: list[float] = []
        for rec in records:
            temp = rec.get("max_temp_c")
            if isinstance(temp, (int, float)) and not isinstance(temp, bool):
                temps.append(float(temp))
        max_temp = max(temps) if temps else None
        any_aborted = any(rec.get("aborted") for rec in records)
        if not valid_ratios:
            verdict = "no_data"
        elif any_aborted:
            verdict = "aborted"
        elif min(valid_ratios) >= SUSTAINED_MIN_RATIO and (max_temp is None or max_temp < 80):
            verdict = "sustained_ok"
        else:
            verdict = "thermal_throttling"
        hosts.append(
            {
                "host": host,
                "n_reps": len(records),
                "ratios": ratios,
                "min_ratio": min(valid_ratios) if valid_ratios else None,
                "max_temp_c": max_temp,
                "aborted": any_aborted,
                "verdict": verdict,
            }
        )
    return {"kind": kind, "hosts": hosts}


def main() -> None:
    """生成と採点の持続負荷を集計し，報告を作る．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": args.run_id,
        "reps": args.reps,
        "sustained_min_ratio": SUSTAINED_MIN_RATIO,
        "generation": analyze_kind(args.run_id, "generation", args.reps),
        "score": analyze_kind(args.run_id, "score", args.reps),
    }
    (args.out / "sustained_analysis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    lines = ["# 持続負荷の判定", ""]
    for kind in ("generation", "score"):
        res = report[kind]
        lines.append(f"## {kind}（基準: 末尾 10 分／開始 5 分 >= {SUSTAINED_MIN_RATIO}）")
        lines.append("")
        lines.append("| host | rep 数 | 最小比 | 温度ピーク | 中止 | 判定 |")
        lines.append("|---|---|---|---|---|---|")
        for h in res["hosts"]:
            min_ratio = h["min_ratio"]
            max_temp = h["max_temp_c"]
            ratio_str = f"{min_ratio:.3f}" if isinstance(min_ratio, float) else "n/a"
            temp_str = f"{max_temp:.1f}" if isinstance(max_temp, float) else "n/a"
            lines.append(
                f"| {h['host']} | {h['n_reps']} | {ratio_str} | {temp_str} | "
                f"{'yes' if h['aborted'] else 'no'} | {h['verdict']} |"
            )
        lines.append("")
    (args.out / "sustained_analysis.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out / 'sustained_analysis.md'}")


if __name__ == "__main__":
    main()
