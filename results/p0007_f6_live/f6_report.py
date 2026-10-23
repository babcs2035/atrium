"""p0007 F-6（cross-encoder を実機の経路に載せた費用）の集計：実行ごとの値・条件ごとの 3 回の範囲・判定（verdict_original）・選択の一致・照合を出す．

使い方（リポジトリの直下で．事前登録は journal の「p0007 F-6（事前登録）」）:
    uv run python results/p0007_f6_live/f6_report.py --out-dir results/p0007_f6_verdict
出力（--out-dir）:
    f6_runs.json      実行ごとの値（処理量・e2e の p50/p95・ce_s・GPU メモリ・他の使用の目安）
    f6_summary.json   条件 × parallel の 3 回の範囲・中央値，ce ÷ base の比，選択の一致，質問者へ渡る文書
    verdict.json      判定（verdict_original．F6-a・F6-b・総合）
    sim_vs_live.json  予測と実測の照合（§10）
メールの本文は読み込まない（数値と文書の ID だけを扱う）．
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Any

RESULTS = Path("results")
MAIN_PREFIX = "p0007_f6"
# 事前登録の判定の閾値（journal「p0007 F-6（事前登録）」）
F6A_CE_P50_MAX_S = 5.0
F6B_THROUGHPUT_RATIO_MIN = 0.80
# 事前登録の予測：1 問（15 組）のサービス時間の中心（A6 の select-features の実測 0.126 秒/組 × 15）と，採点の容量（問/秒）
PREDICTED_CE_SERVICE_S = 1.9
PREDICTED_CE_CAPACITY_QPS = 0.5
# 質問者の 8B の Ollama が使う GPU メモリ（A6 の記録．共存の目安）と，制御点の GPU の容量（MiB）
LLAMA_8B_MIB = 6700
GPU_TOTAL_MIB = 12288
# 実機と A6 の test のオフラインの得点を同点とみなす差（select_eval.CE_TIE_TOLERANCE と同じ）
ALL_EMAILS = 73772
GPU_LINE = re.compile(r"^(?P<power>[\d.]+) W, (?P<util>\d+) %, (?P<mem>\d+) MiB")
ACTIVE_UTIL_PERCENT = 5


def percentile(values: list[float], q: float) -> float:
    """線形補間のパーセンタイル（q は 0〜100）．"""
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100.0
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def span(values: list[float]) -> dict[str, float]:
    """3 回の最小・中央値・最大．"""
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def read_rows(run_dir: Path) -> list[dict[str, Any]]:
    """results.jsonl の行（同じ qid は最後の行）．"""
    rows: dict[str, dict[str, Any]] = {}
    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        rows[row["qid"]] = row
    return list(rows.values())


def gpu_memory(run_dir: Path) -> dict[str, Any] | None:
    """制御点の GPU の使用メモリの記録（gpu_mem_ctrl.csv）：安静時（最小）・最大・増分・プロセスごとの最大・使用率の平均．"""
    path = run_dir / "gpu_mem_ctrl.csv"
    if not path.exists():
        return None
    mem: list[float] = []
    util: list[float] = []
    per_process_peak = 0.0
    for line in path.read_text().splitlines():
        parts = line.split(",", 4)
        if len(parts) < 4 or not parts[1]:
            continue
        mem.append(float(parts[1]))
        util.append(float(parts[2]))
        if len(parts) == 5 and parts[4]:
            for item in parts[4].split(";"):
                _, _, value = item.partition(":")
                if value:
                    per_process_peak = max(per_process_peak, float(value))
    if not mem:
        return None
    return {
        "idle_mib": min(mem),
        "peak_mib": max(mem),
        "increase_mib": max(mem) - min(mem),
        "process_peak_mib": per_process_peak,
        "util_mean_percent": statistics.mean(util),
    }


def external_snapshot(path: Path) -> dict[str, Any] | None:
    """gpu_apps_snapshot の出力から，GPU の使用率が ACTIVE_UTIL_PERCENT 以上のホストの数と，ollama 以外のプロセスを持つホストの数．"""
    if not path.exists():
        return None
    active, hosts, host = 0, set(), ""
    for line in path.read_text().splitlines():
        if line.startswith("== "):
            host = line[3:]
        elif (m := GPU_LINE.match(line)) and int(m["util"]) >= ACTIVE_UTIL_PERCENT:
            active += 1
        elif line.startswith("pid=") and "llama-server" not in line:
            hosts.add(host)
    return {"active_hosts": active, "hosts_with_other_processes": len(hosts)}


def run_summary(run_dir: Path) -> dict[str, Any] | None:
    """1 実行の値（run_meta.json が無い・終わっていない実行は None）．"""
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    if "finished_at" not in meta:
        return None
    rows = read_rows(run_dir)
    ok = [r for r in rows if r.get("error") is None]
    window = meta["finished_at"] - meta["started_at"]
    e2e = [r["timings"]["e2e_s"] for r in ok]
    ce_s = [r["timings"]["ce_s"] for r in ok if "ce_s" in r["timings"]]
    owner = [r["owner_answer"] for r in ok if r.get("owner_answer")]
    received = [o["ce_docs_received"] for o in owner if "ce_docs_received" in o]
    out: dict[str, Any] = {
        "run": run_dir.name,
        "n": len(rows),
        "n_ok": len(ok),
        "failure_rate": 1 - len(ok) / len(rows) if rows else 1.0,
        "window_s": window,
        "throughput_qps": len(ok) / window,
        "e2e_p50_s": percentile(e2e, 50),
        "e2e_p95_s": percentile(e2e, 95),
        "gpu_mem_ctrl": gpu_memory(run_dir),
        "other_use_pre": external_snapshot(run_dir / "gpu_apps_pre.txt"),
        "other_use_post": external_snapshot(run_dir / "gpu_apps_post.txt"),
    }
    if ce_s:
        out["ce_s"] = {"p50": percentile(ce_s, 50), "p95": percentile(ce_s, 95), "mean": statistics.mean(ce_s)}
    if received:
        out["ce_docs_received_mean"] = statistics.mean(received)
    return out


def agreement(out: dict[str, Any]) -> None:
    """p0007_f6_agree の選択の一致（実機 対 オフラインの R3′）と，質問者へ渡った文書の重複なしの数．"""
    run_dir = RESULTS / "p0007_f6_agree"
    path = run_dir / "ce_live_agreement.json"
    if not path.exists():
        return
    out["selection_agreement"] = json.loads(path.read_text())
    rows = [r for r in read_rows(run_dir) if r.get("error") is None]
    # (ii) A6 の test のオフラインの R3′ の選択（回答の文には依らない）との一致
    a6_path = RESULTS / "p0007_a6_test_R3p" / "results.jsonl"
    if a6_path.exists():
        a6 = {j["qid"]: j for j in map(json.loads, a6_path.read_text().splitlines())}
        pairs = [
            (r["owner_answer"]["chosen_index"], a6[r["qid"]]["chosen_index"])
            for r in rows
            if r["qid"] in a6
        ]
        same_sources = sum(
            r["owner_answer"]["candidate_sources"][r["owner_answer"]["chosen_index"]]
            == a6[r["qid"]].get("chosen_source")
            for r in rows
            if r["qid"] in a6
        )
        out["agreement_with_a6_offline_r3p"] = {
            "n": len(pairs),
            "same_index": sum(a == b for a, b in pairs) / max(1, len(pairs)),
            "same_source": same_sources / max(1, len(pairs)),
            "n_different_source": len(pairs) - same_sources,
        }
    docs = {d for r in rows for c in r["owner_answer"].get("candidates") or [] for d in c["context_doc_ids"]}
    out["exposure_to_requester"] = {
        "distinct_docs_300q": len(docs),
        "share_of_all_emails_300q": len(docs) / ALL_EMAILS,
        "a6_test_distinct_docs_2939q": 27704,
        "a6_test_share": 27704 / ALL_EMAILS,
    }


def main() -> None:
    """全実行を集計して out-dir へ書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    # 感度分析（GPU が空いている時に流し直した p0007_f6c_*）では --prefix p0007_f6c --verdict-key sensitivity_posthoc を使う
    parser.add_argument("--prefix", default=MAIN_PREFIX)
    parser.add_argument("--verdict-key", default="verdict_original")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs: dict[str, dict[str, Any]] = {}
    run_pattern = re.compile(rf"{args.prefix}_(?P<cond>base|ce)_p(?P<parallel>\d+)_r(?P<rep>\d+)$")
    for run_dir in sorted(RESULTS.glob(f"{args.prefix}_*_p*_r*")):
        m = run_pattern.match(run_dir.name)
        summary = run_summary(run_dir)
        if m and summary:
            runs[run_dir.name] = {**summary, "cond": m["cond"], "parallel": int(m["parallel"]), "rep": int(m["rep"])}
    (out_dir / "f6_runs.json").write_text(json.dumps(runs, indent=1), encoding="utf-8")

    def of(cond: str, parallel: int) -> list[dict[str, Any]]:
        return sorted(
            (r for r in runs.values() if r["cond"] == cond and r["parallel"] == parallel),
            key=lambda r: r["rep"],
        )

    summary: dict[str, Any] = {"conditions": {}}
    for cond in ("base", "ce"):
        for parallel in (30, 1):
            group = of(cond, parallel)
            if not group:
                continue
            entry: dict[str, Any] = {
                "n_runs": len(group),
                "throughput_qps": span([r["throughput_qps"] for r in group]),
                "e2e_p50_s": span([r["e2e_p50_s"] for r in group]),
                "e2e_p95_s": span([r["e2e_p95_s"] for r in group]),
                "failure_rate_max": max(r["failure_rate"] for r in group),
            }
            if any(r["gpu_mem_ctrl"] for r in group):
                mems = [r["gpu_mem_ctrl"] for r in group if r["gpu_mem_ctrl"]]
                entry["gpu_mem_increase_mib"] = span([m["increase_mib"] for m in mems])
                entry["gpu_mem_peak_mib"] = span([m["peak_mib"] for m in mems])
                entry["gpu_mem_process_peak_mib"] = span([m["process_peak_mib"] for m in mems])
            if cond == "ce":
                entry["ce_docs_received_mean"] = statistics.mean(r["ce_docs_received_mean"] for r in group)
                pooled = [
                    r["timings"]["ce_s"]
                    for r_dir in (RESULTS / g["run"] for g in group)
                    for r in read_rows(r_dir)
                    if r.get("error") is None and "ce_s" in r["timings"]
                ]
                entry["ce_s_pooled"] = {"n": len(pooled), "p50": percentile(pooled, 50), "p95": percentile(pooled, 95)}
            summary["conditions"][f"{cond}_p{parallel}"] = entry
    base30, ce30 = of("base", 30), of("ce", 30)
    verdict: dict[str, Any] = {
        "note": f"{args.verdict_key}（事前登録の規則．精度は判定しない）"
        + ("" if args.verdict_key == "verdict_original" else "．事後の追加の感度分析（主の判定ではない）")
    }
    if base30 and ce30:
        ratio = statistics.median(r["throughput_qps"] for r in ce30) / statistics.median(
            r["throughput_qps"] for r in base30
        )
        by_rep = {
            rep: next(c for c in ce30 if c["rep"] == rep)["throughput_qps"]
            / next(b for b in base30 if b["rep"] == rep)["throughput_qps"]
            for rep in sorted({r["rep"] for r in ce30} & {r["rep"] for r in base30})
        }
        ce_p50 = summary["conditions"]["ce_p30"]["ce_s_pooled"]["p50"]
        f6a, f6b = ce_p50 <= F6A_CE_P50_MAX_S, ratio >= F6B_THROUGHPUT_RATIO_MIN
        disjoint = (
            min(r["throughput_qps"] for r in base30) > max(r["throughput_qps"] for r in ce30)
            or min(r["throughput_qps"] for r in ce30) > max(r["throughput_qps"] for r in base30)
        )
        summary["throughput_ratio_ce_over_base_p30"] = {"median_ratio": ratio, "by_rep": by_rep, "ranges_disjoint": disjoint}
        summary["e2e_difference_ce_minus_base_p30"] = {
            k: statistics.median(r[k] for r in ce30) - statistics.median(r[k] for r in base30)
            for k in ("e2e_p50_s", "e2e_p95_s")
        }
        verdict |= {
            "F6a_ce_s_p50_at_most_5s": {"value_s": ce_p50, "threshold_s": F6A_CE_P50_MAX_S, "holds": f6a},
            "F6b_throughput_ratio_at_least_0.80": {"value": ratio, "threshold": F6B_THROUGHPUT_RATIO_MIN, "holds": f6b},
            "overall": "small_cost" if f6a and f6b else ("partial" if f6a or f6b else "large_cost"),
        }
    if args.prefix == MAIN_PREFIX:
        agreement(summary)
    (out_dir / "f6_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out_dir / "verdict.json").write_text(json.dumps(verdict, indent=1), encoding="utf-8")

    sim: dict[str, Any] = {"selection_agreement": summary.get("selection_agreement"), "agreement_with_a6_offline_r3p": summary.get("agreement_with_a6_offline_r3p")}
    ce1 = summary["conditions"].get("ce_p1")
    if ce1 and "ce_s_pooled" in ce1:
        sim["ce_service_time_s"] = {"predicted": PREDICTED_CE_SERVICE_S, "live_p1_p50": ce1["ce_s_pooled"]["p50"]}
    if ce30 and "ce_p30" in summary["conditions"]:
        sim["throughput_ce_vs_capacity"] = {
            "predicted_capacity_qps": PREDICTED_CE_CAPACITY_QPS,
            "live_ce_p30_median_qps": summary["conditions"]["ce_p30"]["throughput_qps"]["median"],
        }
        if "gpu_mem_increase_mib" in summary["conditions"]["ce_p30"]:
            inc = summary["conditions"]["ce_p30"]["gpu_mem_increase_mib"]["median"]
            sim["coexistence_with_8b"] = {
                "ce_increase_mib": inc,
                "plus_llama_8b_mib": inc + LLAMA_8B_MIB,
                "gpu_total_mib": GPU_TOTAL_MIB,
                "fits": inc + LLAMA_8B_MIB <= GPU_TOTAL_MIB,
            }
    sim["exposure_to_requester"] = summary.get("exposure_to_requester")
    # 事後の感度分析（GPU が空いている時に流し直した p0007_f6c_*．--prefix p0007_f6c で先に集計しておく）があれば併記する
    clean_path = RESULTS / "p0007_f6_verdict_clean" / "f6_summary.json"
    if args.prefix == MAIN_PREFIX and clean_path.exists():
        clean = json.loads(clean_path.read_text())
        base_c, ce_c = clean["conditions"]["base_p30"], clean["conditions"]["ce_p30"]
        sim["sensitivity_clean_gpu_posthoc"] = {
            "throughput_base_qps": base_c["throughput_qps"]["median"],
            "throughput_ce_qps": ce_c["throughput_qps"]["median"],
            "ratio": clean["throughput_ratio_ce_over_base_p30"]["median_ratio"],
            "predicted_capacity_qps": PREDICTED_CE_CAPACITY_QPS,
            "ce_s_p50_s": ce_c["ce_s_pooled"]["p50"],
            "gpu_mem_increase_mib": ce_c["gpu_mem_increase_mib"]["median"],
        }
    (out_dir / "sim_vs_live.json").write_text(json.dumps(sim, indent=1), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "summary": summary["conditions"]}, indent=1))


if __name__ == "__main__":
    main()
