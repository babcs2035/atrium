"""Iteration 9：専門家ノード 1 台への閉じた系の probe の負荷試験（atrium node-bench）の集計（事前登録の正しさの確認 G1〜G4・飽和の規則・律速の分類の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で．DATE は実行日 YYYYMMDD）:
    uv run python results/20261015_iter9_node_bench_summary/iter9_summary.py <DATE> > results/20261015_iter9_node_bench_summary/iter9_summary.out

規則は journal の Iteration 9 の事前登録と同じ（範囲が重ならないときだけ「差がある」）．環境の揺れの確認（前後の flood_score × N = 1 × 300 問）の処理量・e2e p95 は
iter6_summary.py の summarize（処理量 = 成功した問の数 ÷ run_meta.json の finished_at − started_at）で求め，hit@2 は比べない（B27 (e)：300 問の値を 2,940 問の値と同じ欄に並べない）．
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
LEVELS = (1, 2, 4, 8, 16, 32, 64)
# 対象のノード（主・記録）．ラベルは run_id に入れる短い名前
TARGETS = {"t13102": "192.168.13.102", "t15103": "192.168.15.103"}
PRIMARY = "t13102"
FAIL_LIMIT = 0.01
GEN_CPU_LIMIT = 0.80
LITTLE_MIN = 0.90
GROW_RATIO = 1.10
HOST_SAT = 0.90
PROC_SERIAL = (0.85, 1.5)
ITER8_ESTIMATE = 190.0
ENV_THR = (11.127, 14.247)
ENV_P95 = (3.053, 4.067)


def load_bench(run: str) -> dict[str, Any] | None:
    """node_bench.json（無ければ None）．"""
    path = RESULTS / run / "node_bench.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def level_of(bench: dict[str, Any], c: int) -> dict[str, Any] | None:
    """C の水準の集計．"""
    return next((lv for lv in bench["levels"] if lv["concurrency"] == c), None)


def gates(lv: dict[str, Any]) -> dict[str, bool]:
    """1 つの水準の G1（失敗率）・G3（負荷生成が律速でない）・G4（CPU が読めた）．"""
    n = lv["n_ok"] + lv["n_errors"]
    return {
        "G1": n > 0 and lv["n_errors"] / n <= FAIL_LIMIT,
        "G3": max(lv["generator_cpu_util_per_proc"]) <= GEN_CPU_LIMIT
        and (lv["little_ratio"] or 0.0) >= LITTLE_MIN,
        "G4": lv["node_proc_util"] is not None and lv["node_host_busy"] is not None,
    }


def scores_agree(runs: list[str]) -> tuple[bool, int, int]:
    """同じ対象の実行の間で，共通の問の probe の得点が全て一致するか（一致したか，共通の問の数，違った問の数）．"""
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


def main(date: str) -> None:
    """事前登録の順に出力する．"""
    prefix = f"{date}_iter9"
    print("## 環境の揺れ（前後の flood_score × N = 1 × 300 問．判定に使わない）")
    for when in ("pre", "post"):
        s = summarize(RESULTS / f"{prefix}_anchor_fs_n1_p30_{when}")
        if s is None:
            print(f"{when}: 実行なし")
            continue
        in_t = ENV_THR[0] <= s["throughput"] <= ENV_THR[1]
        in_p = ENV_P95[0] <= s["e2e_p95"] <= ENV_P95[1]
        print(
            f"{when}: 処理量 {s['throughput']:.3f} {ENV_THR} {'内' if in_t else '外'}，"
            f"e2e p95 {s['e2e_p95']:.3f} {ENV_P95} {'内' if in_p else '外'}，失敗率 {s['failure_rate']:.4f}"
        )

    best_range: dict[str, list[float]] = {}
    for label, host in TARGETS.items():
        runs = [f"{prefix}_nb_{label}_r{r}" for r in REPEATS]
        benches = {r: load_bench(r) for r in runs}
        print(f"\n## {label}（{host}）{'主' if label == PRIMARY else '記録'}")
        present = [r for r, b in benches.items() if b is not None]
        if not present:
            print("実行なし")
            continue
        for r in present:
            b = benches[r]
            assert b is not None
            ok_target = b["target"]["host"] == host
            print(
                f"{r}: 順 {b['concurrency_order']}，対象 {b['target']['host']}（{'一致' if ok_target else '不一致'}），"
                f"シャード {b['target']['shard_ids']}，水準の間の得点の不一致 {b['cross_level_score_mismatches']}，git {b['git_head']}"
            )
        agree, n_common, n_differ = scores_agree(present)
        within = all(
            (b or {}).get("cross_level_score_mismatches", 0) == 0
            and all(lv["score_mismatches"] == 0 for lv in (b or {"levels": []})["levels"])
            for b in benches.values()
        )
        g2 = agree and within
        print(f"G2（得点の一致）：実行の間 共通 {n_common} 問のうち不一致 {n_differ}，実行の内の不一致なし {within} → {'成立' if g2 else '不成立'}")

        print("\nC | 処理量 req/s（最小〜中央値〜最大） | p50 s 中央値 | p95 s 中央値 | node_s p95 中央値 | proc CPU 中央値 | host busy 中央値 | Little 中央値 | 負荷生成 CPU 最大 | 失敗 | G1 | G3 | G4")
        table: dict[int, dict[str, Any]] = {}
        for c in LEVELS:
            lvs = [lv for b in benches.values() if b is not None and (lv := level_of(b, c)) is not None]
            if not lvs:
                continue
            thr = sorted(lv["throughput_rps"] for lv in lvs)
            g = [gates(lv) for lv in lvs]
            row = {
                "thr": thr,
                "med": median(thr),
                "p50": median(lv["latency_s"]["p50"] for lv in lvs if lv["latency_s"]["p50"] is not None),
                "p95": median(lv["latency_s"]["p95"] for lv in lvs if lv["latency_s"]["p95"] is not None),
                "node_p95": median(lv["node_s"]["p95"] for lv in lvs if lv["node_s"]["p95"] is not None),
                "proc": median(x for lv in lvs if (x := lv["node_proc_util"]) is not None) if any(lv["node_proc_util"] is not None for lv in lvs) else None,
                "host": median(x for lv in lvs if (x := lv["node_host_busy"]) is not None) if any(lv["node_host_busy"] is not None for lv in lvs) else None,
                "little": median(lv["little_ratio"] or 0.0 for lv in lvs),
                "gen": max(max(lv["generator_cpu_util_per_proc"]) for lv in lvs),
                "errors": sum(lv["n_errors"] for lv in lvs),
                "G1": all(x["G1"] for x in g),
                "G3": all(x["G3"] for x in g),
                "G4": all(x["G4"] for x in g),
                "n": len(lvs),
            }
            table[c] = row
            print(
                f"{c} | {thr[0]:.1f}〜{row['med']:.1f}〜{thr[-1]:.1f}（{row['n']} 回） | {fmt(row['p50'], 4)} | {fmt(row['p95'], 4)} | {fmt(row['node_p95'], 4)} | "
                f"{fmt(row['proc'])} | {fmt(row['host'])} | {fmt(row['little'])} | {fmt(row['gen'])} | {row['errors']} | {row['G1']} | {row['G3']} | {row['G4']}"
            )

        valid = {c: r for c, r in table.items() if r["G1"] and r["G3"]}
        print(f"\n欠け（G1 か G3 が不成立の水準．飽和の規則から除く）：{sorted(set(table) - set(valid))}")
        # 飽和の規則：C を倍にして「範囲が C/2 の範囲より上で重ならず，中央値の比が 1.10 以上」が初めて成り立たない C
        c_sat = None
        for c in LEVELS[1:]:
            half = c // 2
            if c not in valid or half not in valid:
                continue
            grew = valid[c]["thr"][0] > valid[half]["thr"][-1] and valid[c]["med"] >= GROW_RATIO * valid[half]["med"]
            ratio = valid[c]["med"] / valid[half]["med"]
            print(f"C = {half} → {c}：中央値の比 {ratio:.3f}，範囲が上で重ならない {valid[c]['thr'][0] > valid[half]['thr'][-1]} → {'伸びた' if grew else '伸びない'}")
            if not grew and c_sat is None:
                c_sat = c
        if not valid:
            print("有効な水準なし")
            continue
        c_best = max(valid, key=lambda c: valid[c]["med"])
        x_sat = valid[c_best]["med"]
        print(f"飽和の C：{c_sat if c_sat is not None else 'C = 64 まで飽和なし'}．処理量の中央値が最大の C = {c_best}（{x_sat:.1f} req/s）")
        best = valid[c_best]
        best_range[label] = best["thr"]
        if best["host"] is not None and best["host"] >= HOST_SAT:
            cls = "ノードのデバイスの CPU 全体が律速"
        elif best["proc"] is not None and PROC_SERIAL[0] <= best["proc"] < PROC_SERIAL[1]:
            cls = "ノードの 1 プロセスの直列の処理（GIL 等）が律速の候補"
        elif best["proc"] is None or best["host"] is None:
            cls = "測定欠け（G4）"
        else:
            cls = "未特定（ロック・スレッドプール・ネットワーク等）"
        print(f"律速の分類（C = {c_best}．proc {fmt(best['proc'])}，host {fmt(best['host'])}）：{cls}")
        if label == PRIMARY:
            h1 = c_sat is not None and g2
            h2 = x_sat >= ITER8_ESTIMATE
            print(f"H-N1（主．C ≤ 64 で飽和する）：{'成立' if h1 else '不成立'}")
            print(f"H-N2（飽和の処理量 ≥ {ITER8_ESTIMATE} req/s）：{'成立' if h2 else '不成立'}（{x_sat:.1f}）")
            invalid_levels = len(set(table) - set(valid))
            if not g2 or invalid_levels > 1:
                verdict = "棄却（測定不成立）"
            elif c_sat is not None:
                verdict = "採用"
            else:
                verdict = "部分的に支持（C = 64 の値は下限）"
            print(f"このイテレーションの結論（規則の機械的な適用）：{verdict}")

    if len(best_range) == len(TARGETS):
        other = next(label for label in TARGETS if label != PRIMARY)
        h3 = best_range[other][0] > best_range[PRIMARY][-1]
        print(f"\nH-N3（記録．{other} の C_best の範囲が {PRIMARY} の C_best の範囲より上で重ならない）：{'成立' if h3 else '不成立'}")


if __name__ == "__main__":
    main(sys.argv[1])
