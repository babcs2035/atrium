"""Iteration 12：sniffio の import の扱い（sniffio_lookup）の負荷試験の集計（事前登録の指標・G1〜G5・判定規則・追加の記録の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261015_iter12_sniffio_summary/iter12_summary.py > results/20261015_iter12_sniffio_summary/iter12_summary.out
    uv run python results/20261015_iter12_sniffio_summary/iter12_summary.py --prefix 20261016_iter12   # 実行日が違うとき

指標の定義は iter6_summary.py の summarize・rel・thr_p95・fmt と iter8_summary.py の load を再利用する（処理量 = 成功した問の数 ÷ run_meta.json の finished_at − started_at．範囲が重ならないときだけ「差がある」）．
TCP の再送の差分は連続実行のログの隣の iter12_chain.log.<run_id>.tcp（iter12_chain.sh が start の直前・直後に取る）から読む．
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

sys.path.insert(0, str(Path("results/20261015_iter6_load_summary")))
sys.path.insert(0, str(Path("results/20261015_iter8_load_summary")))
from iter6_summary import fmt, rel, thr_p95  # noqa: E402
from iter8_summary import load  # noqa: E402

from atrium.analysis import read_results  # noqa: E402

SUMMARY_DIR = Path("results/20261015_iter12_sniffio_summary")
RESULTS = Path("results")
REPEATS = (1, 2, 3)
PROCS = (1, 8)
LOOKUPS = {"sr": "search", "bk": "blocked"}
METHODS = {"fs": ("flood_score", None), "dp12": ("device_probe", 12)}
TEST_HIT2 = {"fs": 0.8041, "dp12": 0.6830}
HIT_TOL = 5e-4
FAIL_LIMIT = 0.01
ENV_THR = (11.127, 14.247)
ENV_P95 = (3.053, 4.067)
RATIO_MIN = 1.10
BASE_TOL = 0.05
N_EXPERTS = 30
# 遅れの問：probe_s がこの秒数以上（Iteration 11 の約 1 秒・約 3 秒の遅れに当たる）．ノードの計算によらない遅れは probe_node_s_max がこの秒数未満
DELAY_PROBE_S = 0.9
NODE_COMPUTE_S = 0.5
# 律速の移りの読み（B33 (c)）
SHIFT_PER_PROC_MAX = 0.9
SHIFT_EXPERT_MIN = 1.58
SHIFT_DELAY_MAX_QUESTIONS = 10
# Iteration 11 の pe の同じ条件の 3 回の処理量の範囲（問/秒）
ITER11_PE_RANGE = {("fs", 1): (17.689, 17.846), ("fs", 8): (124.220, 126.608), ("dp12", 1): (37.752, 42.559), ("dp12", 8): (172.702, 242.558)}
TCP_KEYS = ("TcpRetransSegs", "TcpExtTCPSynRetrans", "TcpExtTCPTimeouts")


def per_request_cpu_ms(s: dict[str, Any]) -> float | None:
    """1 要求あたりの質問者の CPU（ms）= requester_cpu_s ÷ HTTP 要求の数．"""
    cpu = s["meta"].get("requester_cpu_s")
    reqs = s["http_requests_per_s"]
    return None if cpu is None or reqs is None else cpu / (reqs * s["elapsed_s"]) * 1000.0


def delay_counts(run: str) -> tuple[int, int]:
    """（probe_s ≥ 0.9 秒の問の数，そのうち probe_node_s_max < 0.5 秒の問の数）．失敗した問は除く．"""
    rows = [r for r in read_results(RESULTS / run / "results.jsonl") if r["error"] is None]
    delayed = [r for r in rows if r["timings"].get("probe_s", 0.0) >= DELAY_PROBE_S]
    node_free = [r for r in delayed if r.get("probe_node_s_max") is not None and r["probe_node_s_max"] < NODE_COMPUTE_S]
    return len(delayed), len(node_free)


def tcp_delta(log_prefix: str, run: str) -> dict[str, int] | str:
    """start の直前・直後の TCP の再送の計数の差．取れていなければ「取れない」．"""
    path = Path(f"{log_prefix}.{run}.tcp")
    if not path.exists():
        return "取れない（記録なし）"
    snaps: dict[str, dict[str, int]] = {}
    current = ""
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("## "):
            current = line.split()[1]
            snaps[current] = {}
            continue
        m = re.match(r"(\w+)\s+(\d+)\s", line)
        if m and current:
            snaps[current][m.group(1)] = int(m.group(2))
    if "before" not in snaps or "after" not in snaps or any(k not in snaps["after"] or k not in snaps["before"] for k in TCP_KEYS):
        return "取れない（不完全）"
    return {k: snaps["after"][k] - snaps["before"][k] for k in TCP_KEYS}


def main() -> None:
    """全実行の表，G1〜G5，環境の揺れ，H-S1〜H-S3・結論の機械的な出力，記録の指標を出す．"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="20261015_iter12")
    ap.add_argument("--log-prefix", default=str(SUMMARY_DIR / "iter12_chain.log"))
    args = ap.parse_args()
    prefix: str = args.prefix
    names = [f"{prefix}_anchor_fs_n1_pr_pre"]
    for r in REPEATS:
        names += [f"{prefix}_{m}_n{n}_{sl}_r{r}" for m in METHODS for n in PROCS for sl in LOOKUPS]
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

    print("\n## 正しさの確認（G1・G3・G5．満たさない実行は判定に使わず「欠け」）")
    bad: list[str] = []
    g3_bad: list[str] = []
    for n, s in runs.items():
        short = n.removeprefix(prefix + "_")
        if s is None:
            bad.append(short)
            g3_bad.append(short)
            print(f"欠け: {short}")
            continue
        parts = short.split("_")
        meta, cfg = s["meta"], s["meta"]["config"]
        if parts[0] == "anchor":
            m, nproc, sl, pb, nq = "fs", 1, "sr", "per_request", 300
        else:
            m, nproc, sl, pb, nq = parts[0], int(parts[1][1:]), parts[2], "pre_encoded", 2940
        routing, d = METHODS[m]
        ppp = meta.get("requester_parallel_per_proc")
        g1 = s["failure_rate"] <= FAIL_LIMIT
        g3 = (
            meta.get("sniffio_lookup") == LOOKUPS[sl] and meta.get("sniffio_installed") is False
            and meta.get("probe_body") == pb and meta.get("requester_procs") == nproc
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
        print(f"{short}: G1 {g1}（失敗率 {s['failure_rate']:.4f}），G3 {g3}（sniffio_installed {meta.get('sniffio_installed')}），G5 {g5}")

    print("\n## G2：同じ方式の 2,940 問の全実行（sr・bk・N = 1・8）で selected_sources が問ごとに一致するか（失敗の無い問のみ）")
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
        print(f"{m}: 実測の値 {hits}，Iteration 8・11 の値 {ref_hit} → 5e-4 以内 {all(abs(h - ref_hit) <= HIT_TOL for h in hits)}")

    print("\n## 環境の揺れ（前後の flood_score × N = 1 × per_request × search × 300 問）")
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
            for sl in LOOKUPS:
                spreads[(m, n, sl)] = thr_p95([runs[f"{prefix}_{m}_n{n}_{sl}_r{r}"] for r in REPEATS])
    grew: dict[tuple[str, int], bool] = {}
    above: dict[tuple[str, int], bool] = {}
    for m in METHODS:
        for n in PROCS:
            ts, tb = spreads[(m, n, "sr")][0], spreads[(m, n, "bk")][0]
            ratio = tb[1] / ts[1] if ts and tb else float("nan")
            above[(m, n)] = rel(tb, ts) == "上"
            grew[(m, n)] = above[(m, n)] and ratio >= RATIO_MIN
            lo, hi = ITER11_PE_RANGE[(m, n)]
            base_ok = ts is not None and lo * (1 - BASE_TOL) <= ts[1] <= hi * (1 + BASE_TOL)
            print(f"{m} N={n}: sr {ts}，bk {tb}，bk の範囲は sr より {rel(tb, ts)}，中央値の比 {ratio:.3f}（{RATIO_MIN} 以上）→ 伸びた {grew[(m, n)]}；"
                  f"e2e p95 sr {spreads[(m, n, 'sr')][1]} / bk {spreads[(m, n, 'bk')][1]}；sr の中央値は Iteration 11 の pe の範囲 {lo}〜{hi} の ±5% 内 {base_ok}（外なら「環境が変わった」）")
    print(f"H-S1（fs × N = 1）: {grew[('fs', 1)]}．H-S2（dp12 × N = 1）: {grew[('dp12', 1)]}．H-S3（記録）: fs × N = 8 {grew[('fs', 8)]}，dp12 × N = 8 {grew[('dp12', 8)]}")

    # fs × N = 1 の sr・bk の有効な実行（失敗率が上限以下）の数
    valid = {sl: sum(1 for r in REPEATS if (s := runs[f"{prefix}_fs_n1_{sl}_r{r}"]) is not None and s["failure_rate"] <= FAIL_LIMIT) for sl in LOOKUPS}
    if not g2["fs"] or g3_bad or min(valid.values()) < 2:
        v = "棄却（測定不成立）"
    elif grew[("fs", 1)]:
        v = "採用"
    elif above[("fs", 1)] or grew[("dp12", 1)]:
        v = "部分的に支持"
    else:
        v = "棄却（伸びない）"
    print(f"\nこのイテレーションの結論（規則の機械的な適用．G3 の不成立または欠け {len(g3_bad)} 件 {g3_bad}，fs × N = 1 の有効な実行 {valid}）: {v}")

    print("\n## 記録（判定に使わない）")
    for m in METHODS:
        for n in PROCS:
            for sl in LOOKUPS:
                lst = [s for r in REPEATS if (s := runs[f"{prefix}_{m}_n{n}_{sl}_r{r}"]) is not None]
                cpu = [per_request_cpu_ms(s) for s in lst]
                util = [s["req_util_proc_median"] for s in lst]
                em = [s["expert_max"] for s in lst]
                valid_cpu = [c for c in cpu if c is not None]
                print(f"{m} N={n} {sl}: 1 要求あたりの質問者の CPU(ms) 中央値 {fmt(float(median(valid_cpu)) if valid_cpu else None)}，"
                      f"req_util/proc {[round(u, 3) for u in util if u is not None]}，expert_cpu_util_max {[round(e, 3) for e in em if e is not None]}")
            ts, tb = spreads[(m, n, "sr")][0], spreads[(m, n, "bk")][0]
            if ts and tb:
                print(f"{m} N={n}: 比 bk/sr {tb[1] / ts[1]:.3f}")
            cs = [per_request_cpu_ms(s) for r in REPEATS if (s := runs[f"{prefix}_{m}_n{n}_sr_r{r}"]) is not None]
            cb = [per_request_cpu_ms(s) for r in REPEATS if (s := runs[f"{prefix}_{m}_n{n}_bk_r{r}"]) is not None]
            cs_v, cb_v = [c for c in cs if c is not None], [c for c in cb if c is not None]
            if cs_v and cb_v:
                print(f"{m} N={n}: 1 要求あたりの質問者の CPU の差 sr − bk = {median(cs_v) - median(cb_v):.3f} ms（事前の測定 0.54〜0.63 ms）")
        if all(spreads[(m, k, sl)][0] for k in PROCS for sl in LOOKUPS):
            for sl in LOOKUPS:
                e = spreads[(m, 8, sl)][0][1] / (8 * spreads[(m, 1, sl)][0][1])
                print(f"{m}: E(8) {sl} {e:.3f}")

    print("\n## 追加の記録：遅れの問の数（probe_s ≥ 0.9 秒．括弧内はそのうち probe_node_s_max < 0.5 秒），TCP の再送の差分（start の直前・直後．deploy を含まない）")
    delays: dict[str, int] = {}
    for n in names:
        if runs[n] is None:
            continue
        d_all, d_free = delay_counts(n)
        delays[n] = d_all
        print(f"{n.removeprefix(prefix + '_')}: 遅れの問 {d_all}（ノードの計算によらない {d_free}），TCP {tcp_delta(args.log_prefix, n)}")

    print("\n## 追加の記録：N = 8 の遅れの無い実行（遅れの問の数が 0）だけの処理量の中央値（参考）")
    for m in METHODS:
        for sl in LOOKUPS:
            clean = [runs[f"{prefix}_{m}_n8_{sl}_r{r}"]["throughput"] for r in REPEATS if runs[f"{prefix}_{m}_n8_{sl}_r{r}"] is not None and delays.get(f"{prefix}_{m}_n8_{sl}_r{r}") == 0]
            print(f"{m} N=8 {sl}: 遅れの無い実行 {len(clean)} 件，中央値 {fmt(float(median(clean)) if clean else None)}")

    print("\n## 律速の移りの読み（dp12 × N = 8 × bk．per_proc の中央値 < 0.9 かつ最も忙しいノードの expert_cpu_util ≥ 1.58．遅れの問が 10 問以上の実行は使わない）")
    for r in REPEATS:
        name = f"{prefix}_dp12_n8_bk_r{r}"
        s = runs[name]
        if s is None or s["req_util_proc_median"] is None or s["expert_max"] is None:
            print(f"r{r}: 欠け")
            continue
        if delays.get(name, 0) >= SHIFT_DELAY_MAX_QUESTIONS:
            print(f"r{r}: 遅れの問 {delays[name]} 問のため読みに使わない（per_proc {s['req_util_proc_median']:.3f}，expert_max {s['expert_max']:.3f}）")
            continue
        moved = s["req_util_proc_median"] < SHIFT_PER_PROC_MAX and s["expert_max"] >= SHIFT_EXPERT_MIN
        print(f"r{r}: per_proc {s['req_util_proc_median']:.3f}，expert_max {s['expert_max']:.3f} → 律速が専門家ノードへ移った {moved}")


if __name__ == "__main__":
    main()
