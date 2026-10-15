"""Iteration 11：probe の本文の作り方（probe_body）の負荷試験の集計（事前登録の指標・G1〜G5・判定規則の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261015_iter11_probe_body_summary/iter11_summary.py > results/20261015_iter11_probe_body_summary/iter11_summary.out
    uv run python results/20261015_iter11_probe_body_summary/iter11_summary.py --prefix 20261016_iter11   # 実行日が違うとき

指標の定義は iter6_summary.py の summarize・rel・thr_p95・fmt と iter8_summary.py の load を再利用する（処理量 = 成功した問の数 ÷ run_meta.json の finished_at − started_at．範囲が重ならないときだけ「差がある」）．
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

sys.path.insert(0, str(Path("results/20261015_iter6_load_summary")))
sys.path.insert(0, str(Path("results/20261015_iter8_load_summary")))
from iter6_summary import fmt, rel, thr_p95  # noqa: E402
from iter8_summary import load  # noqa: E402

REPEATS = (1, 2, 3)
PROCS = (1, 8)
BODIES = {"pr": "per_request", "pe": "pre_encoded"}
METHODS = {"fs": ("flood_score", None), "dp12": ("device_probe", 12)}
TEST_HIT2 = {"fs": 0.8041, "dp12": 0.6830}
HIT_TOL = 5e-4
FAIL_LIMIT = 0.01
ENV_THR = (11.127, 14.247)
ENV_P95 = (3.053, 4.067)
RATIO_MIN = 1.10
BASE_TOL = 0.05
# Iteration 8 の per_request に当たる条件の 3 回の処理量の範囲（問/秒）
ITER8_RANGE = {("fs", 1): (13.286, 13.574), ("fs", 8): (78.11, 84.95), ("dp12", 1): (32.22, 32.82), ("dp12", 8): (178.3, 186.7)}
N_EXPERTS = 30


def per_request_cpu_ms(s: dict[str, Any]) -> float | None:
    """1 要求あたりの質問者の CPU（ms）= requester_cpu_s ÷ HTTP 要求の数．"""
    cpu = s["meta"].get("requester_cpu_s")
    reqs = s["http_requests_per_s"]
    return None if cpu is None or reqs is None else cpu / (reqs * s["elapsed_s"]) * 1000.0


def main() -> None:
    """全実行の表，G1〜G5，環境の揺れ，H-B1〜H-B3・結論の機械的な出力，記録の指標を出す．"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="20261015_iter11")
    prefix = ap.parse_args().prefix
    names = [f"{prefix}_anchor_fs_n1_pr_pre"]
    for r in REPEATS:
        names += [f"{prefix}_{m}_n{n}_{pb}_r{r}" for m in METHODS for n in PROCS for pb in BODIES]
    names.append(f"{prefix}_anchor_fs_n1_pr_post")
    runs: dict[str, dict[str, Any] | None] = {n: load(n) for n in names}

    print("run | 失敗率 | e2e p50 | e2e p95 | probe p50 | probe p95 | retrieve p50 | 処理量 | req_util/proc 中央値 | 要求あたり質問者CPU(ms) | expert_max | ctl_busy | 再試行 | 新接続 | 要求/問 | HTTP要求/秒 | hit@2 | 誤り")
    for n in names:
        s = runs[n]
        if s is None:
            print(f"{n} | (実行なし)")
            continue
        print(
            f"{n.removeprefix(prefix + '_')} | {s['failure_rate']:.4f} | {fmt(s['e2e_p50'], 4)} | {fmt(s['e2e_p95'], 4)} | {fmt(s['probe_p50'], 4)} | {fmt(s['probe_p95'], 4)} | "
            f"{fmt(s['retrieve_p50'], 4)} | {s['throughput']:.3f} | {fmt(s['req_util_proc_median'])} | {fmt(per_request_cpu_ms(s), 3)} | {fmt(s['expert_max'])} | "
            f"{fmt(s['control_busy'])} | {s['retries']} | {s['new_connections']} | {fmt(s['requests_per_question'], 1)} | {fmt(s['http_requests_per_s'], 1)} | {fmt(s['hit2'], 4)} | {s['errors']}"
        )

    print("\n## 正しさの確認（G1・G3・G5．満たさない実行は判定に使わない）")
    bad: list[str] = []
    g3_bad: list[str] = []
    for n, s in runs.items():
        short = n.removeprefix(prefix + "_")
        if s is None:
            bad.append(short)
            print(f"欠け: {short}")
            continue
        parts = short.split("_")
        meta, cfg = s["meta"], s["meta"]["config"]
        if parts[0] == "anchor":
            m, nproc, pb, nq = "fs", 1, "pr", 300
        else:
            m, nproc, pb, nq = parts[0], int(parts[1][1:]), parts[2], 2940
        routing, d = METHODS[m]
        ppp = meta.get("requester_parallel_per_proc")
        g1 = s["failure_rate"] <= FAIL_LIMIT
        g3 = (
            meta.get("probe_body") == BODIES[pb] and meta.get("requester_procs") == nproc
            and isinstance(ppp, list) and len(ppp) == nproc and sum(ppp) == 30
            and meta.get("http_pool") == "per_node" and meta["routing"] == routing
            and meta["n_questions"] == nq
            and (d is None or cfg["routing"]["probe_d"]["enronqa"] == d)
        )
        g5 = s["expert_n"] == N_EXPERTS and s["control_busy"] is not None
        if not (g1 and g3):
            bad.append(short)
        if not g3:
            g3_bad.append(short)
        print(f"{short}: G1 {g1}（失敗率 {s['failure_rate']:.4f}），G3 {g3}，G5 {g5}")

    print("\n## G2：同じ方式の 2,940 問の全実行（pr・pe・N = 1・8）で selected_sources が問ごとに一致するか（失敗の無い問のみ）")
    g2: dict[str, bool] = {}
    for m in METHODS:
        group = [(n, s) for n, s in runs.items() if s is not None and f"_{m}_n" in n and "_anchor_" not in n]
        if not group:
            g2[m] = False
            print(f"{m}: 実行なし")
            continue
        ref_name, ref = group[0]
        rates = []
        for n, s in group[1:]:
            common = set(s["selection"]) & set(ref["selection"])
            rates.append(float(np.mean([s["selection"][q] == ref["selection"][q] for q in common])) if common else float("nan"))
        g2[m] = bool(rates) and all(r == 1.0 for r in rates)
        print(f"{m}: 実行 {len(group)} 件，基準 {ref_name.removeprefix(prefix + '_')}，最小の一致率 {min(rates, default=float('nan')):.4f} → {g2[m]}")

    print("\n## G4（記録）：2,940 問の hit@2")
    for m, ref_hit in TEST_HIT2.items():
        hits = sorted({round(s["hit2"], 4) for n, s in runs.items() if s is not None and f"_{m}_n" in n and "_anchor_" not in n})
        print(f"{m}: 実測の値 {hits}，Iteration 8 の値 {ref_hit} → 5e-4 以内 {all(abs(h - ref_hit) <= HIT_TOL for h in hits)}")

    print("\n## 環境の揺れ（前後の flood_score × N = 1 × per_request × 300 問）")
    for when in ("pre", "post"):
        a = runs[f"{prefix}_anchor_fs_n1_pr_{when}"]
        if a is None:
            continue
        in_t = ENV_THR[0] <= a["throughput"] <= ENV_THR[1]
        in_p = ENV_P95[0] <= a["e2e_p95"] <= ENV_P95[1]
        print(f"{when}: 処理量 {a['throughput']:.3f} {ENV_THR} {'内' if in_t else '外'}，e2e p95 {a['e2e_p95']:.3f} {ENV_P95} {'内' if in_p else '外'}")

    print("\n## 判定の規則（3 回の（最小，中央値，最大））")
    spreads: dict[tuple[str, int, str], tuple[Any, Any]] = {}
    for m in METHODS:
        for n in PROCS:
            for pb in BODIES:
                spreads[(m, n, pb)] = thr_p95([runs[f"{prefix}_{m}_n{n}_{pb}_r{r}"] for r in REPEATS])
    grew: dict[tuple[str, int], bool] = {}
    above: dict[tuple[str, int], bool] = {}
    for m in METHODS:
        for n in PROCS:
            tr, te = spreads[(m, n, "pr")][0], spreads[(m, n, "pe")][0]
            ratio = te[1] / tr[1] if tr and te else float("nan")
            above[(m, n)] = rel(te, tr) == "上"
            grew[(m, n)] = above[(m, n)] and ratio >= RATIO_MIN
            lo, hi = ITER8_RANGE[(m, n)]
            base_ok = tr is not None and lo * (1 - BASE_TOL) <= tr[1] <= hi * (1 + BASE_TOL)
            print(f"{m} N={n}: pr {tr}，pe {te}，pe の範囲は pr より {rel(te, tr)}，中央値の比 {ratio:.3f}（{RATIO_MIN} 以上）→ 伸びた {grew[(m, n)]}；"
                  f"e2e p95 pr {spreads[(m, n, 'pr')][1]} / pe {spreads[(m, n, 'pe')][1]}；pr の中央値は Iteration 8 の範囲 {lo}〜{hi} の ±5% 内 {base_ok}")
    print(f"H-B1（fs × N = 1）: {grew[('fs', 1)]}．H-B2（dp12 × N = 1）: {grew[('dp12', 1)]}．H-B3（記録）: fs × N = 8 {grew[('fs', 8)]}，dp12 × N = 8 {grew[('dp12', 8)]}")

    # fs × N = 1 の pr・pe の有効な実行（失敗率が上限以下）の数
    valid = {pb: sum(1 for r in REPEATS if (s := runs[f"{prefix}_fs_n1_{pb}_r{r}"]) is not None and s["failure_rate"] <= FAIL_LIMIT) for pb in BODIES}
    n_g3_bad = len(g3_bad) + sum(1 for b in bad if b not in g3_bad and runs[f"{prefix}_{b}"] is None)
    if not g2["fs"] or n_g3_bad or min(valid.values()) < 2:
        v = "棄却（測定不成立）"
    elif grew[("fs", 1)]:
        v = "採用"
    elif above[("fs", 1)] or grew[("dp12", 1)]:
        v = "部分的に支持"
    else:
        v = "棄却（伸びない）"
    print(f"\nこのイテレーションの結論（規則の機械的な適用．G3 または欠けの不一致 {n_g3_bad} 件，fs × N = 1 の有効な実行 {valid}）: {v}")

    print("\n## 記録（判定に使わない）")
    for m in METHODS:
        for n in PROCS:
            for pb in BODIES:
                lst = [s for r in REPEATS if (s := runs[f"{prefix}_{m}_n{n}_{pb}_r{r}"]) is not None]
                cpu = [per_request_cpu_ms(s) for s in lst]
                util = [s["req_util_proc_median"] for s in lst]
                em = [s["expert_max"] for s in lst]
                print(f"{m} N={n} {pb}: 1 要求あたりの質問者の CPU(ms) 中央値 {fmt(float(median(c for c in cpu if c is not None)) if any(c is not None for c in cpu) else None)}，"
                      f"req_util/proc {[round(u, 3) for u in util if u is not None]}，expert_cpu_util_max {[round(e, 3) for e in em if e is not None]}")
            tp = {pb: spreads[(m, n, pb)][0] for pb in BODIES}
            if tp["pr"] and tp["pe"]:
                print(f"{m} N={n}: 比 pe/pr {tp['pe'][1] / tp['pr'][1]:.3f}")
        if spreads[(m, 1, "pr")][0] and spreads[(m, 8, "pr")][0] and spreads[(m, 1, "pe")][0] and spreads[(m, 8, "pe")][0]:
            e_pr = spreads[(m, 8, "pr")][0][1] / (8 * spreads[(m, 1, "pr")][0][1])
            e_pe = spreads[(m, 8, "pe")][0][1] / (8 * spreads[(m, 1, "pe")][0][1])
            print(f"{m}: E(8) pr {e_pr:.3f}，pe {e_pe:.3f}")


if __name__ == "__main__":
    main()
