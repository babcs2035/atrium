"""p0007 D（負荷・エネルギー）の集計：実行ごとの値・条件 × parallel の 3 回の範囲・判定（verdict_original）・K3/K4 の照合・K5 のエネルギーを出す．

使い方（リポジトリの直下で．事前登録は journal の「p0007 D（事前登録）」）:
    uv run python results/p0007_d_live/d_report.py --out-dir results/p0007_d_verdict [--glob 'results/p0007_d_*_p*_r[123]']
出力（--out-dir）:
    d_runs.json      実行ごとの値（処理量・p50・p95・decode/prefill の秒/トークン・エネルギー・他の使用の疑い）
    d_summary.json   条件 × parallel の 3 回の範囲・中央値，ブロックごとの比
    verdict.json     判定（verdict_original．L1・L1'・L2・p95）
    sim_vs_live.json K3（GPU の台数に対する伸び）・K4（予測と実測）
    d_tables.md      上の要約の表
メールの本文は読み込むが保存しない（数値の列だけを扱う）．
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from atrium.energy import EnergySample, integrate_gpu_joules, parse_energy_csv  # noqa: E402

CONDITIONS = ("central", "central_norrf", "semi", "p2p", "p2p_ref")
PARALLELS = (1, 4, 8, 16, 30)
RUN_PATTERN = re.compile(r"p0007_dc?_(?P<cond>[a-z0-9_]+?)_p(?P<parallel>\d+)_[rc](?P<rep>\d+)$")
# 制御点の GPU（生成に使う条件）と，GPU の専門家のホスト（192.168.15.100〜109 = wafl500〜509）
CONTROL_HOST = "wafl-ctrl5"
# 生成に使う GPU の台（energy の併記 (ii)）
USED_GPU_HOSTS = {
    "central": "control",
    "central_norrf": "control",
    "p2p_ref": "control",
    "semi": "experts",
    "p2p": "experts",
}
# アイドルの電力の判定：この値を超える GPU があった実行を「他の使用の疑い」とする（3060 のアイドルは 10〜20 W）
IDLE_SUSPECT_W = 40.0
# アイドルの標本から外す，実行の開始・終了の前後の余裕（秒）
IDLE_MARGIN_S = 5.0
# 失敗がこの割合を超えた実行は範囲に入れない（事前登録）
MAX_FAILURE_RATE = 0.10
# 予測の当たりの基準（p0006 H-P4a と同じ ±20%）
PREDICTION_TOLERANCE = 0.20
# GPU の専門家の数と，P2P の 1 問あたりの生成回数・要求数（事前登録の予測の式の入力）
N_GPU_NODES = 10
P2P_GENERATIONS = 3
REQUESTS_P2P = 27
REQUESTS_P2P_REF = 18


def percentile(values: list[float], q: float) -> float:
    """線形補間のパーセンタイル（q は 0〜100）．numpy の既定と同じ."""
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100.0
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def generation_usages(record: dict[str, Any]) -> list[dict[str, Any]]:
    """1 問の記録から，生成 1 回ごとの計測値（output_tokens・prompt_tokens・prefill_s・decode_s）の列を取り出す."""
    if record.get("node_answers"):
        return [a for a in record["node_answers"] if a.get("output_tokens")]
    llm = record.get("llm")
    if llm and record.get("output_tokens"):
        return [
            {
                "output_tokens": record["output_tokens"],
                "prompt_tokens": record["prompt_tokens"],
                **llm,
            }
        ]
    return []


def external_gpu_hosts(snapshot: Path) -> int | None:
    """gpu_apps_snapshot.sh の出力から，ollama（llama-server）以外の GPU プロセスを持つホストの数（ファイルが無ければ None）."""
    if not snapshot.exists():
        return None
    hosts: set[str] = set()
    host = ""
    for line in snapshot.read_text().splitlines():
        if line.startswith("== "):
            host = line[3:]
        elif line.startswith("pid=") and "llama-server" not in line:
            hosts.add(host)
    return len(hosts)


GPU_LINE = re.compile(r"^(?P<power>[\d.]+) W, (?P<util>\d+) %, (?P<mem>\d+) MiB")
# スナップショットの時点で GPU の使用率がこの値以上なら「使われている」（実験の質問者の処理の外の時刻なので，自分の実験ではない）
ACTIVE_UTIL_PERCENT = 5


def external_active_hosts(snapshot: Path) -> int | None:
    """gpu_apps_snapshot.sh の出力から，GPU の使用率が ACTIVE_UTIL_PERCENT 以上のホストの数（実験の処理の外の時刻の他の使用の目安．ファイルが無ければ None）."""
    if not snapshot.exists():
        return None
    return sum(
        1
        for line in snapshot.read_text().splitlines()
        if (m := GPU_LINE.match(line)) and int(m["util"]) >= ACTIVE_UTIL_PERCENT
    )


# DETR（他の利用者の学習）が始まる前の，何も動いていない GPU のアイドル電力を取った実行（煙試験）．推定の基準に使う
IDLE_REFERENCE_RUN = Path("results/p0007_d_smoke2_semi_p30_r0")


def reference_idle_w() -> dict[str, float]:
    """IDLE_REFERENCE_RUN の開始前の標本から，ホストごとのアイドル電力（中央値．W）."""
    meta = json.loads((IDLE_REFERENCE_RUN / "run_meta.json").read_text())
    out: dict[str, float] = {}
    for path in sorted((IDLE_REFERENCE_RUN / "energy").glob("*.csv")):
        samples, _ = parse_energy_csv(path.read_text())
        values = [x.gpu_w for x in samples if x.gpu_w is not None and x.unix_time < meta["started_at"] - IDLE_MARGIN_S]
        out[path.stem] = statistics.median(values)
    return out


def run_energy(run_dir: Path, started_at: float, finished_at: float, n_ok: int, cond: str) -> dict[str, Any] | None:
    """実行の区間 [started_at, finished_at] の GPU のエネルギー（11 台）と前後のアイドル電力．記録が無ければ None."""
    csv_dir = run_dir / "energy"
    paths = sorted(csv_dir.glob("*.csv")) if csv_dir.exists() else []
    if not paths:
        return None
    hosts: dict[str, dict[str, float | None]] = {}
    for path in paths:
        samples, _ = parse_energy_csv(path.read_text())
        window = [s for s in samples if started_at <= s.unix_time <= finished_at]
        idle = [
            s.gpu_w
            for s in samples
            if s.gpu_w is not None
            and (s.unix_time < started_at - IDLE_MARGIN_S or s.unix_time > finished_at + IDLE_MARGIN_S)
        ]
        hosts[path.stem] = {
            "joules": integrate_gpu_joules(window),
            "window_s": (window[-1].unix_time - window[0].unix_time) if len(window) >= 2 else None,
            "idle_w": statistics.median(idle) if idle else None,
            "mean_w": statistics.fmean([s.gpu_w for s in window if s.gpu_w is not None]) if window else None,
        }
    used = [h for h in hosts if (h == CONTROL_HOST) == (USED_GPU_HOSTS[cond] == "control")]
    measured = [h for h in hosts.values() if h["joules"] is not None]
    gross = sum(h["joules"] for h in measured)  # type: ignore[misc]
    duration = finished_at - started_at
    idle_total_w = sum(h["idle_w"] or 0.0 for h in hosts.values())
    used_gross = sum(hosts[h]["joules"] or 0.0 for h in used)
    used_idle_w = sum(hosts[h]["idle_w"] or 0.0 for h in used)
    reference = reference_idle_w()
    used_reference_w = sum(reference[h] for h in used)
    return {
        # 外部の使用の影響を除いた推定：使う GPU の「アイドル差引」の増分 + 基準のアイドル電力 × 区間 ÷ 問の数（net は前後のアイドルが他の使用を含むので揺れる）
        "estimated_gross_joules_per_question_used_gpu": (
            (used_gross - used_idle_w * duration + used_reference_w * duration) / n_ok if n_ok else None
        ),
        "reference_idle_w_used_gpu": used_reference_w,
        "n_hosts": len(hosts),
        "gross_joules_11gpu": gross,
        "gross_joules_per_question_11gpu": gross / n_ok if n_ok else None,
        "idle_w_11gpu": idle_total_w,
        "net_joules_per_question_11gpu": (gross - idle_total_w * duration) / n_ok if n_ok else None,
        "used_gpu_hosts": sorted(used),
        "gross_joules_per_question_used_gpu": used_gross / n_ok if n_ok else None,
        "net_joules_per_question_used_gpu": (used_gross - used_idle_w * duration) / n_ok if n_ok else None,
        "mean_power_w_11gpu": sum(h["mean_w"] or 0.0 for h in hosts.values()),
        "idle_suspect_hosts": sorted(h for h, v in hosts.items() if (v["idle_w"] or 0.0) > IDLE_SUSPECT_W),
    }


def summarize_run(run_dir: Path) -> dict[str, Any] | None:
    """1 回の実行の値（処理量・e2e・decode/prefill・エネルギー）．run_meta か results が無ければ None."""
    match = RUN_PATTERN.match(run_dir.name)
    meta_path = run_dir / "run_meta.json"
    if match is None or not meta_path.exists() or "finished_at" not in json.loads(meta_path.read_text()):
        return None
    meta = json.loads(meta_path.read_text())
    records = [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines() if line]
    ok = [r for r in records if r.get("error") is None]
    window_s = meta["finished_at"] - meta["started_at"]
    usages = [u for r in ok for u in generation_usages(r)]
    out_tokens = sum(u["output_tokens"] for u in usages)
    prompt_tokens = sum(u["prompt_tokens"] for u in usages)
    e2e = [r["timings"]["e2e_s"] for r in ok]
    cond = match["cond"]
    return {
        "run_id": run_dir.name,
        "cond": cond,
        "parallel": int(match["parallel"]),
        "rep": int(match["rep"]),
        "n_questions": meta["n_questions"],
        "n_ok": len(ok),
        "n_failed": len(records) - len(ok),
        "failure_rate": (meta["n_questions"] - len(ok)) / meta["n_questions"],
        "resumed": meta.get("n_skipped_on_resume", 0) > 0,
        "window_s": window_s,
        "throughput_qps": len(ok) / window_s,
        "e2e_p50_s": percentile(e2e, 50) if e2e else None,
        "e2e_p95_s": percentile(e2e, 95) if e2e else None,
        "n_generations": len(usages),
        "generations_per_question": len(usages) / len(ok) if ok else None,
        "decode_s_per_token": sum(u["decode_s"] for u in usages) / out_tokens if out_tokens else None,
        "prefill_s_per_token": sum(u["prefill_s"] for u in usages) / prompt_tokens if prompt_tokens else None,
        "mean_requests_total": statistics.fmean(
            [r["owner_answer"]["n_requests_total"] for r in ok if "owner_answer" in r]
            or [REQUESTS_P2P_REF if cond == "p2p_ref" else 0]
        ),
        "external_active_hosts_pre": external_active_hosts(run_dir / "gpu_apps_pre.txt"),
        "external_active_hosts_post": external_active_hosts(run_dir / "gpu_apps_post.txt"),
        "external_gpu_hosts_pre": external_gpu_hosts(run_dir / "gpu_apps_pre.txt"),
        "external_gpu_hosts_post": external_gpu_hosts(run_dir / "gpu_apps_post.txt"),
        "energy": run_energy(run_dir, meta["started_at"], meta["finished_at"], len(ok), cond),
        "started_at": meta["started_at"],
    }


def reps_of(runs: list[dict[str, Any]], cond: str, parallel: int) -> list[dict[str, Any]]:
    """条件 × parallel の，範囲に入れる実行（失敗が 10% 以下．再開した実行は除く）を rep の順に返す."""
    return sorted(
        (
            r
            for r in runs
            if r["cond"] == cond
            and r["parallel"] == parallel
            and r["failure_rate"] <= MAX_FAILURE_RATE
            and not r["resumed"]
        ),
        key=lambda r: r["rep"],
    )


def span(values: list[float]) -> dict[str, float] | None:
    """3 回の範囲（最小・中央値・最大）．空なら None."""
    if not values:
        return None
    return {"min": min(values), "median": statistics.median(values), "max": max(values), "n": len(values)}


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """条件 × parallel ごとに，指標の範囲を出す."""
    table: dict[str, Any] = {}
    for cond in CONDITIONS:
        for parallel in PARALLELS:
            group = reps_of(runs, cond, parallel)
            if not group:
                continue
            table.setdefault(cond, {})[str(parallel)] = {
                "throughput_qps": span([r["throughput_qps"] for r in group]),
                "e2e_p50_s": span([r["e2e_p50_s"] for r in group]),
                "e2e_p95_s": span([r["e2e_p95_s"] for r in group]),
                "decode_s_per_token": span([r["decode_s_per_token"] for r in group if r["decode_s_per_token"]]),
                "prefill_s_per_token": span([r["prefill_s_per_token"] for r in group if r["prefill_s_per_token"]]),
                "gross_j_per_q_11gpu": span(
                    [r["energy"]["gross_joules_per_question_11gpu"] for r in group if r["energy"]]
                ),
                "net_j_per_q_11gpu": span([r["energy"]["net_joules_per_question_11gpu"] for r in group if r["energy"]]),
                "gross_j_per_q_used_gpu": span(
                    [r["energy"]["gross_joules_per_question_used_gpu"] for r in group if r["energy"]]
                ),
                "net_j_per_q_used_gpu": span(
                    [r["energy"]["net_joules_per_question_used_gpu"] for r in group if r["energy"]]
                ),
                "est_gross_j_per_q_used_gpu": span(
                    [r["energy"]["estimated_gross_joules_per_question_used_gpu"] for r in group if r["energy"]]
                ),
                "idle_suspect_runs": [r["run_id"] for r in group if r["energy"] and r["energy"]["idle_suspect_hosts"]],
                "runs": [r["run_id"] for r in group],
            }
    return table


def ranges_disjoint(a: dict[str, float] | None, b: dict[str, float] | None) -> str:
    """3 回の範囲が重ならないときだけ差がある．a が b より上なら 'above'，下なら 'below'，重なれば 'overlap'（欠測は 'missing'）."""
    if a is None or b is None:
        return "missing"
    if a["min"] > b["max"]:
        return "above"
    if a["max"] < b["min"]:
        return "below"
    return "overlap"


def metric(table: dict[str, Any], cond: str, parallel: int, key: str) -> dict[str, float] | None:
    """summarize の表から範囲を引く（無ければ None）."""
    return table.get(cond, {}).get(str(parallel), {}).get(key)


def judge_load(table: dict[str, Any]) -> dict[str, Any]:
    """事前登録の判定（L1・L1'・L2・p95）．verdict_original の中身."""
    p = 30
    thr = lambda c: metric(table, c, p, "throughput_qps")  # noqa: E731
    p95 = lambda c: metric(table, c, p, "e2e_p95_s")  # noqa: E731
    l1 = ranges_disjoint(thr("p2p"), thr("central"))
    l2 = ranges_disjoint(thr("semi"), thr("p2p"))
    p95_rel = ranges_disjoint(p95("p2p"), p95("central"))  # 'below' なら p2p の p95 が中央より短い
    if l2 == "overlap" and l1 == "above":
        wording = "負荷の優位は分散の構造ではなく GPU の台数による（準集権が P2P と同程度）"
    elif l2 == "above":
        wording = "準集権は P2P より処理量が高い（P2P は 1 問あたり 3 回生成する分の費用を払う）"
    elif l2 == "below":
        wording = "P2P の方が準集権より処理量が高い"
    else:
        wording = "L1・L2 のどちらかが欠測のため書き分けない"
    return {
        "hypothesis": "負荷の優位：parallel 30 で P2P（bm25_probe・owner_answer）の処理量の 3 回の範囲が中央（central_local・B1・rrf）より上で重ならない",
        "L1_p2p_vs_central_throughput": l1,
        "L1_load_advantage": l1 == "above",
        "L1_prime_p2p_ref_vs_central_throughput": ranges_disjoint(thr("p2p_ref"), thr("central")),
        "L1_prime_p2p_vs_p2p_ref_throughput": ranges_disjoint(thr("p2p"), thr("p2p_ref")),
        "p95_p2p_vs_central": p95_rel,
        "p95_p2p_advantage": p95_rel == "below",
        "L2_semi_vs_p2p_throughput": l2,
        "L2_semi_vs_central_throughput": ranges_disjoint(thr("semi"), thr("central")),
        "writing": wording,
        "ranges_parallel_30": {c: {"throughput_qps": thr(c), "e2e_p95_s": p95(c)} for c in CONDITIONS},
    }


def block_ratios(runs: list[dict[str, Any]], num: str, den: str, parallel: int) -> list[dict[str, float]]:
    """同じブロック（rep）の中の処理量の比 num/den（時間帯の揺れに強い比較）."""
    out = []
    for rep in sorted({r["rep"] for r in runs}):
        a = [r for r in reps_of(runs, num, parallel) if r["rep"] == rep]
        b = [r for r in reps_of(runs, den, parallel) if r["rep"] == rep]
        if a and b:
            out.append({"rep": rep, "ratio": a[0]["throughput_qps"] / b[0]["throughput_qps"]})
    return out


def within_tolerance(actual: float, predicted: float) -> bool:
    """実測が予測の ±20% に入るか."""
    return abs(actual / predicted - 1.0) <= PREDICTION_TOLERANCE


def sim_vs_live(table: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    """K3（GPU の台数に対する伸び）と K4（予測と実測）．予測の式は事前登録のとおり."""
    k3: dict[str, Any] = {}
    k4_rows: list[dict[str, Any]] = []
    for parallel in PARALLELS:
        central = metric(table, "central", parallel, "throughput_qps")
        semi = metric(table, "semi", parallel, "throughput_qps")
        p2p = metric(table, "p2p", parallel, "throughput_qps")
        ref = metric(table, "p2p_ref", parallel, "throughput_qps")
        if central is None:
            continue
        t_c = central["median"]
        if semi:
            k3[str(parallel)] = {
                "semi_over_central": semi["median"] / t_c,
                "efficiency_vs_10_gpus": semi["median"] / t_c / N_GPU_NODES,
                "same_block_ratios": block_ratios(runs, "semi", "central", parallel),
            }
        for cond, actual, predicted in (
            ("semi", semi, N_GPU_NODES * t_c),
            ("p2p", p2p, N_GPU_NODES * t_c / P2P_GENERATIONS),
            ("p2p_ref", ref, t_c),
        ):
            if actual is None:
                continue
            k4_rows.append(
                {
                    "cond": cond,
                    "parallel": parallel,
                    "model": "generation_bound",
                    "predicted_qps": predicted,
                    "actual_qps_median": actual["median"],
                    "actual_over_predicted": actual["median"] / predicted,
                    "within_20pct": within_tolerance(actual["median"], predicted),
                }
            )
        if p2p and ref:
            ratio = p2p["median"] / ref["median"]
            for model, predicted_ratio in (
                ("generation_bound", N_GPU_NODES / P2P_GENERATIONS),
                ("request_count_inverse", REQUESTS_P2P_REF / REQUESTS_P2P),
            ):
                k4_rows.append(
                    {
                        "cond": "p2p_over_p2p_ref",
                        "parallel": parallel,
                        "model": model,
                        "predicted_ratio": predicted_ratio,
                        "actual_ratio": ratio,
                        "actual_over_predicted": ratio / predicted_ratio,
                        "within_20pct": within_tolerance(ratio, predicted_ratio),
                        "same_block_ratios": block_ratios(runs, "p2p", "p2p_ref", parallel),
                    }
                )
    drift: dict[str, Any] = {}
    for cond in CONDITIONS:
        values = [r["decode_s_per_token"] for r in runs if r["cond"] == cond and r["decode_s_per_token"]]
        if values:
            drift[cond] = {
                "decode_s_per_token_min": min(values),
                "decode_s_per_token_max": max(values),
                "max_over_min": max(values) / min(values),
            }
    return {"K3_gpu_scaling": k3, "K4_predictions": k4_rows, "decode_drift_by_condition": drift}


OFFLINE_SELECTION = Path("results/p0007_a4_build_summary/offline_selection.json")
# 実機の記録の選択（候補の受信箱）のフィールドと，オフラインの選択のキー
SELECTION_CHECKS = {"p2p_ref": ("selected_sources", "hybrid_probe"), "p2p": ("owner_answer.candidate_sources", "bm25_probe_m3")}


def selection_agreement(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """実機の選択（p2p_ref の selected_sources・p2p の候補）とオフラインの選択（A4 の offline_selection.json）の集合の一致（退行ゲート R5 の実機版）."""
    if not OFFLINE_SELECTION.exists():
        return {}
    offline = json.loads(OFFLINE_SELECTION.read_text())
    out: dict[str, Any] = {}
    for cond, (field, key) in SELECTION_CHECKS.items():
        same = total = 0
        for run in (r for r in runs if r["cond"] == cond):
            for line in (Path("results") / run["run_id"] / "results.jsonl").read_text().splitlines():
                record = json.loads(line)
                if record.get("error") is not None or record["qid"] not in offline[key]:
                    continue
                selected = record["owner_answer"]["candidate_sources"] if cond == "p2p" else record[field]
                total += 1
                same += set(selected) == set(offline[key][record["qid"]])
        out[cond] = {"offline_key": key, "n": total, "same": same, "agreement": same / total if total else None}
    return out


def external_gpu_use(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """条件ごとに，実行の前後のスナップショットで他の利用者の GPU の使用（使用率 5% 以上のホストがある）があった実行の数（記録の無い実行は別に数える）."""
    out: dict[str, Any] = {}
    for cond in CONDITIONS:
        group = [r for r in runs if r["cond"] == cond]
        known = [r for r in group if r["external_active_hosts_pre"] is not None]
        out[cond] = {
            "n_runs": len(group),
            "n_with_snapshot": len(known),
            "n_active_at_pre_or_post": sum(
                1 for r in known if (r["external_active_hosts_pre"] or 0) + (r["external_active_hosts_post"] or 0) > 0
            ),
            "n_idle_power_suspect": sum(1 for r in group if r["energy"] and r["energy"]["idle_suspect_hosts"]),
        }
    return out


def fmt_span(s: dict[str, float] | None, digits: int = 3) -> str:
    """範囲の表示（中央値 [最小〜最大]）."""
    if s is None:
        return "-"
    return f"{s['median']:.{digits}f} [{s['min']:.{digits}f}〜{s['max']:.{digits}f}]"


def render_tables(table: dict[str, Any]) -> str:
    """条件 × parallel の表（Markdown）."""
    lines = ["| 条件 | parallel | 処理量（問/秒） | e2e p50（秒） | e2e p95（秒） | decode（ms/トークン） | J/問（11 GPU・総） | J/問（11 GPU・アイドル差引） |", "|---|---|---|---|---|---|---|---|"]
    for cond in CONDITIONS:
        for parallel in PARALLELS:
            row = table.get(cond, {}).get(str(parallel))
            if row is None:
                continue
            decode = row["decode_s_per_token"]
            decode_ms = None if decode is None else {k: v * 1000 if k != "n" else v for k, v in decode.items()}
            lines.append(
                f"| {cond} | {parallel} | {fmt_span(row['throughput_qps'])} | {fmt_span(row['e2e_p50_s'], 2)} | "
                f"{fmt_span(row['e2e_p95_s'], 2)} | {fmt_span(decode_ms, 1)} | {fmt_span(row['gross_j_per_q_11gpu'], 0)} | "
                f"{fmt_span(row['net_j_per_q_11gpu'], 0)} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    """全実行を集計して出力ファイルを書く."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--glob", default="results/p0007_d_*_p*_r[123]")
    # 事後の感度分析（格子の外の実行）の判定は verdict_original と呼ばない
    parser.add_argument("--verdict-key", default="verdict_original")
    args = parser.parse_args()
    runs = [s for d in sorted(glob.glob(args.glob)) if (s := summarize_run(Path(d))) is not None]
    table = summarize(runs)
    verdict = {args.verdict_key: judge_load(table)}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "d_runs.json").write_text(json.dumps(runs, indent=1, ensure_ascii=False))
    (out / "d_summary.json").write_text(json.dumps(table, indent=1, ensure_ascii=False))
    (out / "verdict.json").write_text(json.dumps(verdict, indent=1, ensure_ascii=False))
    sim = sim_vs_live(table, runs)
    sim["external_gpu_use"] = external_gpu_use(runs)
    sim["K4_selection_agreement"] = selection_agreement(runs)
    (out / "sim_vs_live.json").write_text(json.dumps(sim, indent=1, ensure_ascii=False))
    (out / "d_tables.md").write_text(render_tables(table))
    print(render_tables(table))
    print(json.dumps(verdict, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
