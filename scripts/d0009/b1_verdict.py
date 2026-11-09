"""d0009 B1（Iteration 35）：質問者の生存確認で止まったノードを問い合わせ先から外す仕組みの，障害の測定 3 ラウンドの集計・ゲート・判定．

使い方（リポジトリの直下．実機は使わない．実行の後，commit の前に sanitize_results.py を通す）:
    uv run python scripts/d0009/b1_verdict.py [--prefix results/d0009_b1_exclusion_r] [--out-dir results/d0009_b1_exclusion]
- 入力：`<prefix>{1,2,3}/`（failure.json・g_r_not_serving_after.txt），`results/d0009_a6_failure/{verdict.json,posthoc_completion.json}`・
  `results/d0009_a6_failure_r{1,2,3}/failure.json`（Iteration 30．外す仕組み無し．読むだけ）．
- 出力：`<out-dir>/{verdict.json,b1_summary.json,analysis_report.md}`．
- 主の指標 F（journal の Iteration 35）：止めた間 [0, down_s) に聞いた問のうち，答えられた（`hit`）割合．終わった時刻の版は，終わった時刻
  `t_s + e2e_s` が再開の確認 `start.acked_at_s` より前の問だけを数える（Iteration 30 の `posthoc_completion.json` と同じ分け方）．聞いた時刻の版は `fault_hit_rate`．
- H-B1a（container）・H-B1b（availability）の規則・ゲート G-TB・G-FP・G-B・G-R・有効なラウンド・採用の条件は journal の Iteration 35 のとおり．
積分・境目・復元抽出に使う関数は，`atrium.dynamics` と `a5s_verdict.py`（Iteration 34）の既存実装をそのまま呼ぶ．
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from a5s_verdict import crossing_s
from a5s_verdict import three_way as three_way_integral

from atrium.dynamics import integrate_rates

ROUNDS = (1, 2, 3)
I30_PREFIX = "results/d0009_a6_failure_r"
I30_DIR = Path("results/d0009_a6_failure")
POST_S = 30.0
EXPECTED_CONDITIONS = 8
MIN_VALID_ROUNDS = 2
# 事前登録の定数（journal の Iteration 35．結果を見て変えない）
MARGIN_UP = 0.15  # H-B1a：container の「上がった」「下がった」の幅
MARGIN_SAME = 0.10  # H-B1b：availability の「変わらない」の幅
MIN_K_UP = 3  # H-B1a の仮説の成立：container の 4 つの k のうち上がった数の下限
MIN_PRE_STOP_HIT = 0.86  # G-FP (i)
MIN_BASELINE_RETURNED = 0.95  # G-B
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 12
PERCENTILES = (2.5, 97.5)
UP = "上がった"
DOWN = "下がった"
UNDECIDED = "決まらない"
SAME = "変わらない"
CHANGED = "変わった"


def fault_questions(cond: Mapping[str, Any], down_s: float) -> list[Mapping[str, Any]]:
    """止めた間 [0, down_s) に聞いた問．"""
    return [q for q in cond["questions"] if 0.0 <= q["t_s"] < down_s]


def answered_before_restart(q: Mapping[str, Any], restart_ack_s: float) -> bool:
    """答えられ（hit），かつ終わった時刻が再開の確認より前の問か（終わった時刻の版）．"""
    return bool(q["hit"]) and q["e2e_s"] is not None and q["t_s"] + q["e2e_s"] < restart_ack_s


def bootstrap_rate(flags: Sequence[bool], rng: np.random.Generator) -> list[float | None]:
    """記述 (D5)：問を復元抽出（BOOTSTRAP_N 回）した割合の 2.5〜97.5 パーセンタイル（問が無ければ None）．"""
    if not flags:
        return [None, None]
    arr = np.asarray(flags, dtype=float)
    draws = rng.choice(arr, size=(BOOTSTRAP_N, len(arr)), replace=True).mean(axis=1)
    lo, hi = np.percentile(draws, PERCENTILES)
    return [float(lo), float(hi)]


def percentile50_and_max(values: Sequence[float | None]) -> dict[str, float | None]:
    """None を除いた中央値・最大・値の数（無ければ None）．"""
    vs = [v for v in values if v is not None]
    return {
        "p50": statistics.median(vs) if vs else None,
        "max": max(vs) if vs else None,
        "n": len(vs),
    }


def condition_metrics(
    cond: Mapping[str, Any], params: Mapping[str, Any], central_i: float, rng: np.random.Generator
) -> dict[str, Any]:
    """1 条件（止め方，k）・1 ラウンドの主の指標・併記 (D1)〜(D5)．"""
    down_s = float(params["down_s"])
    ack = float(cond["start"]["acked_at_s"])
    fault = fault_questions(cond, down_s)
    n = len(fault)
    done = [answered_before_restart(q, ack) for q in fault]
    hit = [bool(q["hit"]) for q in fault]
    returned = [bool(q.get("returned")) for q in fault]
    kinds: dict[str, int] = {}
    for q in fault:
        if q.get("failure_kind") is not None:
            kinds[q["failure_kind"]] = kinds.get(q["failure_kind"], 0) + 1
    # (D2)：委託先が外されていた問（delegate_down が None の問は打ち切りで，選んだ受信箱が分からない．外されていない側に数える）
    failed = [q for q in fault if not q.get("returned")]
    down_failed = [q for q in failed if q.get("delegate_down") is True]
    not_delegate_down = [q for q in fault if q.get("delegate_down") is not True]
    health = cond.get("peer_health")
    return {
        "mode": cond["mode"],
        "k": cond["k"],
        "fault_n": n,
        "F_completion": sum(done) / n if n else None,
        "F_asked": cond["fault_hit_rate"],
        "F_completion_ci": bootstrap_rate(done, rng),
        "restart_acked_at_s": ack,
        "returned_rate": sum(returned) / n if n else None,
        "owner_hit_rate": cond["owner"]["fault"]["rates"]["owner_hit"],
        "failure_kinds": kinds,
        "D2": {
            "n_failed": len(failed),
            "n_failed_delegate_down": len(down_failed),
            "failed_delegate_down_share": len(down_failed) / len(failed) if failed else None,
            "n_delegate_down_unknown": sum(q.get("delegate_down") is None for q in fault),
            "n_not_delegate_down": len(not_delegate_down),
            "F_completion_not_delegate_down": (
                sum(answered_before_restart(q, ack) for q in not_delegate_down)
                / len(not_delegate_down)
                if not_delegate_down
                else None
            ),
        },
        "D3": None
        if health is None
        else {
            "dropped_at_s": percentile50_and_max(list(health["dropped_at_s"].values())),
            "restored_after_start_ack_s": percentile50_and_max(
                list(health["restored_after_start_ack_s"].values())
            ),
            "false_positive_drops": health["false_positive_drops"],
        },
        "integral": cond["integral"],
        "I_C": central_i,
        "n_visible_min": min(
            (q["n_visible"] for q in cond["questions"] if q.get("n_visible") is not None),
            default=None,
        ),
        "_hit_flags": hit,
    }


def round_gates(run_dir: Path, failure: Mapping[str, Any]) -> dict[str, Any]:
    """ラウンドの有効性：G-FP (i)・G-B・G-R・8 条件を流し終えたこと，と G-FP (ii)・G-TB の数．"""
    p2p = failure["p2p"]
    n = sum(r["owner"]["baseline"]["n"] for r in p2p)
    pre_hit = sum(r["owner"]["baseline"]["n_hit"] for r in p2p)
    returned = sum(r["owner"]["baseline"]["n_returned"] for r in p2p)
    not_serving = run_dir / "g_r_not_serving_after.txt"
    return {
        "n_conditions": len(p2p),
        "pre_stop_hit_rate": pre_hit / n if n else None,
        "G_FP_i": bool(n and pre_hit / n >= MIN_PRE_STOP_HIT),
        "baseline_returned_rate": returned / n if n else None,
        "G_B": bool(n and returned / n >= MIN_BASELINE_RETURNED),
        "G_R": not_serving.exists() and not_serving.read_text("utf-8").strip() == "none",
        "G_FP_ii_false_positive_drops": sum(
            (r.get("peer_health") or {}).get("false_positive_drops", 0) for r in p2p
        ),
        "n_table_miss": sum(
            q.get("failure_kind") == "table_miss" for r in p2p for q in r["questions"]
        ),
    }


def load_i30_reference() -> dict[tuple[str, int], dict[str, list[float]]]:
    """Iteration 30 の 3 ラウンドの条件ごとの F（終わった時刻の版・聞いた時刻の版）と，I_P・I_P の窓の境目 S*（同じ規則で出し直す）．"""
    posthoc = json.loads((I30_DIR / "posthoc_completion.json").read_text("utf-8"))
    central_i = float(
        json.loads((I30_DIR / "verdict.json").read_text("utf-8"))["central_integral_assumed_45s"]
    )
    ref: dict[tuple[str, int], dict[str, list[float]]] = {}
    for r in ROUNDS:
        run = f"d0009_a6_failure_r{r}"
        failure = json.loads(Path(f"results/{run}/failure.json").read_text("utf-8"))
        post = {(c["mode"], c["k"]): c for c in posthoc["rounds"][run]}
        restart_s = failure["central"]["restart"]["restart_to_answer_s"]
        for cond in failure["p2p"]:
            entry = ref.setdefault(
                (cond["mode"], cond["k"]),
                {"F_completion": [], "F_asked": [], "S_star": [], "central_i": [central_i]},
            )
            entry["F_completion"].append(
                post[(cond["mode"], cond["k"])]["rate_finished_before_restart"]
            )
            entry["F_asked"].append(cond["fault_hit_rate"])
            s = crossing_s(cond["rates"], cond["bin_start_s"], cond["bin_s"], central_i, restart_s)[
                "S_star"
            ]
            entry["S_star"].append(-1.0 if s is None else s)
    return ref


def judge_container(values: Sequence[float], ref: Sequence[float]) -> str:
    """H-B1a：有効なラウンドの全てで F_on ≥ F30_max ＋ 0.15 なら UP，全てで F_on ≤ F30_min − 0.15 なら DOWN，それ以外は UNDECIDED．"""
    if all(v >= max(ref) + MARGIN_UP for v in values):
        return UP
    if all(v <= min(ref) - MARGIN_UP for v in values):
        return DOWN
    return UNDECIDED


def judge_availability(
    values_by_version: Mapping[str, Sequence[float]], refs: Mapping[str, Sequence[float]]
) -> dict[str, Any]:
    """H-B1b：有効なラウンドの全てで，聞いた時刻・終わった時刻の両方の版が |F_on − F30_mean| ≤ 0.10 なら SAME，それ以外は CHANGED（向きを書く）．"""
    diffs = {
        version: [v - statistics.mean(refs[version]) for v in values]
        for version, values in values_by_version.items()
    }
    same = all(abs(d) <= MARGIN_SAME for ds in diffs.values() for d in ds)
    flat = [d for ds in diffs.values() for d in ds if abs(d) > MARGIN_SAME]
    directions = [
        name
        for name, hit in (
            ("下がった向き", any(d < 0 for d in flat)),
            ("上がった向き", any(d > 0 for d in flat)),
        )
        if hit
    ]
    direction = None if same else "・".join(directions)
    return {"label": SAME if same else CHANGED, "direction": direction, "differences": diffs}


def render_report(verdict: Mapping[str, Any], conditions: Sequence[Mapping[str, Any]]) -> str:
    """analysis_report.md（判定・主の指標・ゲートの一覧）．"""

    def fmt(v: float | None, nd: int = 3) -> str:
        return "—" if v is None else f"{v:.{nd}f}"

    lines = [
        "# d0009 B1（Iteration 35）生存確認で止まったノードを外す仕組みの障害時の効果",
        "",
        f"有効なラウンド：{', '.join(verdict['valid_rounds']) or 'なし'}．採用：{verdict['adopted']}．"
        f"H-B1a の仮説の成立（container の {MIN_K_UP} つ以上の k で上がった）：{verdict['H_B1a_hypothesis_holds']}．",
        "",
        "## ゲート",
        "",
        f"```json\n{json.dumps(verdict['gates'], indent=1, ensure_ascii=False)}\n```",
        "",
        "## 主の指標（止めた間に答えられた割合．終わった時刻の版／聞いた時刻の版）",
        "",
        "| 止め方 | k | 判定 | F_on 終わった時刻（ラウンド） | F30 終わった時刻（ラウンド） | F_on 聞いた時刻 | F30 聞いた時刻 | 問の数 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in conditions:
        lines.append(
            f"| {c['mode']} | {c['k']} | {c['label']} | "
            f"{', '.join(fmt(v) for v in c['F_completion_on'])} | {', '.join(fmt(v) for v in c['F_completion_i30'])} | "
            f"{', '.join(fmt(v) for v in c['F_asked_on'])} | {', '.join(fmt(v) for v in c['F_asked_i30'])} | "
            f"{', '.join(str(v) for v in c['fault_n'])} |"
        )
    lines += [
        "",
        "## (D4) 窓の積分と境目 S*",
        "",
        "| 止め方 | k | I_P（ラウンド） | I_C | S*（ラウンド） | S*（Iteration 30） |",
        "|---|---|---|---|---|---|",
    ]
    for c in conditions:
        lines.append(
            f"| {c['mode']} | {c['k']} | {', '.join(fmt(v, 2) for v in c['I_P'])} | {fmt(c['I_C'], 2)} | "
            f"{', '.join(fmt(v, 2) for v in c['S_star_on'])} | {', '.join(fmt(v, 2) for v in c['S_star_i30'])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    """3 ラウンドを集計して verdict.json・b1_summary.json・analysis_report.md を書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default="results/d0009_b1_exclusion_r")
    parser.add_argument("--out-dir", default="results/d0009_b1_exclusion")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    ref = load_i30_reference()
    central_i = float(
        json.loads((I30_DIR / "verdict.json").read_text("utf-8"))["central_integral_assumed_45s"]
    )

    rounds: dict[str, Any] = {}
    for r in ROUNDS:
        run_dir = Path(f"{args.prefix}{r}")
        path = run_dir / "failure.json"
        if not path.exists():
            rounds[run_dir.name] = {"valid": False, "reason": "failure.json が無い"}
            continue
        failure = json.loads(path.read_text("utf-8"))
        gates = round_gates(run_dir, failure)
        restart_s = failure["central"]["restart"]["restart_to_answer_s"]
        valid = (
            gates["G_FP_i"]
            and gates["G_B"]
            and gates["G_R"]
            and gates["n_conditions"] == EXPECTED_CONDITIONS
        )
        conds = []
        for cond in failure["p2p"]:
            m = condition_metrics(cond, failure["params"], central_i, rng)
            start, bin_s = cond["bin_start_s"], cond["bin_s"]
            m["integral_w0"] = integrate_rates(cond["rates"], start, bin_s, 0.0, restart_s + POST_S)
            m["S_star"] = crossing_s(cond["rates"], start, bin_s, central_i, restart_s)["S_star"]
            del m["_hit_flags"]
            conds.append(m)
        rounds[run_dir.name] = {
            "valid": valid,
            "gates": gates,
            "window_s": failure["window_s"],
            "central_restart_to_answer_s": restart_s,
            "deadline_s": failure["params"]["answer_deadline_s"],
            "conditions": conds,
        }

    valid_runs = [k for k, v in rounds.items() if v["valid"]]
    conditions: list[dict[str, Any]] = []
    if valid_runs:
        keys = sorted((x["mode"], x["k"]) for x in rounds[valid_runs[0]]["conditions"])
        for mode, k in keys:
            per_round = [
                next(x for x in rounds[run]["conditions"] if (x["mode"], x["k"]) == (mode, k))
                for run in valid_runs
            ]
            r30 = ref[(mode, k)]
            on_end = [x["F_completion"] for x in per_round]
            on_asked = [x["F_asked"] for x in per_round]
            if mode == "container":
                label: Any = judge_container(on_end, r30["F_completion"])
            else:
                label = judge_availability(
                    {"completion": on_end, "asked": on_asked},
                    {"completion": r30["F_completion"], "asked": r30["F_asked"]},
                )
            i_p = [x["integral"] for x in per_round]
            i_p0 = [x["integral_w0"] for x in per_round]
            conditions.append(
                {
                    "mode": mode,
                    "k": k,
                    "label": label if isinstance(label, str) else label["label"],
                    "judgement": label,
                    "F_completion_on": on_end,
                    "F_asked_on": on_asked,
                    "F_completion_i30": r30["F_completion"],
                    "F_asked_i30": r30["F_asked"],
                    "F30_max_completion": max(r30["F_completion"]),
                    "F30_min_completion": min(r30["F_completion"]),
                    "F30_mean_completion": statistics.mean(r30["F_completion"]),
                    "fault_n": [x["fault_n"] for x in per_round],
                    "I_P": i_p,
                    "I_P_w0": i_p0,
                    "I_C": central_i,
                    "H_B1_window": three_way_integral(i_p, central_i),
                    "H_B1_window_w0": three_way_integral(i_p0, central_i),
                    "S_star_on": [x["S_star"] for x in per_round],
                    "S_star_i30": [None if s < 0 else s for s in r30["S_star"]],
                    "details_per_round": per_round,
                }
            )
    container_up = [c["k"] for c in conditions if c["mode"] == "container" and c["label"] == UP]
    availability_bad = [
        c["k"]
        for c in conditions
        if c["mode"] == "availability"
        and c["label"] == CHANGED
        and "下がった" in (c["judgement"]["direction"] or "")
    ]
    gates = {
        "G_T": "mise run check の結果は journal に記録（このスクリプトでは確かめない）",
        "G_TB_live_table_miss_total": sum(
            v["gates"]["n_table_miss"] for v in rounds.values() if "gates" in v
        ),
        "G_R_all_rounds": all(v["gates"]["G_R"] for v in rounds.values() if "gates" in v),
        "G_FP_ii_false_positive_drops_by_round": {
            k: v["gates"]["G_FP_ii_false_positive_drops"] for k, v in rounds.items() if "gates" in v
        },
        "G_FP_i_pre_stop_hit_rate_by_round": {
            k: v["gates"]["pre_stop_hit_rate"] for k, v in rounds.items() if "gates" in v
        },
        "n_valid_rounds": len(valid_runs),
    }
    hypothesis_holds = len(container_up) >= MIN_K_UP
    adopted = (
        gates["G_TB_live_table_miss_total"] == 0
        and gates["G_R_all_rounds"]
        and len(valid_runs) >= MIN_VALID_ROUNDS
        and hypothesis_holds
        and not availability_bad
    )
    verdict = {
        "iteration": 35,
        "gates": gates,
        "valid_rounds": valid_runs,
        "adopted": adopted,
        "H_B1a_hypothesis_holds": hypothesis_holds,
        "H_B1a_container": {
            str(c["k"]): c["label"] for c in conditions if c["mode"] == "container"
        },
        "H_B1b_availability": {
            str(c["k"]): c["label"] for c in conditions if c["mode"] == "availability"
        },
        "H_B1b_down_direction_ks": availability_bad,
        "conditions": [
            {k: v for k, v in c.items() if k != "details_per_round"} for c in conditions
        ],
        "note": "F は止めた間に聞いた問のうち答えられた割合．終わった時刻の版（再開の確認より前に終わった問）を主とする．",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verdict.json").write_text(
        json.dumps(verdict, indent=1, ensure_ascii=False), "utf-8"
    )
    (out_dir / "b1_summary.json").write_text(
        json.dumps({"rounds": rounds, "conditions": conditions}, indent=1, ensure_ascii=False),
        "utf-8",
    )
    (out_dir / "analysis_report.md").write_text(render_report(verdict, conditions), "utf-8")
    print(
        json.dumps(
            {
                "adopted": adopted,
                "H_B1a_container": verdict["H_B1a_container"],
                "H_B1a_hypothesis_holds": hypothesis_holds,
                "H_B1b_availability": verdict["H_B1b_availability"],
                "gates": gates,
            },
            indent=1,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
