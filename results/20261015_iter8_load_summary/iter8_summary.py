"""Iteration 8：質問者のプロセスの数（requester_procs）を変えた負荷試験の集計（事前登録の指標・正しさの確認 G1〜G5・判定規則の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261015_iter8_load_summary/iter8_summary.py > results/20261015_iter8_load_summary/iter8_summary.out

指標の定義は iter6_summary.py の summarize・spread・rel を再利用する（処理量 = 成功した問の数 ÷ run_meta.json の finished_at − started_at．範囲が重ならないときだけ「差がある」）．
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

sys.path.insert(0, str(Path("results/20261015_iter6_load_summary")))
from iter6_summary import ACCESS_LINE, fmt, rel, spread, summarize, thr_p95  # noqa: E402

RESULTS = Path("results")
PREFIX = "20261015_iter8"
REPEATS = (1, 2, 3)
PROCS = (1, 2, 4, 8)
METHODS = {"fs": ("flood_score", None), "dp24": ("device_probe", 24), "dp12": ("device_probe", 12)}
TEST_HIT2 = {"fs": 0.8041, "dp24": 0.7837, "dp12": 0.6830}
HIT_TOL = 5e-4
FAIL_LIMIT = 0.01
ENV_THR = (11.127, 14.247)
ENV_P95 = (3.053, 4.067)
P1_RATIO = 1.5
PLATEAU_RATIO = 1.2
REQ_CPU_SAT = 0.90
EXPERT_CPU_SAT = 0.90
CONTROL_CPU_SAT = 0.50
N_EXPERTS = 30


def load(run: str) -> dict[str, Any] | None:
    """実行の指標に，run_meta.json の CPU 関係の値と，ノードごとの要求の数を足したもの．"""
    s = summarize(RESULTS / run)
    if s is None:
        return None
    meta = json.loads((RESULTS / run / "run_meta.json").read_text(encoding="utf-8"))
    s["meta"] = meta
    per_proc = meta.get("requester_cpu_util_per_proc") or []
    s["req_util_proc_median"] = float(median(per_proc)) if per_proc else None
    s["expert_max"] = meta.get("expert_cpu_util_max")
    s["expert_median"] = meta.get("expert_cpu_util_median")
    s["control_busy"] = meta.get("control_cpu_busy")
    s["control_softirq"] = meta.get("control_cpu_softirq")
    s["expert_n"] = len(meta.get("expert_cpu_util") or {})
    counts = [sum(1 for ln in p.read_text(errors="replace").splitlines() if ACCESS_LINE.search(ln)) for p in sorted((RESULTS / run / "logs").glob("*.log"))]
    elapsed = s["elapsed_s"]
    s["node_req_mean_per_s"] = float(np.mean(counts)) / elapsed if counts else None
    s["node_req_max_per_s"] = max(counts) / elapsed if counts else None
    s["n_log_files"] = len(counts)
    return s


def classify(lst: list[dict[str, Any] | None]) -> str:
    """律速の分類（事前登録の順）．3 回（または 1 回）の中央値で判定する．欠けがあれば測定欠け．"""
    runs = [s for s in lst if s is not None]
    if not runs or any(s["req_util_proc_median"] is None or s["expert_max"] is None or s["control_busy"] is None or s["expert_n"] != N_EXPERTS for s in runs):
        return "測定欠け"
    ru = float(median(s["req_util_proc_median"] for s in runs))
    em = float(median(s["expert_max"] for s in runs))
    cb = float(median(s["control_busy"] for s in runs))
    if ru >= REQ_CPU_SAT:
        return f"質問者のプロセスが律速（per_proc 中央値 {ru:.3f}）"
    if em >= EXPERT_CPU_SAT:
        return f"専門家の側が律速の候補（expert_cpu_util_max {em:.3f}）"
    if cb >= CONTROL_CPU_SAT:
        return f"制御点の CPU が律速の候補（control_cpu_busy {cb:.3f}）"
    return f"未特定（per_proc {ru:.3f}，expert_max {em:.3f}，control_busy {cb:.3f}）"


def main() -> None:
    """全実行の表，G1〜G5，環境の揺れ，H-P1・H-P2・頭打ちの N・律速の分類・結論，記録の指標を出す．"""
    names: list[str] = [f"{PREFIX}_anchor_fs_n1_p30_pre"]
    for m in METHODS:
        for r in REPEATS:
            names += [f"{PREFIX}_{m}_n{n}_p30_r{r}" for n in PROCS]
    names += [f"{PREFIX}_fs_n8_p240_r{r}" for r in REPEATS] + [f"{PREFIX}_dp24_n8_p240_r1", f"{PREFIX}_dp12_n8_p240_r1", f"{PREFIX}_anchor_fs_n1_p30_post"]
    runs: dict[str, dict[str, Any] | None] = {}
    print("run | 失敗率 | e2e p50 | e2e p95 | probe p50 | probe p95 | retrieve p50 | route p50 | 処理量 | req_util | req_util/proc 中央値 | expert_max | expert_median | ctl_busy | ctl_softirq | 再試行 | 新接続 | 要求/問 | HTTP要求/秒 | ノード要求/秒 平均 | 最大 | node_s_max p95 | hit@2 | 誤り")
    for n in names:
        s = load(n)
        runs[n] = s
        if s is None:
            print(f"{n} | (実行なし)")
            continue
        print(
            f"{n.removeprefix(PREFIX + '_')} | {s['failure_rate']:.4f} | {fmt(s['e2e_p50'], 4)} | {fmt(s['e2e_p95'], 4)} | {fmt(s['probe_p50'], 4)} | {fmt(s['probe_p95'], 4)} | "
            f"{fmt(s['retrieve_p50'], 4)} | {fmt(s['route_p50'], 5)} | {s['throughput']:.3f} | {fmt(s['cpu_util'])} | {fmt(s['req_util_proc_median'])} | {fmt(s['expert_max'])} | {fmt(s['expert_median'])} | "
            f"{fmt(s['control_busy'])} | {fmt(s['control_softirq'], 4)} | {s['retries']} | {s['new_connections']} | {fmt(s['requests_per_question'], 1)} | {fmt(s['http_requests_per_s'], 1)} | "
            f"{fmt(s['node_req_mean_per_s'], 1)} | {fmt(s['node_req_max_per_s'], 1)} | {fmt(s['node_s_max_p95'], 4)} | {fmt(s['hit2'], 4)} | {s['errors']}"
        )

    print("\n## 正しさの確認（満たさない実行は判定に使わない）")
    bad: list[str] = []
    for n, s in runs.items():
        short = n.removeprefix(PREFIX + "_")
        if s is None:
            bad.append(short)
            print(f"欠け: {short}")
            continue
        parts = short.split("_")
        if parts[0] == "anchor":
            continue
        m, nproc, par = parts[0], int(parts[1][1:]), int(parts[2][1:])
        routing, d = METHODS[m]
        meta = s["meta"]
        cfg = meta["config"]
        ppp = meta.get("requester_parallel_per_proc")
        g1 = s["failure_rate"] <= FAIL_LIMIT
        g3 = (
            meta.get("requester_procs") == nproc
            and isinstance(ppp, list) and len(ppp) == nproc and sum(ppp) == par
            and meta.get("http_pool") == "per_node" and meta["routing"] == routing
            and meta["n_questions"] == 2940
            and (d is None or cfg["routing"]["probe_d"]["enronqa"] == d)
        )
        g5 = s["expert_n"] == N_EXPERTS and s["control_busy"] is not None
        if not (g1 and g3):
            bad.append(short)
        print(f"{short}: G1 {g1}（失敗率 {s['failure_rate']:.4f}），G3 {g3}，G5 {g5}")

    print("\n## G2：同じ方式の 2,940 問の全実行で selected_sources が問ごとに一致するか（失敗の無い問のみ）")
    g2: dict[str, bool] = {}
    for m in METHODS:
        group = [(n, s) for n, s in runs.items() if s is not None and f"_{m}_n" in n]
        ref_name, ref = group[0]
        rates = []
        for n, s in group[1:]:
            common = set(s["selection"]) & set(ref["selection"])
            rates.append(float(np.mean([s["selection"][q] == ref["selection"][q] for q in common])) if common else float("nan"))
        g2[m] = all(r == 1.0 for r in rates)
        print(f"{m}: 実行 {len(group)} 件，基準 {ref_name.removeprefix(PREFIX + '_')}，最小の一致率 {min(rates):.4f} → {g2[m]}")

    print("\n## G4（記録）：2,940 問の hit@2 と device-probe-eval の test の値")
    for m, ref_hit in TEST_HIT2.items():
        hits = sorted({round(s["hit2"], 4) for n, s in runs.items() if s is not None and f"_{m}_n" in n})
        ok = all(abs(h - ref_hit) <= HIT_TOL for h in hits)
        print(f"{m}: 実測の値 {hits}，基準 {ref_hit} → 5e-4 以内 {ok}")

    print("\n## 環境の揺れ（前後の flood_score × N = 1 × 300 問 の p30）")
    for when in ("pre", "post"):
        a = runs[f"{PREFIX}_anchor_fs_n1_p30_{when}"]
        if a is None:
            continue
        in_t = ENV_THR[0] <= a["throughput"] <= ENV_THR[1]
        in_p = ENV_P95[0] <= a["e2e_p95"] <= ENV_P95[1]
        print(f"{when}: 処理量 {a['throughput']:.3f} {ENV_THR} {'内' if in_t else '外'}，e2e p95 {a['e2e_p95']:.3f} {ENV_P95} {'内' if in_p else '外'}，cpu_util {fmt(a['cpu_util'])}")

    print("\n## 判定の規則（主の系列．parallel 30．3 回の（最小，中央値，最大））")
    spreads: dict[tuple[str, int], tuple[Any, Any]] = {}
    for m in METHODS:
        for nproc in PROCS:
            spreads[(m, nproc)] = thr_p95([runs[f"{PREFIX}_{m}_n{nproc}_p30_r{r}"] for r in REPEATS])
        for nproc in PROCS:
            thr, p95 = spreads[(m, nproc)]
            eff = thr[1] / (nproc * spreads[(m, 1)][0][1]) if thr and spreads[(m, 1)][0] else float("nan")
            print(f"{m} N={nproc}: 処理量 {thr}，e2e p95 {p95}，E(N) = {eff:.3f}")
    hp: dict[str, tuple[bool, bool, float]] = {}
    for m in METHODS:
        t1, t2 = spreads[(m, 1)][0], spreads[(m, 2)][0]
        above = rel(t2, t1) == "上"
        ratio = t2[1] / t1[1] if t1 and t2 else float("nan")
        hp[m] = (above and ratio >= P1_RATIO, above, ratio)
        print(f"H-P1/H-P2 {m}: N=2 の範囲は N=1 より {rel(t2, t1)}，中央値の比 {ratio:.3f}（{P1_RATIO} 以上）→ 成り立つ {hp[m][0]}")
    plateau: dict[str, str] = {}
    for m in METHODS:
        plateau[m] = "N = 8 まで頭打ちなし"
        for nproc in (2, 4, 8):
            cur, prev = spreads[(m, nproc)][0], spreads[(m, nproc // 2)][0]
            ratio = cur[1] / prev[1]
            if not (rel(cur, prev) == "上" and ratio >= PLATEAU_RATIO):
                plateau[m] = f"頭打ちの N = {nproc}（N={nproc} の範囲は N={nproc // 2} より {rel(cur, prev)}，比 {ratio:.3f}）"
                break
        print(f"頭打ちの N {m}: {plateau[m]}")

    print("\n## 律速の分類（N = 8）")
    for m in METHODS:
        print(f"{m} 主（p30，3 回）: {classify([runs[f'{PREFIX}_{m}_n8_p30_r{r}'] for r in REPEATS])}")
        p240 = [runs[f"{PREFIX}_{m}_n8_p240_r{r}"] for r in (REPEATS if m == 'fs' else (1,))]
        print(f"{m} 記録（p240，{len(p240)} 回）: {classify(p240)}")

    n_bad_g = len(bad) + sum(not v for v in g2.values())
    if hp["fs"][0]:
        v = "採用"
    elif hp["fs"][1] or hp["dp24"][0] or hp["dp12"][0]:
        v = "部分的に支持"
    elif all(rel(spreads[(m, 2)][0], spreads[(m, 1)][0]) != "上" for m in METHODS):
        v = "棄却"
    else:
        v = "部分的に支持"
    print(f"\nこのイテレーションの結論（規則の機械的な適用．G1・G3・欠け・G2 の不一致 {n_bad_g} 件が 0 のときだけ有効）: {v}")
    if v == "採用":
        for m in METHODS:
            best = max(PROCS, key=lambda nn: spreads[(m, nn)][0][1])
            print(f"  {m}: 処理量の中央値が最大の N = {best}（{spreads[(m, best)][0][1]:.3f} 問/秒）．{plateau[m]}")

    print("\n## 記録（判定に使わない）")
    for m in METHODS:
        for nproc, par, rs in ((8, 30, REPEATS), (8, 240, REPEATS if m == "fs" else (1,))):
            lst = [runs[f"{PREFIX}_{m}_n{nproc}_p{par}_r{r}"] for r in rs]
            thr = [s["throughput"] for s in lst if s]
            e2e = [s["e2e_p95"] for s in lst if s]
            req = [s["http_requests_per_s"] for s in lst if s]
            nm = [s["node_req_mean_per_s"] for s in lst if s]
            nx = [s["node_req_max_per_s"] for s in lst if s]
            em = [s["expert_max"] for s in lst if s]
            cap = float(median(nm)) / float(median(em)) if em and float(median(em)) > 0 else float("nan")
            print(
                f"{m} N={nproc} p{par}: 処理量 {thr}，e2e p95 {[round(x, 3) for x in e2e]}，HTTP 要求/秒 {[round(x, 1) for x in req]}，"
                f"ノード要求/秒（平均の中央値 {float(median(nm)):.1f}，最大の中央値 {float(median(nx)):.1f}），expert_cpu_util_max 中央値 {float(median(em)):.3f}，"
                f"専門家 1 台の処理能力の見積もり（平均要求/秒 ÷ expert_max，ノードが 1 コアで飽和すると仮定した推測）{cap:.0f} 要求/秒"
            )
    for nproc in PROCS:
        f, a, b = (spreads[(m, nproc)][0][1] for m in METHODS)
        print(f"N={nproc}: 処理量の比 dp24/fs {a / f:.3f}，dp12/fs {b / f:.3f}（要求/問の比は表の「要求/問」を参照）")


if __name__ == "__main__":
    main()
