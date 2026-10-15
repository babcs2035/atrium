"""Iteration 10：専門家ノードのプロセスの数 W（experiment.node_workers）を変えた node-bench の集計（事前登録の正しさの確認 G1〜G5・W の伸びの規則・律速の分類の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で．DATE は実行日 YYYYMMDD）:
    uv run python results/20261015_iter10_node_workers_summary/iter10_summary.py <DATE> > results/20261015_iter10_node_workers_summary/iter10_summary.out

規則は journal の Iteration 10 の事前登録と同じ（3 回の範囲が重ならないときだけ「差がある」）．主の量は実行ごとの頂点の処理量
P（その実行の欠けでない水準の処理量の最大）で，W ごとに 3 個の P を比べる．Iteration 9 と同じ X_sat（水準ごとの中央値の最大）も記録として出す．
環境の揺れの確認（各 deploy の flood_score × N = 1 × 300 問）は iter6_summary.py の summarize で求める．
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path("results/20261015_iter6_load_summary")))
from iter6_summary import summarize  # noqa: E402

RESULTS = Path("results")
REPEATS = (1, 2, 3)
WORKERS = (1, 2, 4)
LEVELS = (1, 2, 4, 8, 16, 32, 64)
TARGETS = {"t13102": "192.168.13.102", "t15103": "192.168.15.103"}
PRIMARY = "t13102"
FAIL_LIMIT = 0.01
GEN_CPU_LIMIT = 0.80
LITTLE_MIN = 0.90
GROW_RATIO = 1.10
# H-W1：W = 1 → 2 で頂点の処理量の中央値が 1.5 倍以上
SCALE_MAIN = 1.5
HOST_SAT = 0.90
# 律速の分類：cgroup の CPU が「W × W = 1 の頂点の cgroup の CPU」の 0.85 倍以上なら，各プロセスが W = 1 と同じ上限で止まっている
PER_PROC_SAT = 0.85
# G6（記録）：W = 1 で cgroup の CPU とプロセスの CPU の比がこの範囲（cgroup の測り方の確認）
CGROUP_PROC_RATIO = (0.9, 1.2)
ENV_THR = (11.127, 14.247)
ENV_P95 = (3.053, 4.067)


def load_bench(run: str) -> dict[str, Any] | None:
    """node_bench.json（無ければ None）．"""
    path = RESULTS / run / "node_bench.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def gates(lv: dict[str, Any], workers: int) -> dict[str, bool]:
    """1 つの水準の G1（失敗率）・G3（負荷生成が律速でない）・G4（CPU が読めた）・G5（ノードが W 個のプロセスで動いている）．"""
    n = lv["n_ok"] + lv["n_errors"]
    return {
        "G1": n > 0 and lv["n_errors"] / n <= FAIL_LIMIT,
        "G3": max(lv["generator_cpu_util_per_proc"]) <= GEN_CPU_LIMIT
        and (lv["little_ratio"] or 0.0) >= LITTLE_MIN,
        "G4": lv.get("node_cgroup_util") is not None and lv["node_host_busy"] is not None,
        "G5": lv.get("node_workers") == workers,
    }


def scores_agree(runs: list[str]) -> tuple[bool, int, int]:
    """同じ対象の全ての実行（全ての W）の間で，共通の問の probe の得点が全て一致するか（一致したか，共通の問の数，違った問の数）．"""
    tables = [
        json.loads((RESULTS / r / "scores.json").read_text(encoding="utf-8"))
        for r in runs
        if (RESULTS / r / "scores.json").exists()
    ]
    if len(tables) < 2:
        return True, 0, 0
    common = set.intersection(*(set(t) for t in tables))
    differ = sum(any(t[q] != tables[0][q] for t in tables[1:]) for q in common)
    return differ == 0, len(common), differ


def fmt(x: float | None, nd: int = 3) -> str:
    """数値の表示（None は -）．"""
    return "-" if x is None else f"{x:.{nd}f}"


def med_of(lvs: list[dict[str, Any]], key: str) -> float | None:
    """水準の値の中央値（None を除く．全て None なら None）．"""
    xs = [lv[key] for lv in lvs if lv.get(key) is not None]
    return median(xs) if xs else None


def summarize_w(prefix: str, label: str, w: int) -> dict[str, Any] | None:
    """1 つの対象・W の 3 回を集計して表を出し，頂点の処理量などを返す．"""
    runs = [f"{prefix}_nb_w{w}_{label}_r{r}" for r in REPEATS]
    benches = {r: b for r in runs if (b := load_bench(r)) is not None}
    print(f"\n### {label} W = {w}")
    if not benches:
        print("実行なし")
        return None
    for r, b in benches.items():
        print(
            f"{r}: 順 {b['concurrency_order']}，対象 {b['target']['host']}，max_procs {b['max_procs']}，git {b['git_head']}"
        )
    # 水準ごとの判定（欠け＝G1・G3・G5 のどれかが不成立）
    valid_levels: dict[str, dict[int, dict[str, Any]]] = {r: {} for r in benches}
    table: dict[int, list[dict[str, Any]]] = {}
    invalid: set[int] = set()
    g4_all = True
    print(
        "C | 処理量（最小〜中央値〜最大） | p95 s 中央値 | cgroup CPU 中央値 | proc CPU 中央値 | host busy 中央値 | 1 要求の CPU ms | Little 中央値 | 負荷生成 CPU 最大 | 失敗 | 欠け"
    )
    for c in LEVELS:
        lvs = []
        for r, b in benches.items():
            lv = next((x for x in b["levels"] if x["concurrency"] == c), None)
            if lv is None:
                continue
            g = gates(lv, w)
            g4_all = g4_all and g["G4"]
            if g["G1"] and g["G3"] and g["G5"]:
                valid_levels[r][c] = lv
            else:
                invalid.add(c)
            lvs.append(lv)
        if not lvs:
            continue
        table[c] = lvs
        thr = sorted(lv["throughput_rps"] for lv in lvs)
        cg = med_of(lvs, "node_cgroup_util")
        per_req = None if cg is None else 1000 * cg / median(thr)
        print(
            f"{c} | {thr[0]:.1f}〜{median(thr):.1f}〜{thr[-1]:.1f}（{len(lvs)} 回） | {fmt(median(lv['latency_s']['p95'] for lv in lvs), 4)} | "
            f"{fmt(cg)} | {fmt(med_of(lvs, 'node_proc_util'))} | {fmt(med_of(lvs, 'node_host_busy'))} | {fmt(per_req, 2)} | "
            f"{fmt(median(lv['little_ratio'] or 0.0 for lv in lvs))} | {fmt(max(max(lv['generator_cpu_util_per_proc']) for lv in lvs))} | "
            f"{sum(lv['n_errors'] for lv in lvs)} | {'欠け' if c in invalid else ''}"
        )
    peaks: list[float] = []
    peak_levels: list[dict[str, Any]] = []
    for r in benches:
        if not valid_levels[r]:
            continue
        c_best, lv = max(valid_levels[r].items(), key=lambda kv: kv[1]["throughput_rps"])
        peaks.append(lv["throughput_rps"])
        peak_levels.append(lv)
        print(
            f"{r}：頂点 P = {lv['throughput_rps']:.1f}（C = {c_best}，cgroup {fmt(lv.get('node_cgroup_util'))}，host {fmt(lv['node_host_busy'])}）"
        )
    meds = {
        c: median(lv["throughput_rps"] for lv in lvs)
        for c, lvs in table.items()
        if c not in invalid
    }
    x_sat_c = max(meds, key=lambda c: meds[c]) if meds else None
    print(f"欠けの水準：{sorted(invalid)}．G4（CPU が読めた）：{'成立' if g4_all else '不成立'}")
    if x_sat_c is not None:
        print(f"記録：Iteration 9 と同じ X_sat = {meds[x_sat_c]:.1f}（C = {x_sat_c}）")
    if not peaks:
        return {"peaks": [], "invalid": invalid, "benches": list(benches), "g4": g4_all}
    peaks.sort()
    return {
        "peaks": peaks,
        "p_med": median(peaks),
        "cgroup": med_of(peak_levels, "node_cgroup_util"),
        "proc": med_of(peak_levels, "node_proc_util"),
        "host": med_of(peak_levels, "node_host_busy"),
        "invalid": invalid,
        "benches": list(benches),
        "g4": g4_all,
    }


def grew(lo: dict[str, Any] | None, hi: dict[str, Any] | None) -> tuple[bool, float | None]:
    """W → 2W で「伸びた」か（2W の頂点の範囲が W の範囲より上で重ならず，中央値の比が 1.10 以上）と，中央値の比．"""
    if not lo or not hi or not lo["peaks"] or not hi["peaks"]:
        return False, None
    ratio = hi["p_med"] / lo["p_med"]
    return hi["peaks"][0] > lo["peaks"][-1] and ratio >= GROW_RATIO, ratio


def classify(s: dict[str, Any], w: int, c1: float | None) -> str:
    """頂点の水準の CPU による律速の分類（上から順に最初に当てはまるもの）．"""
    if s["host"] is not None and s["host"] >= HOST_SAT:
        return "ノードのデバイスの CPU 全体が律速"
    if s["cgroup"] is None or s["host"] is None:
        return "測定欠け（G4）"
    if w == 1:
        return "基準（W = 1 の頂点の cgroup の CPU が以下の W の比べる先）"
    if c1 is not None and s["cgroup"] >= PER_PROC_SAT * w * c1:
        return "各プロセスが W = 1 と同じ上限で止まる（プロセスの中の直列が律速）"
    return "未特定（プロセスの外：ネットワーク・カーネル・物理コアの取り合いなど）"


def main(date: str) -> None:
    """事前登録の順に出力する．"""
    prefix = f"{date}_iter10"
    print("## 環境の揺れ（各 deploy の flood_score × N = 1 × 300 問．判定に使わない）")
    for name in ("w1_pre", "w2", "w4", "w1_post"):
        s = summarize(RESULTS / f"{prefix}_anchor_{name}")
        if s is None:
            print(f"{name}: 実行なし")
            continue
        in_t = ENV_THR[0] <= s["throughput"] <= ENV_THR[1]
        in_p = ENV_P95[0] <= s["e2e_p95"] <= ENV_P95[1]
        print(
            f"{name}: 処理量 {s['throughput']:.3f} {ENV_THR} {'内' if in_t else '外'}，"
            f"e2e p95 {s['e2e_p95']:.3f} {ENV_P95} {'内' if in_p else '外'}，失敗率 {s['failure_rate']:.4f}"
        )

    for label, host in TARGETS.items():
        print(f"\n## {label}（{host}）{'主' if label == PRIMARY else '記録'}")
        per_w = {w: summarize_w(prefix, label, w) for w in WORKERS}
        all_runs = [r for s in per_w.values() if s for r in s["benches"]]
        within = all(
            (b := load_bench(r)) is not None
            and b["cross_level_score_mismatches"] == 0
            and all(lv["score_mismatches"] == 0 for lv in b["levels"])
            for r in all_runs
        )
        agree, n_common, n_differ = scores_agree(all_runs)
        g2 = agree and within
        print(
            f"\nG2（全ての W・実行の得点の一致）：共通 {n_common} 問のうち不一致 {n_differ}，実行の内の不一致なし {within} → {'成立' if g2 else '不成立'}"
        )
        print(
            "\nW | 頂点 P（3 回．昇順） | P の中央値 | E(W) = P(W) ÷ (W × P(1)) | cgroup CPU | proc CPU | host busy | 欠けの水準 | 律速の分類"
        )
        p1 = per_w[1]["p_med"] if per_w[1] and per_w[1]["peaks"] else None
        c1 = per_w[1]["cgroup"] if per_w[1] and per_w[1]["peaks"] else None
        for w in WORKERS:
            s = per_w[w]
            if not s or not s["peaks"]:
                print(f"{w} | 有効な実行なし")
                continue
            eff = None if p1 is None else s["p_med"] / (w * p1)
            print(
                f"{w} | {[round(x, 1) for x in s['peaks']]} | {s['p_med']:.1f} | {fmt(eff)} | {fmt(s['cgroup'])} | {fmt(s['proc'])} | {fmt(s['host'])} | "
                f"{sorted(s['invalid'])} | {classify(s, w, c1)}"
            )
        s1 = per_w[1]
        if s1 and s1["peaks"] and s1["cgroup"] is not None and s1["proc"] is not None:
            ratio = s1["cgroup"] / s1["proc"]
            ok = CGROUP_PROC_RATIO[0] <= ratio <= CGROUP_PROC_RATIO[1]
            print(
                f"G6（記録．W = 1 の cgroup ÷ proc = {ratio:.3f}，範囲 {CGROUP_PROC_RATIO}）：{'成立' if ok else '不成立'}"
            )
        g12, r12 = grew(per_w[1], per_w[2])
        g24, r24 = grew(per_w[2], per_w[4])
        print(f"W = 1 → 2：中央値の比 {fmt(r12)} → {'伸びた' if g12 else '伸びない'}")
        print(f"W = 2 → 4：中央値の比 {fmt(r24)} → {'伸びた' if g24 else '伸びない'}")
        h1 = g12 and r12 is not None and r12 >= SCALE_MAIN
        name = "H-W1（主）" if label == PRIMARY else "H-W3（記録．15.103 の H-W1）"
        print(f"{name}：W = 1 → 2 で伸び，比 ≥ {SCALE_MAIN} → {'成立' if h1 else '不成立'}")
        if label == PRIMARY:
            print(f"H-W2（記録）：W = 2 → 4 で伸びる → {'成立' if g24 else '不成立'}")
            many_invalid = any(
                s is None or not s["peaks"] or len(s["invalid"]) > 1 for s in per_w.values()
            )
            g5_bad = any(
                lv.get("node_workers") != w
                for w, s in per_w.items()
                if s
                for r in s["benches"]
                if (b := load_bench(r)) is not None
                for lv in b["levels"]
            )
            if not g2 or many_invalid or g5_bad:
                verdict = "棄却（測定不成立）"
            elif h1:
                verdict = "採用"
            elif g12:
                verdict = "部分的に支持"
            else:
                verdict = "棄却（W を増やしても伸びない）"
            print(f"G5（全ての水準で node_workers = W）：{'不成立' if g5_bad else '成立'}")
            print(f"このイテレーションの結論（規則の機械的な適用）：{verdict}")

    drift = load_bench(f"{prefix}_nb_w1_{PRIMARY}_r4")
    if drift is not None:
        peak = max(lv["throughput_rps"] for lv in drift["levels"])
        print(f"\n記録：W = 1 へ戻した後の {PRIMARY} の頂点 {peak:.1f}（判定に使わない）")


if __name__ == "__main__":
    main(sys.argv[1])
