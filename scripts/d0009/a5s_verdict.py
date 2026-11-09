"""d0009 A5 中央の停止時間の掃引（Iteration 34）：Iteration 30 の時系列の再集計で，P2P が上になる中央の停止時間 S の境目 S* を求める（実機・GPU は使わない）．

使い方（リポジトリの直下．実行の後，commit の前に sanitize_results.py を通す）:
    uv run python scripts/d0009/a5s_verdict.py
- 入力：`results/d0009_a6_failure_r{1,2,3}/failure.json`（`p2p[].rates`・`questions`・`start.acked_at_s`，`central.restart`）・
  `results/d0009_a6_failure/verdict.json`（c の積分 I_C・有効なラウンド・I_P・I_P_w0．読むだけで書き換えない）．
- 出力：`results/d0009_a5_outage_sweep/{verdict.json,outage_sweep.json,analysis_report.md}`．
- 定義（journal の Iteration 34）：中央は時刻 0 から S 秒止まり，S ＋ R_r に答えられるようになり，その後 30 秒を測る．窓 W(S) = S ＋ R_r ＋ 30，
  I_P(S, r) = `integrate_rates(rates, bin_start_s, bin_s, 0, W(S))`，I_C = c × 30（S に依らない）．P2P の停止は実測の 45 秒のまま．
  S* = I_P(S) > I_C となる最小の S ≥ 0（`first_time_integral_exceeds`）．T* = S* ＋ R（中央が止まっている時間の全体．I_P(0) > I_C なら T* は [0, R] の中で同じ規則で解く）．
- H-A5S1：条件ごとの S*_r の範囲と S_robust = max S*_r．H-A5S2：格子の各 S の 3 値の判定（Iteration 30 と同じ規則）．
- 記述（判定に使わない）：(i) 区間ごとの復元抽出の幅，(ii) 終わった時刻の版，(iii) R = 1.44 秒の当てはめ．
積分・境目・区間の平均は `atrium.dynamics` の既存実装を呼ぶ．
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.dynamics import (
    binned_rates,
    fill_forward,
    first_time_integral_exceeds,
    integrate_rates,
)

SOURCE_DIR = Path("results/d0009_a6_failure")
OUT_DIR = Path("results/d0009_a5_outage_sweep")
# 事前登録の定数（journal の Iteration 34．結果を見て変えない）
GRID_S = (0.0, 2.5, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0)
LOWER_BOUND_GRID_S = (
    60.0,
    90.0,
    120.0,
    300.0,
)  # S > 45 は単調性の下限だけ（測っていない時刻の割合は使わない）
REFERENCE_S = (30.0, 10.0)  # Kubernetes の既定の liveness（30 秒）・自動の検出（10 秒）の参照点
POST_S = 30.0
# 窓の探索の上限に使う P2P の停止時間（実測の 45 秒）．main で failure.json の params と一致を確かめる
DOWN_S_FOR_RANGE = 45.0
EXPECTED_CONDITIONS = 8
MIN_VALID_ROUNDS = 2
G_R0_TOL = 1e-9
G_M_TOL = 1e-6
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 12
PERCENTILES = (2.5, 97.5)
R_NO_REBUILD_S = 1.44  # p0007 E1（fusion: none．BM25 を作り直さない中央）の再起動
NOT_FOUND_SENTINEL = 1e9  # 範囲内で超えない標本（S* > 45）を百分位の計算で最大として扱う値
P2P_ABOVE = "P2P が上"
CENTRAL_ABOVE = "中央が上"
UNDECIDED = "決まらない"


def three_way(p2p: Sequence[float], central: float) -> str:
    """有効なラウンドの全てで I_P > I_C なら P2P_ABOVE，全てで < なら CENTRAL_ABOVE，それ以外は UNDECIDED（Iteration 30 と同じ規則）．"""
    if all(p > central for p in p2p):
        return P2P_ABOVE
    if all(p < central for p in p2p):
        return CENTRAL_ABOVE
    return UNDECIDED


def crossing_s(
    rates: Sequence[float], start: float, bin_s: float, central_i: float, restart_s: float
) -> dict[str, float | None]:
    """境目 S*（と T* = S* ＋ R）．窓の終わり t = S ＋ R ＋ 30 を [30, 45 ＋ R ＋ 30] の中で解く（T = t − 30 ≥ 0 なので I_P(0) > I_C のとき S* = 0）．

    範囲内で超えなければ両方 None（S* > 45）．
    """
    t = first_time_integral_exceeds(
        rates, start, bin_s, central_i, POST_S, DOWN_S_FOR_RANGE + restart_s + POST_S
    )
    if t is None:
        return {"S_star": None, "T_star": None}
    t_star = t - POST_S
    return {"S_star": max(0.0, t_star - restart_s), "T_star": t_star}


def summarize_range(values: Sequence[float | None]) -> dict[str, Any]:
    """3 ラウンドの S*_r の範囲と H-A5S1 の向き（None は S > 45）．"""
    if any(v is None for v in values):
        return {"range": None, "label": "S = 45 でも超えない"}
    vs = [float(v) for v in values if v is not None]
    if all(v == 0.0 for v in vs):
        label = "S によらず P2P が上"
    elif all(0.0 < v <= DOWN_S_FOR_RANGE for v in vs):
        label = "境目あり"
    else:
        label = "ラウンドで分かれる（0 と 0 より大きい値が混在）"
    return {"range": [min(vs), max(vs)], "label": label}


def window_integral(
    rates: Sequence[float], start: float, bin_s: float, restart_s: float, outage_s: float
) -> float:
    """I_P(S)：窓 [0, S ＋ R ＋ 30] の P2P の答えられた割合の積分（割合 × 秒）．"""
    return integrate_rates(rates, start, bin_s, 0.0, outage_s + restart_s + POST_S)


def question_bins(cond: Mapping[str, Any]) -> tuple[list[int], list[int]]:
    """区間ごとの質問数と答えられた数（`binned_rates` と同じ区間の切り方）．"""
    start, bin_s, n_bins = float(cond["bin_start_s"]), float(cond["bin_s"]), len(cond["rates"])
    counts, hits = [0] * n_bins, [0] * n_bins
    for q in cond["questions"]:
        t = q["t_s"]
        if start <= t < start + n_bins * bin_s:
            b = min(int((t - start) // bin_s), n_bins - 1)
            counts[b] += 1
            hits[b] += bool(q["hit"])
    return counts, hits


def bootstrap_s_star(
    cond: Mapping[str, Any], restart_s: float, central_i: float, rng: np.random.Generator
) -> dict[str, Any]:
    """記述 (i)：各区間の質問を同じ数だけ復元抽出して S* を計算し直した百分位（区間内の答えられた数の二項分布への置き換えと同じ分布）．"""
    counts, hits = question_bins(cond)
    start, bin_s = float(cond["bin_start_s"]), float(cond["bin_s"])
    initial = float(cond["baseline_hit_rate"])
    draws = np.empty((BOOTSTRAP_N, len(counts)))
    for b, (n, h) in enumerate(zip(counts, hits, strict=True)):
        draws[:, b] = rng.binomial(n, h / n, size=BOOTSTRAP_N) / n if n else np.nan
    s_stars: list[float] = []
    for row in draws:
        rates = fill_forward([None if np.isnan(x) else float(x) for x in row], initial)
        s = crossing_s(rates, start, bin_s, central_i, restart_s)["S_star"]
        s_stars.append(NOT_FOUND_SENTINEL if s is None else s)
    lo, hi = np.percentile(s_stars, PERCENTILES)
    n_over = sum(s >= NOT_FOUND_SENTINEL for s in s_stars)
    return {
        "p2_5": None if lo >= NOT_FOUND_SENTINEL else float(lo),
        "p97_5": None if hi >= NOT_FOUND_SENTINEL else float(hi),
        "n_over_45": int(n_over),
        "n_boot": BOOTSTRAP_N,
    }


def completion_rates(cond: Mapping[str, Any], down_s: float) -> list[float]:
    """記述 (ii)：止めた間 [0, down_s) に聞いた問のうち，終わった時刻が再開の確認より後のものを「答えられない」にして区間の平均を作り直す．"""
    ack = float(cond["start"]["acked_at_s"])
    start, bin_s, n_bins = float(cond["bin_start_s"]), float(cond["bin_s"]), len(cond["rates"])
    times: list[float] = []
    flags: list[bool] = []
    for q in cond["questions"]:
        late = 0.0 <= q["t_s"] < down_s and q["e2e_s"] is not None and q["t_s"] + q["e2e_s"] >= ack
        times.append(q["t_s"])
        flags.append(bool(q["hit"]) and not late)
    return fill_forward(
        binned_rates(times, flags, start, start + n_bins * bin_s, bin_s),
        float(cond["baseline_hit_rate"]),
    )


def main() -> int:
    """3 ラウンドの failure.json から掃引を計算し，verdict.json・outage_sweep.json・analysis_report.md を書く．"""
    source = json.loads((SOURCE_DIR / "verdict.json").read_text("utf-8"))
    central_i = float(source["central_integral_assumed_45s"])
    valid_runs: list[str] = source["valid_rounds"]
    failures = {
        run: json.loads(Path(f"results/{run}/failure.json").read_text("utf-8"))
        for run in valid_runs
    }
    down_s = {float(f["params"]["down_s"]) for f in failures.values()}
    assert down_s == {DOWN_S_FOR_RANGE}, f"P2P の停止時間が 45 秒でない: {down_s}"
    restart = {
        r: float(f["central"]["restart"]["restart_to_answer_s"]) for r, f in failures.items()
    }
    source_conditions = {(c["mode"], c["k"]): c for c in source["conditions"]}
    rng = np.random.default_rng(BOOTSTRAP_SEED)

    conditions: list[dict[str, Any]] = []
    g_r0_errors: list[float] = []
    g_m_violations = 0
    g_m_solve_errors: list[float] = []
    rates_match_questions = True
    for (mode, k), src in sorted(source_conditions.items(), key=lambda mk: (mk[0][0], mk[0][1])):
        per_round: list[dict[str, Any]] = []
        for ri, run in enumerate(valid_runs):
            cond = next(c for c in failures[run]["p2p"] if (c["mode"], c["k"]) == (mode, k))
            rates = fill_forward(cond["rates"], float(cond["baseline_hit_rate"]))
            start, bin_s, r_s = float(cond["bin_start_s"]), float(cond["bin_s"]), restart[run]

            # G-R0：S = 45 と S = 0 が Iteration 30 の I_P・I_P_w0 と一致する
            g_r0_errors += [
                abs(window_integral(rates, start, bin_s, r_s, 45.0) - src["I_P"][ri]),
                abs(window_integral(rates, start, bin_s, r_s, 0.0) - src["I_P_w0"][ri]),
            ]
            grid = [window_integral(rates, start, bin_s, r_s, s) for s in GRID_S]
            g_m_violations += sum(b < a for a, b in zip(grid, grid[1:], strict=False))
            main_cross = crossing_s(rates, start, bin_s, central_i, r_s)
            if main_cross["S_star"] and main_cross["S_star"] > 0:
                g_m_solve_errors.append(
                    abs(window_integral(rates, start, bin_s, r_s, main_cross["S_star"]) - central_i)
                )
            # `rates` が問ごとの結果の区間平均と一致するか（再構成の前提の確認）
            recomputed = binned_rates(
                [q["t_s"] for q in cond["questions"]],
                [bool(q["hit"]) for q in cond["questions"]],
                start,
                start + len(rates) * bin_s,
                bin_s,
            )
            rates_match_questions &= bool(
                np.allclose(
                    fill_forward(recomputed, float(cond["baseline_hit_rate"])), rates, atol=1e-12
                )
            )
            done = completion_rates(cond, DOWN_S_FOR_RANGE)
            t_star = main_cross["T_star"]
            per_round.append(
                {
                    "run": run,
                    "R_s": r_s,
                    "I_P_grid": dict(zip(map(str, GRID_S), grid, strict=True)),
                    "I_P_at_45": window_integral(rates, start, bin_s, r_s, 45.0),
                    "S_star": main_cross["S_star"],
                    "T_star": t_star,
                    "S_star_R1_44": None if t_star is None else max(0.0, t_star - R_NO_REBUILD_S),
                    "bootstrap": bootstrap_s_star(cond, r_s, central_i, rng),
                    "S_star_completion": crossing_s(done, start, bin_s, central_i, r_s),
                }
            )
        conditions.append({"mode": mode, "k": k, "rounds": per_round})

    # H-A5S1・H-A5S2
    h_s1: dict[str, Any] = {}
    h_s2: dict[str, dict[str, str]] = {str(s): {} for s in GRID_S}
    for c in conditions:
        key = f"{c['mode']}:k={c['k']}"
        s_vals = [r["S_star"] for r in c["rounds"]]
        t_vals = [r["T_star"] for r in c["rounds"]]
        h_s1[key] = {"S_star": summarize_range(s_vals), "T_star": summarize_range(t_vals)}
        for s in GRID_S:
            h_s2[str(s)][key] = three_way([r["I_P_grid"][str(s)] for r in c["rounds"]], central_i)
    all_s = [r["S_star"] for c in conditions for r in c["rounds"]]
    all_t = [r["T_star"] for c in conditions for r in c["rounds"]]
    s_robust = None if any(v is None for v in all_s) else max(v for v in all_s if v is not None)
    t_robust = None if any(v is None for v in all_t) else max(v for v in all_t if v is not None)
    lower_bound_p2p_above = all(r["I_P_at_45"] > central_i for c in conditions for r in c["rounds"])

    gates = {
        "G_T": "mise run check の結果は journal に記録（このスクリプトでは確かめない）",
        "G_R0_max_abs_error": max(g_r0_errors),
        "G_R0": max(g_r0_errors) <= G_R0_TOL,
        "G_M_grid_monotone_violations": g_m_violations,
        "G_M_solve_max_abs_error": max(g_m_solve_errors) if g_m_solve_errors else None,
        "G_M": g_m_violations == 0 and all(e <= G_M_TOL for e in g_m_solve_errors),
        "n_valid_rounds": len(valid_runs),
        "n_conditions": len(conditions),
        "rates_match_question_bins": rates_match_questions,
    }
    gates["G_R0_conditions_match_iteration30"] = all(
        h_s2["45.0"][f"{c['mode']}:k={c['k']}"]
        == source["verdict_original"]["H_A6F1"][f"{c['mode']}:k={c['k']}"]
        and h_s2["0.0"][f"{c['mode']}:k={c['k']}"]
        == source["verdict_original"]["H_A6F2_reference_only"][f"{c['mode']}:k={c['k']}"]
        for c in conditions
    )
    passed = (
        gates["G_R0"]
        and gates["G_R0_conditions_match_iteration30"]
        and gates["G_M"]
        and len(valid_runs) >= MIN_VALID_ROUNDS
        and len(conditions) == EXPECTED_CONDITIONS
    )
    verdict = {
        "iteration": 34,
        "c": source["c"],
        "I_C": central_i,
        "valid_rounds": valid_runs,
        "gates": gates,
        "gates_passed_except_G_T": passed,
        "verdict_original": {
            "H_A5S1": h_s1,
            "S_robust": s_robust,
            "T_robust": t_robust,
            "H_A5S2": h_s2,
            "reference_points": {str(s): h_s2[str(s)] for s in REFERENCE_S},
            "S_gt_45_lower_bound": {
                "grid": list(LOWER_BOUND_GRID_S),
                "all_conditions_p2p_above": lower_bound_p2p_above,
                "basis": "I_P(S) は S について単調に増えるので，全条件・全ラウンドで I_P(45) > I_C なら S > 45 でも I_P > I_C（測っていない時刻の割合は 0 と置いた下限）",
            },
        },
        "note": "P2P の停止は実測の 45 秒のまま．S < 45 は中央が P2P の止めた台より先に戻る比較．問の標本の幅・終わった時刻の版・R = 1.44 秒は outage_sweep.json（判定に使わない）．",
    }
    sweep = {
        "iteration": 34,
        "grid_S": list(GRID_S),
        "I_C": central_i,
        "conditions": conditions,
        "note_symmetric": "中央の再起動 R の分だけ中央が長く止まるので，P2P の再開の後の割合が平常に戻る限り S によらず P2P が上になる（対称の比較は実測が無いので出さない）．",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "verdict.json").write_text(
        json.dumps(verdict, indent=1, ensure_ascii=False) + "\n", "utf-8"
    )
    (OUT_DIR / "outage_sweep.json").write_text(
        json.dumps(sweep, indent=1, ensure_ascii=False) + "\n", "utf-8"
    )
    (OUT_DIR / "analysis_report.md").write_text(render_report(verdict, conditions), "utf-8")
    print(
        json.dumps(
            {"gates": gates, "S_robust": s_robust, "T_robust": t_robust, "H_A5S1": h_s1},
            indent=1,
            ensure_ascii=False,
        )
    )
    return 0


def _fmt(v: float | None) -> str:
    """表示用：None は「> 45」，数値は小数 1 位．"""
    return "> 45" if v is None else f"{v:.1f}"


def render_report(verdict: Mapping[str, Any], conditions: Sequence[Mapping[str, Any]]) -> str:
    """verdict と条件ごとの値から analysis_report.md（数値の一覧．解釈は journal 側）を作る．"""
    vo = verdict["verdict_original"]
    lines = [
        "# d0009 A5 中央の停止時間の掃引（Iteration 34）の集計",
        "",
        "数値の一覧（`verdict.json`・`outage_sweep.json` の再掲）．判定・解釈は journal の Iteration 34 に書く．",
        "",
        "## ゲート",
        "",
        "```json",
        json.dumps(verdict["gates"], indent=1, ensure_ascii=False),
        "```",
        "",
        f"## H-A5S1：境目 S*（秒．3 ラウンドの範囲）．S_robust = {_fmt(vo['S_robust'])}，T_robust = {_fmt(vo['T_robust'])}",
        "",
        "| 条件 | S* の範囲 | T* の範囲 | 向き |",
        "|---|---|---|---|",
    ]
    for key, v in vo["H_A5S1"].items():
        s_r, t_r = v["S_star"]["range"], v["T_star"]["range"]
        s_txt = "> 45" if s_r is None else f"{s_r[0]:.2f}〜{s_r[1]:.2f}"
        t_txt = "> 45" if t_r is None else f"{t_r[0]:.2f}〜{t_r[1]:.2f}"
        lines.append(f"| {key} | {s_txt} | {t_txt} | {v['S_star']['label']} |")
    lines += [
        "",
        "## 記述（判定に使わない）：ラウンドごとの S*（秒）",
        "",
        "| 条件 | ラウンド | S* | 復元抽出 2.5〜97.5 % | 終わった時刻の版 S* | R = 1.44 秒の S* |",
        "|---|---|---|---|---|---|",
    ]
    for c in conditions:
        for r in c["rounds"]:
            b = r["bootstrap"]
            lo, hi = _fmt(b["p2_5"]), _fmt(b["p97_5"])
            lines.append(
                f"| {c['mode']}:k={c['k']} | {r['run'][-2:]} | {_fmt(r['S_star'])} | "
                f"{lo}〜{hi}（> 45 の標本 {b['n_over_45']}/{b['n_boot']}） | "
                f"{_fmt(r['S_star_completion']['S_star'])} | {_fmt(r['S_star_R1_44'])} |"
            )
    lines += ["", "## H-A5S2：格子の各 S の向き（8 条件）", ""]
    conds = list(next(iter(vo["H_A5S2"].values())).keys())
    lines += ["| S（秒） | " + " | ".join(conds) + " |", "|---|" + "---|" * len(conds)]
    for s, row in vo["H_A5S2"].items():
        lines.append(f"| {float(s):g} | " + " | ".join(row[k] for k in conds) + " |")
    lb = vo["S_gt_45_lower_bound"]
    lines += [
        "",
        f"S > 45（{', '.join(f'{g:g}' for g in lb['grid'])} 秒）：全条件・全ラウンドで I_P(45) > I_C = {verdict['I_C']:.4f} は {lb['all_conditions_p2p_above']}．"
        "I_P は S について単調に増えるので，真なら下限で P2P が上．",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
