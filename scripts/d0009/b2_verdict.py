"""d0009 B2（Iteration 37）：採点を G 枚の GPU に分けたときの処理量の集計と，ゲート（G-S・G-CAL・有効な実行）の判定．

b2_chain.sh が呼ぶ．使い方（リポジトリの直下．GPU・実機は使わない．実行ごとの記録は results/d0009_b2_split_scoring_<name>/）:
    uv run python scripts/d0009/b2_verdict.py gs [--run results/d0009_b2_split_scoring_gs]
    uv run python scripts/d0009/b2_verdict.py cal                # 校正（G-CAL）：入れ子の順を results/d0009_b2_split_scoring/order.txt へ
    uv run python scripts/d0009/b2_verdict.py check --run <dir> --hosts wafl502,wafl503 [--smoke]
    uv run python scripts/d0009/b2_verdict.py orders / set-hosts --hosts a,b,...   # ラウンドごとの G の順／使える機と G の格子
    uv run python scripts/d0009/b2_verdict.py summary            # b2_summary.json・verdict.json・analysis_report.md
- gs（G-S）：scores.jsonl の全 4,320 組の得点が表 `ce_table/holdout_owner.jsonl` と |差| ≤ GS_TOLERANCE，かつ R3′ の選んだ候補が全問で表と一致
  （表の上位 2 候補の得点の差が GS_TIE_GAP 未満の問の食い違いは同点として許し，数を記録する）．
- check：実行の有効性．失敗の要求 0 件・安全の打ち切りなし・全ての機で SW Thermal Slowdown の累計の差 0・実行の間に Active の標本なし・
  温度の最大 < MAX_TEMP_C・他の研究者の GPU のプロセス 0（前後）．smoke では，加えて全ての GPU に要求が届くこと．
- summary：H-B2a・H-B2b・H-B2c の数値と機械的な判定（採否の評価は rc-evaluator が行う）．
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE = "d0009_b2_split_scoring"
RESULTS = Path("results")
RESULT_DIR = RESULTS / BASE
DEFAULT_HOSTS = [f"wafl50{i}" for i in range(2, 10)]
DEFAULT_GRID = [1, 2, 4, 8]
ROUNDS = [1, 2, 3]
GS_TOLERANCE = 1e-3
GS_TIE_GAP = 2e-3
CAL_BAND = 0.15
MAX_TEMP_C = 83.0
P2P_THROUGHPUT_QPS = 1.32
P2P_REFERENCE_QPS = 1.3205
E_EXTENDS = 0.85
E_NOT_EXTENDS = 0.70
GPUS_TOTAL = 10


def available_hosts() -> list[str]:
    """使える機（G-ENV0 が hosts.txt に書く．無ければ wafl502〜509 の 8 台）．"""
    path = RESULT_DIR / "hosts.txt"
    return path.read_text(encoding="utf-8").split() if path.exists() else DEFAULT_HOSTS


def grid() -> list[int]:
    """G の格子（機が 8 台に足りないときの置き換え．事前登録）．G-ENV0 が grid.txt に書く．無ければ 1・2・4・8．"""
    path = RESULT_DIR / "grid.txt"
    return (
        [int(g) for g in path.read_text(encoding="utf-8").split()]
        if path.exists()
        else list(DEFAULT_GRID)
    )


def round_orders(seed: int = 37) -> list[list[int]]:
    """ラウンドごとの G の順（種 37 の無作為な並べ替え．Iteration 29 と同じく 1 つの乱数列から続けて並べ替える）．"""
    rng = random.Random(seed)
    orders = []
    for _ in ROUNDS:
        order = grid()
        rng.shuffle(order)
        orders.append(order)
    return orders


def run_dir(name: str) -> Path:
    """実行の名前（gs・cal_<host>・smoke・r<round>_g<G>）から結果のディレクトリを返す．"""
    return RESULTS / f"{BASE}_{name}"


def read_json(path: Path) -> Any:
    """JSON を読む．"""
    return json.loads(path.read_text(encoding="utf-8"))


def read_snapshot(path: Path) -> dict[str, str]:
    """b2_gpu_snapshot.sh の出力（key=value の行）を読む．"""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key] = value
    return out


def read_gpu_csv(path: Path) -> list[dict[str, Any]]:
    """1 秒ごとの記録（epoch,温度,使用率,メモリ,SW Thermal Slowdown,HW Slowdown）を読む．壊れた行は飛ばす．"""
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(",")
        if len(parts) != 6:
            continue
        try:
            rows.append(
                {
                    "t": float(parts[0]),
                    "temp": float(parts[1]),
                    "util": float(parts[2]),
                    "mem": float(parts[3]),
                    "sw_active": parts[4] == "Active",
                    "hw_active": parts[5] == "Active",
                }
            )
        except ValueError:
            continue
    return rows


# ── G-S ──────────────────────────────────────────────────────────────────────


def gate_gs(run: Path, table_path: Path) -> dict[str, Any]:
    """G-S：採点の server を経由した得点が表と一致するか．"""
    table: dict[tuple[str, str], float] = {}
    for line in table_path.open(encoding="utf-8"):
        row = json.loads(line)
        table[(row["qid"], row["doc_id"])] = float(row["score"])
    remote: dict[str, dict[int, list[tuple[str, float, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    n_pairs = 0
    max_diff_by_target: dict[int, float] = defaultdict(float)
    max_diff = 0.0
    n_over = 0
    missing = 0
    for line in (run / "scores.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        n_pairs += 1
        remote[row["qid"]][row["candidate"]].append((row["doc_id"], row["score"], row["target"]))
        expected = table.get((row["qid"], row["doc_id"]))
        if expected is None:
            missing += 1
            continue
        diff = abs(row["score"] - expected)
        max_diff = max(max_diff, diff)
        max_diff_by_target[row["target"]] = max(max_diff_by_target[row["target"]], diff)
        n_over += diff > GS_TOLERANCE
    mismatched: list[dict[str, Any]] = []
    tolerated = 0
    for qid, cands in remote.items():
        remote_max = [max(s for _, s, _ in cands[c]) for c in sorted(cands)]
        table_max = [max(table[(qid, d)] for d, _, _ in cands[c]) for c in sorted(cands)]
        # 同点は候補の並びの先（choose_cross_encoder と同じ）
        pick_remote = max(range(len(remote_max)), key=lambda i: (remote_max[i], -i))
        pick_table = max(range(len(table_max)), key=lambda i: (table_max[i], -i))
        if pick_remote == pick_table:
            continue
        ordered = sorted(table_max, reverse=True)
        gap = ordered[0] - ordered[1] if len(ordered) > 1 else float("inf")
        if gap < GS_TIE_GAP:
            tolerated += 1
        else:
            mismatched.append(
                {"qid": qid, "table_pick": pick_table, "remote_pick": pick_remote, "gap": gap}
            )
    bench = read_json(run / "score_bench.json")
    result = {
        "n_pairs": n_pairs,
        "n_questions": len(remote),
        "n_pairs_missing_in_table": missing,
        "n_pairs_over_tolerance": n_over,
        "max_abs_diff": max_diff,
        "max_abs_diff_by_target": {str(k): v for k, v in sorted(max_diff_by_target.items())},
        "n_selection_mismatch": len(mismatched),
        "n_selection_mismatch_tolerated_as_tie": tolerated,
        "selection_mismatches": mismatched,
        "table_sha256": hashlib.sha256(table_path.read_bytes()).hexdigest(),
        "n_errors": bench["n_errors"],
        "tolerance": GS_TOLERANCE,
        "tie_gap": GS_TIE_GAP,
    }
    result["pass"] = (
        n_pairs == 4320
        and missing == 0
        and n_over == 0
        and not mismatched
        and bench["n_errors"] == 0
        and len(remote) == bench["n_questions_input"]
    )
    (run / "gs.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


# ── G-CAL ────────────────────────────────────────────────────────────────────


def gate_cal() -> dict[str, Any]:
    """G-CAL：8 台の x_h が中央値の ±15% に入るか．外れる機は入れ子の最後へ回す順を order.txt に書く．"""
    x_h = {
        h: read_json(run_dir(f"cal_{h}") / "score_bench.json")["throughput_qps"]
        for h in available_hosts()
    }
    median = statistics.median(x_h.values())
    outside = [h for h in available_hosts() if abs(x_h[h] / median - 1) > CAL_BAND]
    order = [h for h in available_hosts() if h not in outside] + outside
    result = {
        "x_h_qps": x_h,
        "median_qps": median,
        "band": CAL_BAND,
        "outside_band": outside,
        "order": order,
        "pass": not outside,
        "sum_x_h_qps": sum(x_h.values()),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "cal.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (RESULT_DIR / "order.txt").write_text(" ".join(order) + "\n", encoding="utf-8")
    return result


# ── 有効な実行 ───────────────────────────────────────────────────────────────


def check_run(run: Path, hosts: list[str], smoke: bool) -> dict[str, Any]:
    """1 回の実行の有効性を判定して valid.json に書く．"""
    reasons: list[str] = []
    bench_path = run / "score_bench.json"
    if (run / "aborted.txt").exists():
        reasons.append("safety abort: " + (run / "aborted.txt").read_text(encoding="utf-8").strip())
    if not bench_path.exists():
        reasons.append("no score_bench.json")
        bench: dict[str, Any] = {}
    else:
        bench = read_json(bench_path)
        if bench.get("n_errors", 1) != 0 or bench.get("n_request_errors_total", 1) != 0:
            reasons.append(
                f"failed requests (window questions {bench.get('n_errors')}, requests {bench.get('n_request_errors_total')})"
            )
        if smoke and any(n <= 0 for n in bench.get("requests_per_target_in_window", [0])):
            reasons.append(f"a GPU got no request: {bench.get('requests_per_target_in_window')}")
    per_host: dict[str, Any] = {}
    for host in hosts:
        info: dict[str, Any] = {}
        try:
            pre = read_snapshot(run / f"snap_{host}_pre.txt")
            post = read_snapshot(run / f"snap_{host}_post.txt")
            info["counter_delta_us"] = int(post["counter_us"]) - int(pre["counter_us"])
            info["other_procs_pre"] = int(pre["other_procs"])
            info["other_procs_post"] = int(post["other_procs"])
            if info["counter_delta_us"] != 0:
                reasons.append(
                    f"{host}: SW Thermal Slowdown counter advanced by {info['counter_delta_us']} us"
                )
            if info["other_procs_pre"] or info["other_procs_post"]:
                reasons.append(
                    f"{host}: other GPU processes (pre {pre['other_procs']}, post {post['other_procs']})"
                )
        except (OSError, KeyError, ValueError) as exc:
            reasons.append(f"{host}: unreadable snapshot ({type(exc).__name__}: {exc})")
        try:
            rows = read_gpu_csv(run / f"gpu_{host}.csv")
            info["n_gpu_samples"] = len(rows)
            info["max_temp_c"] = max((r["temp"] for r in rows), default=None)
            info["sw_thermal_active_samples"] = sum(r["sw_active"] for r in rows)
            info["hw_slowdown_active_samples"] = sum(r["hw_active"] for r in rows)
            if not rows:
                reasons.append(f"{host}: no GPU log")
            if info["max_temp_c"] is not None and info["max_temp_c"] >= MAX_TEMP_C:
                reasons.append(f"{host}: max temperature {info['max_temp_c']}")
            if info["sw_thermal_active_samples"] or info["hw_slowdown_active_samples"]:
                reasons.append(
                    f"{host}: slowdown Active samples {info['sw_thermal_active_samples']}/{info['hw_slowdown_active_samples']}"
                )
        except OSError as exc:
            reasons.append(f"{host}: unreadable GPU log ({exc})")
        per_host[host] = info
    result = {"valid": not reasons, "reasons": reasons, "hosts": per_host}
    (run / "valid.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


# ── 集計 ─────────────────────────────────────────────────────────────────────


def host_descriptives(run: Path, hosts: list[str], bench: dict[str, Any]) -> dict[str, Any]:
    """測定の区間の各機の GPU の使用率の平均・メモリ・温度の最大（1 秒ごとの記録）と要求の数．"""
    start, end = bench["window_start"], bench["window_end"]
    out: dict[str, Any] = {}
    for i, host in enumerate(hosts):
        rows = [r for r in read_gpu_csv(run / f"gpu_{host}.csv") if start <= r["t"] < end]
        out[host] = {
            "util_mean_pct": statistics.fmean(r["util"] for r in rows) if rows else None,
            "mem_max_mib": max((r["mem"] for r in rows), default=None),
            "temp_max_c": max((r["temp"] for r in rows), default=None),
            "requests_in_window": bench["requests_per_target_in_window"][i],
        }
    return out


def load_runs() -> dict[tuple[int, int], dict[str, Any]]:
    """（ラウンド，G）→ 有効な実行の集計（再実行があれば最初の有効なもの）．無効だった実行の理由も残す．"""
    order = (RESULT_DIR / "order.txt").read_text(encoding="utf-8").split()
    runs: dict[tuple[int, int], dict[str, Any]] = {}
    invalid: dict[str, list[str]] = {}
    for rnd in ROUNDS:
        for g in grid():
            for name in (f"r{rnd}_g{g}", f"r{rnd}_g{g}_retry"):
                path = run_dir(name)
                if not (path / "valid.json").exists():
                    continue
                valid = read_json(path / "valid.json")
                if not valid["valid"]:
                    invalid[name] = valid["reasons"]
                    continue
                if (rnd, g) in runs:
                    continue
                bench = read_json(path / "score_bench.json")
                runs[(rnd, g)] = {
                    "run": path.name,
                    "x_qps": bench["throughput_qps"],
                    "n_ok": bench["n_ok"],
                    "latency_p50_s": bench["latency_s"]["p50"],
                    "latency_p95_s": bench["latency_s"]["p95"],
                    "little_ratio": bench["little_ratio"],
                    "control_cpu_busy": bench["control_cpu_busy"],
                    "generator_process_cpu_util": bench["generator_process_cpu_util"],
                    "requests_per_target": bench["requests_per_target_in_window"],
                    "hosts": host_descriptives(path, order[:g], bench),
                }
    runs[(0, 0)] = {"invalid_runs": invalid}
    return runs


def summarize() -> dict[str, Any]:
    """H-B2a・H-B2b・H-B2c の数値と機械的な判定．"""
    runs = load_runs()
    invalid = runs.pop((0, 0))["invalid_runs"]
    cal = read_json(RESULT_DIR / "cal.json")
    by_g: dict[int, dict[str, Any]] = {}
    for g in grid():
        rounds = [r for r in ROUNDS if (r, g) in runs]
        row: dict[str, Any] = {
            "valid_rounds": rounds,
            "x_qps": {str(r): runs[(r, g)]["x_qps"] for r in rounds},
            "efficiency": {},
        }
        for r in rounds:
            if (r, 1) in runs:
                row["efficiency"][str(r)] = runs[(r, g)]["x_qps"] / (g * runs[(r, 1)]["x_qps"])
        row["x_qps_median"] = statistics.median(row["x_qps"].values()) if rounds else None
        row["sum_x_h_ratio"] = (
            row["x_qps_median"] / sum(cal["x_h_qps"][h] for h in cal["order"][:g])
            if rounds
            else None
        )
        row["p2p_upper_bound_qps"] = (
            min(P2P_THROUGHPUT_QPS * (GPUS_TOTAL - g) / GPUS_TOTAL, row["x_qps_median"])
            if rounds
            else None
        )
        by_g[g] = row
    enough_rounds = all(len(by_g[g]["valid_rounds"]) >= 2 for g in grid())

    def efficiencies(g: int) -> list[float]:
        return list(by_g[g]["efficiency"].values())

    e2, e4 = efficiencies(2), efficiencies(4)
    if e2 and e4 and min(e2 + e4) >= E_EXTENDS:
        h_b2a = "extends"
    elif e4 and max(e4) < E_NOT_EXTENDS:
        h_b2a = "does_not_extend"
    else:
        h_b2a = "undetermined"
    g_star = next(
        (
            g
            for g in grid()
            if by_g[g]["valid_rounds"]
            and all(x >= P2P_THROUGHPUT_QPS for x in by_g[g]["x_qps"].values())
        ),
        None,
    )
    summary = {
        "grid": grid(),
        "order": cal["order"],
        "calibration": cal,
        "by_g": {str(g): by_g[g] for g in grid()},
        "runs": {f"r{r}_g{g}": v for (r, g), v in sorted(runs.items())},
        "invalid_runs": invalid,
        "success_condition_2_enough_valid_rounds": enough_rounds,
        "H_B2a": h_b2a,
        "H_B2a_thresholds": {"extends": E_EXTENDS, "not_extends": E_NOT_EXTENDS},
        "H_B2b_g_star": g_star if g_star is not None else "limited_even_at_8",
        "H_B2b_threshold_qps": P2P_THROUGHPUT_QPS,
        "H_B2c": {
            "E_largest_g_by_round": by_g[max(grid())]["efficiency"],
            "control_cpu_busy_by_run": {
                k: v["control_cpu_busy"] for k, v in summary_runs(runs).items()
            },
            "note": "describe: control CPU saturation / GPU utilisation drop / request imbalance (see runs[*].hosts)",
        },
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "b2_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    verdict = {
        "H_B2a": h_b2a,
        "H_B2b_g_star": summary["H_B2b_g_star"],
        "success_condition_2_enough_valid_rounds": enough_rounds,
        "note": "mechanical application of the pre-registered rules; adoption is decided by rc-evaluator",
    }
    (RESULT_DIR / "verdict.json").write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    (RESULT_DIR / "analysis_report.md").write_text(render_report(summary), encoding="utf-8")
    return summary


def summary_runs(runs: dict[tuple[int, int], dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """（ラウンド，G）の添字を実行名に直す．"""
    return {f"r{r}_g{g}": v for (r, g), v in sorted(runs.items())}


def render_report(summary: dict[str, Any]) -> str:
    """G ごとの X(G)・E(G) の表（Markdown）．"""
    lines = [
        "# d0009 B2 採点を G 枚の GPU に分けたときの処理量（機械集計．採否は評価の担当）",
        "",
        f"入れ子の順: {' '.join(summary['order'])}",
        "",
        "| G | 有効なラウンド | X(G) 問/秒（ラウンドごと） | E(G)（ラウンドごと） | 中央値 X | Σx_h に対する比 | 分け合いの上限の見積もり |",
        "|---|---|---|---|---|---|---|",
    ]
    for g in grid():
        row = summary["by_g"][str(g)]
        xs = ", ".join(f"r{r}: {x:.3f}" for r, x in row["x_qps"].items())
        es = ", ".join(f"r{r}: {e:.3f}" for r, e in row["efficiency"].items())
        med = row["x_qps_median"]
        ratio = row["sum_x_h_ratio"]
        bound = row["p2p_upper_bound_qps"]
        lines.append(
            f"| {g} | {row['valid_rounds']} | {xs} | {es} | {'-' if med is None else f'{med:.3f}'} | "
            f"{'-' if ratio is None else f'{ratio:.3f}'} | {'-' if bound is None else f'{bound:.3f}'} |"
        )
    lines += [
        "",
        f"H-B2a: {summary['H_B2a']}　H-B2b（G*）: {summary['H_B2b_g_star']}",
        f"無効になった実行: {json.dumps(summary['invalid_runs'], ensure_ascii=False)}",
    ]
    return "\n".join(lines) + "\n"


def set_hosts(hosts: list[str]) -> list[int]:
    """使える機の列と G の格子を書く．8 台なら {1,2,4,8}，足りなければ {1,2,4,台数}（4 台未満なら {1,2,台数}．事前登録）．"""
    n = len(hosts)
    if n < 2:
        raise SystemExit(f"only {n} usable host(s); at least 2 are needed")
    new_grid = list(DEFAULT_GRID) if n >= 8 else sorted({1, 2, 4, n} if n >= 4 else {1, 2, n})
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "hosts.txt").write_text(" ".join(hosts) + "\n", encoding="utf-8")
    (RESULT_DIR / "grid.txt").write_text(" ".join(map(str, new_grid)) + "\n", encoding="utf-8")
    return new_grid


def main() -> int:
    """サブコマンドを実行し，結果を標準出力へ JSON で出す．gs・check は不合格のとき終了コード 1．"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("gs")
    p.add_argument("--run", default=str(run_dir("gs")))
    p.add_argument("--table", default="artifacts/enronqa/ce_table/holdout_owner.jsonl")
    sub.add_parser("cal")
    p = sub.add_parser("check")
    p.add_argument("--run", required=True)
    p.add_argument("--hosts", required=True)
    p.add_argument("--smoke", action="store_true")
    sub.add_parser("summary")
    sub.add_parser("orders")
    p = sub.add_parser("set-hosts")
    p.add_argument("--hosts", required=True)
    args = parser.parse_args()
    if args.cmd == "gs":
        result = gate_gs(Path(args.run), Path(args.table))
        print(
            json.dumps({k: v for k, v in result.items() if k != "selection_mismatches"}, indent=1)
        )
        return 0 if result["pass"] else 1
    if args.cmd == "cal":
        result = gate_cal()
        print(json.dumps(result, indent=1))
        return 0
    if args.cmd == "check":
        result = check_run(Path(args.run), args.hosts.split(","), args.smoke)
        print(json.dumps({"valid": result["valid"], "reasons": result["reasons"]}))
        return 0 if result["valid"] else 1
    if args.cmd == "orders":
        for order in round_orders():
            print(" ".join(map(str, order)))
        return 0
    if args.cmd == "set-hosts":
        print(" ".join(map(str, set_hosts(args.hosts.split(",")))))
        return 0
    print(json.dumps(summarize(), indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
