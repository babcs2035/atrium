"""Ollama による LLM 生成試験の結果から全組の性能比と同時信頼区間を集計する．

計画書 §5 の主指標「LLM の入力処理・生成」について，ホスト・条件（短／長入力・同時要求 1/4）
ごとに中央値の生成速度を集め，ラウンドを反復単位とした対数性能比の Bonferroni 補正同時 95%
信頼区間で全組を判定する．出力 SHA-256 のホスト間一致も確認し，モデル・精度・量子化の差を検出する．

使い方:
    uv run --no-sync python scripts/gpu_equivalence/analyze_llm.py \
        --run-id 20261111_gpu_equivalence --rounds 10 \
        --out results/20261111_gpu_equivalence/llm
"""

from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
from itertools import combinations
from pathlib import Path
from typing import TypedDict

from analyze_benchmarks import HOSTS, MAIN_TOLERANCE, REMOTE_BASE, SSH, pair_stats, simultaneous_ci

CONDITIONS: list[tuple[str, int]] = [("short", 1), ("short", 4), ("long", 1), ("long", 4)]


class LlmPair(TypedDict):
    """ホスト 1 組の LLM 生成速度比と判定．"""

    host_a: str
    host_b: str
    mean_ratio: float
    ci: list[float]
    n_rounds: int
    verdict: str


class ConditionAnalysis(TypedDict):
    """単一条件の全組の判定結果と出力ハッシュの一致状況．"""

    pairs: list[LlmPair]
    cond_verdict: str
    output_hashes_consistent: bool
    distinct_output_hashes: int
COLLECT_SNIPPET = """
import json
host = {host!r}
run_id = {run_id!r}
rounds = {rounds}
conds = {conds!r}
base = "{base}/runs/{{run_id}}".format(run_id=run_id)
for r in range(1, rounds + 1):
    for kind, conc in conds:
        p = f"{{base}}/round_{{r}}/{{host}}/llm/llm_{{kind}}_c{{conc}}.jsonl"
        try:
            lines = [json.loads(l) for l in open(p) if l.strip()]
        except FileNotFoundError:
            continue
        summary = [l for l in lines if l.get("kind") == "summary"]
        if not summary:
            continue
        s = summary[-1]
        print(json.dumps({{"round": r, "kind": kind, "conc": conc,
                           "gen": s.get("gen_tokens_per_s_median"),
                           "prompt": s.get("prompt_tokens_per_s_median"),
                           "total_p95": s.get("total_time_p95"),
                           "hash": s.get("dominant_output_sha256"),
                           "n_ok": s.get("n_ok"), "aborted": s.get("aborted", False)}}))
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


def collect_llm(
    run_id: str, rounds: int
) -> tuple[dict[str, dict[tuple[str, int], list[dict[str, object]]]], dict[str, bool]]:
    """ホスト・条件ごとにラウンド順の集約（生成速度・p95・出力ハッシュ）と中止マーカーを集める．"""
    out: dict[str, dict[tuple[str, int], list[dict[str, object]]]] = {}
    aborted: dict[str, bool] = {}
    for host in HOSTS:
        snippet = COLLECT_SNIPPET.format(
            host=host, run_id=run_id, rounds=rounds, conds=CONDITIONS, base=REMOTE_BASE
        )
        command = f"cd {shlex.quote(REMOTE_BASE)} && python3 -c {shlex.quote(snippet)}"
        try:
            result = subprocess.run([*SSH, host, command], capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            out[host] = {}
            aborted[host] = False
            continue
        per_cond: dict[tuple[str, int], list[dict[str, object]]] = {c: [] for c in CONDITIONS}
        host_aborted = False
        for line in result.stdout.splitlines():
            if not line.strip().startswith("{"):
                continue
            rec = json.loads(line)
            if rec.get("__abort__"):
                host_aborted = bool(rec.get("aborted"))
                continue
            key = (rec["kind"], int(rec["conc"]))
            per_cond[key].append(rec)
        out[host] = per_cond
        aborted[host] = host_aborted
    return out, aborted


def median_by_round(records: list[dict[str, object]], field: str) -> list[float | None]:
    """ラウンドごとに中央値を取り，欠測・中止は None とする．"""
    values: list[float | None] = []
    for rec in records:
        value = rec.get(field)
        if rec.get("aborted") or not isinstance(value, (int, float)) or isinstance(value, bool):
            values.append(None)
        else:
            values.append(float(value))
    return values


def analyze_condition(
    hosts: list[str], per_host: dict[str, dict[tuple[str, int], list[dict[str, object]]]], cond: tuple[str, int], n_pairs: int
) -> ConditionAnalysis:
    """単一条件について全組の比と判定，および出力ハッシュの一致を検証する．"""
    series = {h: median_by_round(per_host.get(h, {}).get(cond, []), "gen") for h in hosts}
    pairs: list[LlmPair] = []
    for hi, hj in combinations(hosts, 2):
        st = pair_stats(series[hi], series[hj])
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
    # 出力ハッシュのホスト間一致（同一条件で全ホストが同じ出力か）
    hashes: dict[str, str | None] = {}
    for h in hosts:
        recs = per_host.get(h, {}).get(cond, [])
        dominant = [str(r["hash"]) for r in recs if r.get("hash")]
        hashes[h] = dominant[0] if dominant else None
    distinct_hashes = {v for v in hashes.values() if v is not None}
    if not pairs:
        cond_verdict = "no_data"
    else:
        verdicts = [p["verdict"] for p in pairs]
        if all(v == "equivalent" for v in verdicts):
            cond_verdict = "equivalent"
        elif all(v in ("equivalent", "undetermined") for v in verdicts):
            cond_verdict = "undetermined"
        else:
            cond_verdict = "performance_difference"
    return {
        "pairs": pairs,
        "cond_verdict": cond_verdict,
        "output_hashes_consistent": len(distinct_hashes) <= 1,
        "distinct_output_hashes": len(distinct_hashes),
    }


def main() -> None:
    """全条件を集計し，JSON と markdown の報告を作る．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    print("collecting LLM results...", flush=True)
    data, aborted = collect_llm(args.run_id, args.rounds)
    # 中止マーカーがあるホストは「熱による中止」として扱い，性能比の対象から外す．
    # LLM 実験中に中止したホストは部分データがあっても，結論を全体へ広げない．
    measured_hosts = [
        h
        for h in HOSTS
        if not aborted.get(h, False)
        and any(len(v) > 0 for v in data.get(h, {}).values())
    ]
    aborted_hosts = [h for h in HOSTS if aborted.get(h, False) or h not in measured_hosts]
    n_pairs = len(list(combinations(measured_hosts, 2)))
    results: dict[str, ConditionAnalysis] = {}
    for cond in CONDITIONS:
        key = f"{cond[0]}_c{cond[1]}"
        results[key] = analyze_condition(measured_hosts, data, cond, n_pairs)
    report = {
        "run_id": args.run_id,
        "measured_hosts": measured_hosts,
        "aborted_hosts": aborted_hosts,
        "n_pairs": n_pairs,
        "tolerance": list(MAIN_TOLERANCE),
        "conditions": results,
    }
    (args.out / "llm_analysis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    lines = [
        "# LLM 生成の同等性判定",
        "",
        f"対象: {len(measured_hosts)} 台（{', '.join(measured_hosts)}）",
        f"熱による中止: {len(aborted_hosts)} 台（{', '.join(aborted_hosts) if aborted_hosts else 'なし'}）",
        f"組数: {n_pairs}，許容幅: {MAIN_TOLERANCE[0]}〜{MAIN_TOLERANCE[1]}（Bonferroni 同時 95% CI）",
        "",
    ]
    for cond in CONDITIONS:
        key = f"{cond[0]}_c{cond[1]}"
        res = results[key]
        lines.append(f"## {key}: {res['cond_verdict']}（出力ハッシュ一致: {res['output_hashes_consistent']}）")
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
    (args.out / "llm_analysis.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {args.out / 'llm_analysis.md'}")


if __name__ == "__main__":
    main()
