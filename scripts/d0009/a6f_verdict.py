"""d0009 A6 障害（Iteration 30）：主構成 P の障害の測定 3 ラウンドの集計・ゲート・判定（`results/d0009_a6_failure/verdict.json`・`failure_summary.json`）．

使い方（リポジトリの直下．実機は使わない．実行の後，commit の前に sanitize_results.py を通す）:
    uv run python scripts/d0009/a6f_verdict.py [--data-dir artifacts]
- 入力：`results/d0009_a6_failure_r{1,2,3}/`（failure.json・g_r_not_serving_after.txt・gpu_apps_*.txt）・`results/d0009_a6_failure/table/table_check.json`（G-TB）・
  `results/d0009_a6_c_r{1..4}/results.jsonl`（中央 C1-RRF-CE の上位 5 通．再起動後の割合 c）・`<data-dir>/enronqa/benchmark/holdout_gold.jsonl`・
  `results/p0007_e1_failure/failure.json`（並べるだけ．比べない）．
- P2P の積分 I_P は failure.json の `integral`（`integrate_rates`．窓 W_r = 45 ＋ 中央の再起動の実測 ＋ 30 秒）．中央の積分 I_C = c × 30（停止 45 秒の仮定．
  `central_rate_integral` と同じ）．H-A6F2 は窓 W0_r = 再起動の実測 ＋ 30 秒で I_P を同じ階段関数から積分し直し，中央は c × 30 と比べる．
- 判定の規則（journal の Iteration 30）：有効なラウンドの全てで I_P > I_C なら「P2P が上」，全てで I_P < I_C なら「中央が上」，それ以外は「決まらない」．
  `verdict_original` は 8 条件の一覧と，p0007 E1 と同じ全体の判定「全 8 条件で P2P が上（E1 成立）」の真偽．
統計の計算は，この repo に積分・比較の既存実装があるもの（`atrium.dynamics` の `integrate_rates`・`central_rate_integral`）をそのまま呼び，3 値の規則は事前登録のとおり書いた．
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from atrium.analysis import read_results
from atrium.dynamics import central_rate_integral, integrate_rates

OUT_DIR = Path("results/d0009_a6_failure")
ROUNDS = (1, 2, 3)
C_ROUNDS = (1, 2, 3, 4)
POST_S = 30.0
MIN_BASELINE_RETURNED = 0.95
MIN_VALID_ROUNDS = 2
EXPECTED_CONDITIONS = 8
# journal の Iteration 30 のオフラインの見込み（k = 1・3・6・10 の順．判定に使わない）
FORECAST_A = {1: 0.847, 3: 0.781, 6: 0.698, 10: 0.580}
FORECAST_B = {1: 0.622, 3: 0.490, 6: 0.243, 10: 0.170}
P2P_ABOVE = "P2P が上"
CENTRAL_ABOVE = "中央が上"
UNDECIDED = "決まらない"


def central_rate(data_dir: Path, c_round: int, questions: set[str]) -> dict[str, Any]:
    """Iteration 29 の中央 C1-RRF-CE の実行で，上位 5 通に正解のメールがある割合（H の 288 問）．"""
    gold = {
        g["qid"]: g["path"]
        for g in map(
            json.loads,
            (data_dir / "enronqa/benchmark/holdout_gold.jsonl").read_text("utf-8").splitlines(),
        )
    }
    rows = [r for r in read_results(Path(f"results/d0009_a6_c_r{c_round}/results.jsonl"))]
    rows = [r for r in rows if r["qid"] in questions]
    hits = [r.get("error") is None and gold[r["qid"]] in r.get("top_doc_ids", []) for r in rows]
    return {"run": f"d0009_a6_c_r{c_round}", "n": len(rows), "rate": sum(hits) / len(hits)}


def three_way(p2p: Sequence[float], central: Sequence[float]) -> str:
    """有効なラウンドの全てで P2P が上なら P2P_ABOVE，全てで中央が上なら CENTRAL_ABOVE，それ以外は UNDECIDED．"""
    if all(p > c for p, c in zip(p2p, central, strict=True)):
        return P2P_ABOVE
    if all(p < c for p, c in zip(p2p, central, strict=True)):
        return CENTRAL_ABOVE
    return UNDECIDED


def empty_bins(questions: Sequence[Mapping[str, Any]], bin_s: float, window_s: float) -> int:
    """窓 [0, window_s) の区間のうち，質問が 1 件も始まらなかった区間の数（その区間の割合は前の値で埋める）．"""
    n_bins = int(-(-window_s // bin_s))
    seen = {int(q["t_s"] // bin_s) for q in questions if 0 <= q["t_s"] < window_s}
    return n_bins - len(seen)


def condition_summary(row: Mapping[str, Any], window_s: float, restart_s: float) -> dict[str, Any]:
    """1 条件（止め方，k）の併記の値．"""
    owner = row["owner"]
    w0 = restart_s + POST_S
    return {
        "mode": row["mode"],
        "k": row["k"],
        "hosts": row["hosts"],
        "baseline_rates": owner["baseline"]["rates"],
        "baseline_n": owner["baseline"]["n"],
        "fault_rates": owner["fault"]["rates"],
        "fault_n": owner["fault"]["n"],
        "fault_failure_kinds": owner["fault"]["failure_kinds"],
        "fault_http_statuses": owner["fault"]["http_statuses"],
        "baseline_failure_kinds": owner["baseline"]["failure_kinds"],
        "integral": row["integral"],
        "integral_w0": integrate_rates(row["rates"], row["bin_start_s"], row["bin_s"], 0.0, w0),
        "detection_s_p50": row["detection_s_p50"],
        "ready_s_max": row["ready_s_max"],
        "first_failed_request_s": row["first_failed_request_s"],
        "n_timeouts": row["n_timeouts"],
        "n_questions": row["n_questions"],
        "empty_bins": empty_bins(row["questions"], row["bin_s"], window_s),
        "central_index_only_integral": row["central_integral"],
    }


def round_gates(run: str, failure: Mapping[str, Any]) -> dict[str, Any]:
    """ラウンドの有効性：8 条件を流し終え，平常の「D 以内に回答を返した割合」が基準以上で，30 台が動いている（G-B・G-R）．"""
    n = sum(r["owner"]["baseline"]["n"] for r in failure["p2p"])
    returned = sum(r["owner"]["baseline"]["n_returned"] for r in failure["p2p"])
    not_serving = Path(f"results/{run}/g_r_not_serving_after.txt")
    return {
        "n_conditions": len(failure["p2p"]),
        "baseline_returned_rate": returned / n if n else None,
        "G_B": bool(n and returned / n >= MIN_BASELINE_RETURNED),
        "G_R": not_serving.exists() and not_serving.read_text("utf-8").strip() == "none",
        "n_table_miss": sum(
            q.get("failure_kind") == "table_miss" for r in failure["p2p"] for q in r["questions"]
        ),
    }


def main() -> int:
    """3 ラウンドを集計して verdict.json・failure_summary.json を書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="artifacts")
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    # 質問集合は各受信箱の先頭 2 問（288 問）．中央の側は results の行の質問 ID で絞る
    first_two = set(_first_per_inbox(data_dir, 2))
    c_runs = [central_rate(data_dir, r, first_two) for r in C_ROUNDS]
    c = c_runs[0]["rate"]
    g_c = len({round(x["rate"], 12) for x in c_runs}) == 1

    table_check_path = OUT_DIR / "table/table_check.json"
    table_check = (
        json.loads(table_check_path.read_text("utf-8")) if table_check_path.exists() else {}
    )

    rounds: dict[str, Any] = {}
    for r in ROUNDS:
        run = f"d0009_a6_failure_r{r}"
        path = Path(f"results/{run}/failure.json")
        if not path.exists():
            rounds[run] = {"valid": False, "reason": "failure.json が無い"}
            continue
        failure = json.loads(path.read_text("utf-8"))
        gates = round_gates(run, failure)
        restart_s = failure["central"]["restart"]["restart_to_answer_s"]
        window_s = failure["window_s"]
        valid = gates["G_B"] and gates["G_R"] and gates["n_conditions"] == EXPECTED_CONDITIONS
        rounds[run] = {
            "valid": valid,
            "gates": gates,
            "deadline_s": failure["params"]["answer_deadline_s"],
            "window_s": window_s,
            "central": {
                "restart_to_answer_s": restart_s,
                "restart_to_ready_s": failure["central"]["restart"]["restart_to_ready_s"],
                "index_load": failure["central"]["restart"]["index_load"],
                "hit_rate_after_restart_index_only": failure["central"]["hit_rate_after_restart"],
            },
            "conditions": [condition_summary(p, window_s, restart_s) for p in failure["p2p"]],
        }

    valid_runs = [k for k, v in rounds.items() if v["valid"]]
    central_integral = central_rate_integral(
        0.0, c, POST_S
    )  # c × 30（停止 45 秒の仮定．窓のうち再起動の後の 30 秒）
    conditions: list[dict[str, Any]] = []
    if valid_runs:
        keys = [(x["mode"], x["k"]) for x in rounds[valid_runs[0]]["conditions"]]
        keys.sort(key=lambda mk: (mk[0], mk[1]))
        for mode, k in keys:
            per_round = [
                next(x for x in rounds[run]["conditions"] if (x["mode"], x["k"]) == (mode, k))
                for run in valid_runs
            ]
            i_p = [x["integral"] for x in per_round]
            i_p0 = [x["integral_w0"] for x in per_round]
            fault_hit = [x["fault_rates"]["hit"] for x in per_round]
            conditions.append(
                {
                    "mode": mode,
                    "k": k,
                    "H_A6F1": three_way(i_p, [central_integral] * len(i_p)),
                    "H_A6F2": three_way(i_p0, [central_integral] * len(i_p0)),
                    "I_P": i_p,
                    "I_P_w0": i_p0,
                    "I_C": central_integral,
                    "fault_hit_rate": fault_hit,
                    "H_A6F3": (
                        {
                            "forecast_a": FORECAST_A[k],
                            "forecast_b": FORECAST_B[k],
                            "closer_to": [
                                "a" if abs(h - FORECAST_A[k]) <= abs(h - FORECAST_B[k]) else "b"
                                for h in fault_hit
                                if h is not None
                            ],
                        }
                        if mode == "availability"
                        else None
                    ),
                    "I_P_range": [min(i_p), max(i_p)],
                }
            )
    f1 = {f"{x['mode']}:k={x['k']}": x["H_A6F1"] for x in conditions}
    f2 = {f"{x['mode']}:k={x['k']}": x["H_A6F2"] for x in conditions}
    gates = {
        "G_T": "mise run check の結果は journal に記録（このスクリプトでは確かめない）",
        "G_TB_table": table_check.get("G_TB_passed"),
        "G_TB_live_table_miss_total": sum(
            v["gates"]["n_table_miss"] for v in rounds.values() if "gates" in v
        ),
        "G_C_same_top5_across_c_rounds": g_c,
        "n_valid_rounds": len(valid_runs),
    }
    adopted = (
        gates["G_TB_table"] is True
        and gates["G_TB_live_table_miss_total"] == 0
        and len(valid_runs) >= MIN_VALID_ROUNDS
        and all(v["gates"]["G_R"] for v in rounds.values() if "gates" in v)
    )
    verdict = {
        "iteration": 30,
        "c": c,
        "c_runs": c_runs,
        "central_integral_assumed_45s": central_integral,
        "gates": gates,
        "valid_rounds": valid_runs,
        "adopted": adopted,
        "verdict_original": {
            "H_A6F1": f1,
            "H_A6F1_all_eight_p2p_above": bool(f1)
            and len(f1) == EXPECTED_CONDITIONS
            and all(v == P2P_ABOVE for v in f1.values()),
            "H_A6F2_reference_only": f2,
            "H_A6F2_all_eight_p2p_above": bool(f2)
            and len(f2) == EXPECTED_CONDITIONS
            and all(v == P2P_ABOVE for v in f2.values()),
        },
        "conditions": conditions,
        "note": "P2P の積分は failure.json の integral．中央の積分は c × 30（H-A6F2 は検出と同時の再起動）．中央の生成が D 以内に終わることは仮定（中央に有利）．",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "verdict.json").write_text(
        json.dumps(verdict, indent=1, ensure_ascii=False), "utf-8"
    )
    p0007 = Path("results/p0007_e1_failure/failure.json")
    reference = (
        {
            "window_s": json.loads(p0007.read_text("utf-8"))["window_s"],
            "rows": [
                {k: r[k] for k in ("mode", "k", "baseline_hit_rate", "fault_hit_rate", "integral")}
                for r in json.loads(p0007.read_text("utf-8"))["p2p"]
            ],
            "note": "構成・質問・定義が違うので比べない",
        }
        if p0007.exists()
        else None
    )
    restarts = [v["central"]["restart_to_answer_s"] for v in rounds.values() if "central" in v]
    (OUT_DIR / "failure_summary.json").write_text(
        json.dumps(
            {
                "rounds": rounds,
                "restart_to_answer_s_median": statistics.median(restarts) if restarts else None,
                "p0007_e1_reference": reference,
            },
            indent=1,
            ensure_ascii=False,
        ),
        "utf-8",
    )
    print(
        json.dumps(
            {"adopted": adopted, "H_A6F1": f1, "H_A6F2": f2, "gates": gates},
            indent=1,
            ensure_ascii=False,
        )
    )
    return 0


def _first_per_inbox(data_dir: Path, limit: int) -> list[str]:
    """holdout_questions.jsonl の受信箱ごとの先頭 limit 問の質問 ID（`load_questions` の limit と同じ数え方）．"""
    taken: dict[str, int] = {}
    out: list[str] = []
    for line in (
        (data_dir / "enronqa/benchmark/holdout_questions.jsonl").read_text("utf-8").splitlines()
    ):
        row = json.loads(line)
        if taken.get(row["bank"], 0) < limit:
            taken[row["bank"]] = taken.get(row["bank"], 0) + 1
            out.append(row["qid"])
    return out


if __name__ == "__main__":
    raise SystemExit(main())
