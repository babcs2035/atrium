"""d0009 A6（Iteration 29）：負荷の測定 12 実行の集計とゲート・判定（`results/d0009_a6_load/verdict.json`・`load.json`）．

使い方（リポジトリの直下．実機は使わない．実行の後，commit の前に sanitize_results.py を通す）:
    uv run python scripts/d0009/a6_verdict.py
- 入力：`results/d0009_a6_{p,c,s}_r{1..4}/`（results.jsonl・run_meta.json・requester.log・gpu_apps_*.txt・ollama_ps_*.txt）と
  `results/d0009_a6_load/table/cost.json`（a6_owner_table.py の t_P・t_C）．
- 処理量 X = 成功した問の数 ÷（最後の問の終わり − 最初の問の始まり）．results の行には時刻が無いので，run_meta の
  `started_at`・`finished_at`（X_meta）と，requester.log の HTTP 要求の最初と最後の時刻（X_log）の両方を書く．判定には X_meta を使う．
- ゲート：G-S（P の 4 回の選択が `results/d0009_a3_p1_R3p` と一致）・G-C（C・S の上位 5 通が `results/d0009_a3_c1` と一致）・
  G-F（失敗率 > 1% の実行は，そのラウンドを判定から除く）．
- 判定：H-A6L1（P 対 C）・H-A6L2（S 対 P）は q_r の閾値 1.10 の規則（journal の Iteration 29）．H-A6L3・H-A6V は記述．
統計の計算は，この repo に比の判定の既存実装が無いので，journal に事前登録した規則（単純な比と閾値）をそのまま書いた．
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atrium.analysis import read_results

OUT_DIR = Path("results/d0009_a6_load")
CONDITIONS = ("p", "c", "s")
ROUNDS = (1, 2, 3, 4)
RATIO_THRESHOLD = 1.10
MAX_FAILURE_RATE = 0.01
MIN_VALID_ROUNDS = 3
CE_TIE_TOLERANCE = 1e-6
TOP_MATCH_MIN = 0.999
TOP_TIE_TOLERANCE = 1e-6
# F-6（p0007．廃止前の制御点の RTX 3060）の 1 組の採点の時間（秒）
F6_RTX3060_S_PER_PAIR = 0.126
# Iteration 28 の V2 の検証の費用（gpu4 の RTX 5090・並列 2．候補 1 件の中央値の秒）と 1 問の候補数・並列数
V2_VERIFY_MEDIAN_S = 1.58
V2_CANDIDATES_PER_QUESTION = 3
V2_PARALLEL = 2
V2_DELEGATED_DOCS_MAX = 15
E_ANY = {"H": 0.3698, "Q": 0.5498}
LLAMA_BLOB = "667b0c1932bc6ffc"
LOG_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3}) INFO httpx: HTTP Request")


def percentile(values: Sequence[float], q: float) -> float | None:
    """線形補間なしの順位（切り下げ）の百分位．空なら None．"""
    ordered = sorted(values)
    return ordered[int(q * (len(ordered) - 1))] if ordered else None


def median_or_none(values: Sequence[float]) -> float | None:
    """中央値（空なら None）．"""
    return statistics.median(values) if values else None


def window_from_log(path: Path) -> tuple[float, float] | None:
    """requester.log の httpx の要求の最初と最後の時刻（UTC の epoch 秒）．無ければ None．"""
    times: list[float] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LOG_TIME.match(line)
        if m:
            stamp = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            times.append(stamp.timestamp() + int(m.group(2)) / 1000)
    return (times[0], times[-1]) if times else None


def other_gpu_processes(path: Path) -> list[str]:
    """gpu_apps_*.txt の中の，この実験の llama3.1:8b（ollama の llama-server）以外のプロセスの行（他の研究者の使用の目安）．"""
    out: list[str] = []
    host = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("== "):
            host = line[3:].strip()
        elif line.startswith("pid=") and LLAMA_BLOB not in line:
            out.append(f"{host}: {line[:120]}")
    return out


def ollama_not_fully_on_gpu(path: Path) -> list[str]:
    """ollama_ps_*.txt の中の，GPU に 100% 載っていない行．"""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "llama3.1" in line and "100% GPU" not in line
    ]


def decode_ms_per_token_by_host(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """GPU の PC ごとの生成の decode の ms/トークン（decode_s の合計 ÷ 出力トークンの合計）．"""
    decode: dict[str, float] = {}
    tokens: dict[str, int] = {}
    for row in rows:
        generations: list[tuple[str, float, int]] = []
        for na in row.get("node_answers") or []:
            host = str(na.get("delegated_to") or na.get("node_id"))
            generations.append(
                (host, float(na.get("decode_s") or 0), int(na.get("output_tokens") or 0))
            )
        if not generations and row.get("llm"):
            generations.append(
                (
                    str(row.get("generated_by") or "requester_llm"),
                    float(row["llm"].get("decode_s") or 0),
                    int(row.get("output_tokens") or 0),
                )
            )
        for host, d, t in generations:
            decode[host] = decode.get(host, 0.0) + d
            tokens[host] = tokens.get(host, 0) + t
    return {h: 1000 * decode[h] / tokens[h] for h in sorted(decode) if tokens[h] > 0}


def summarize_run(run_dir: Path) -> dict[str, Any]:
    """1 実行の処理量・失敗・遅延・費用の内訳・他のプロセスの有無．"""
    rows = read_results(run_dir / "results.jsonl")
    ok = [r for r in rows if r.get("error") is None]
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    n_total = int(meta["n_questions"])
    window_meta = float(meta["finished_at"]) - float(meta["started_at"])
    log_window = window_from_log(run_dir / "requester.log")

    def timing(key: str) -> list[float]:
        return [float(r["timings"][key]) for r in ok if key in r["timings"]]

    owner = [r["owner_answer"] for r in ok if r.get("owner_answer")]
    summary: dict[str, Any] = {
        "n_questions": n_total,
        "n_rows": len(rows),
        "n_ok": len(ok),
        "failure_rate": 1 - len(ok) / n_total,
        "window_meta_s": window_meta,
        "x_meta": len(ok) / window_meta,
        "window_log_s": (log_window[1] - log_window[0]) if log_window else None,
        "x_log": len(ok) / (log_window[1] - log_window[0]) if log_window else None,
        "log_start_minus_meta_start_s": (log_window[0] - float(meta["started_at"]))
        if log_window
        else None,
        "e2e_p50_s": percentile(timing("e2e_s"), 0.5),
        "e2e_p95_s": percentile(timing("e2e_s"), 0.95),
        "median_s": {
            k: median_or_none(timing(k))
            for k in ("generate_s", "ce_s", "rerank_s", "retrieve_s", "probe_s")
        },
        "requests_per_question_mean": (
            statistics.fmean(o["n_requests_total"] for o in owner) if owner else 1.0
        ),
        "ce_docs_received_mean": (
            statistics.fmean(o["ce_docs_received"] for o in owner if "ce_docs_received" in o)
            if any("ce_docs_received" in o for o in owner)
            else None
        ),
        "requester_cpu_util": meta.get("requester_cpu_util"),
        "decode_ms_per_token_by_host": decode_ms_per_token_by_host(ok),
        "other_gpu_processes_pre": other_gpu_processes(run_dir / "gpu_apps_pre.txt"),
        "other_gpu_processes_post": other_gpu_processes(run_dir / "gpu_apps_post.txt"),
        "ollama_not_fully_on_gpu_start": ollama_not_fully_on_gpu(run_dir / "ollama_ps_start.txt"),
        "ollama_not_fully_on_gpu_end": ollama_not_fully_on_gpu(run_dir / "ollama_ps_end.txt"),
        "gpu_wait_result": (run_dir / "gpu_wait_result.txt").read_text(encoding="utf-8").strip(),
    }
    summary["confounded_by_other_processes"] = bool(
        summary["other_gpu_processes_pre"] or summary["other_gpu_processes_post"]
    )
    return summary


def selection_agreement(p_dir: Path, reference_dir: Path) -> dict[str, Any]:
    """G-S：live の選んだ候補の番号が R3′（reference）と一致する問の数（ce_max の差が許容以内の同点は一致）．"""
    ref = {r["qid"]: r for r in read_results(reference_dir / "results.jsonl") if not r.get("error")}
    n = strict = tied = 0
    mismatched: list[str] = []
    for row in read_results(p_dir / "results.jsonl"):
        if row.get("error") is not None or row["qid"] not in ref:
            continue
        n += 1
        owner = row["owner_answer"]
        live, wanted = owner["chosen_index"], ref[row["qid"]]["chosen_index"]
        scores = owner["ce_max"]
        same = live == wanted
        strict += same
        close = same or (
            scores[live] is not None
            and scores[wanted] is not None
            and abs(scores[live] - scores[wanted]) <= CE_TIE_TOLERANCE
        )
        tied += close
        if not close:
            mismatched.append(row["qid"])
    return {
        "n": n,
        "strict": strict / n if n else None,
        "tie_tolerant": tied / n if n else None,
        "n_mismatched": len(mismatched),
        "mismatched_qids": mismatched[:20],
    }


def top5_agreement(
    run_dir: Path, reference_dir: Path, scores_of: Callable[[str, str], float]
) -> dict[str, Any]:
    """G-C：上位 5 通の文書 ID が参照の実行と一致する問の割合（得点が許容以内で並ぶ同点の入れ替えは一致とみなす）．"""
    ref = {r["qid"]: r for r in read_results(reference_dir / "results.jsonl") if not r.get("error")}
    n = strict = tied = 0
    for row in read_results(run_dir / "results.jsonl"):
        if row.get("error") is not None or row["qid"] not in ref:
            continue
        n += 1
        a, b = list(row["top_doc_ids"]), list(ref[row["qid"]]["top_doc_ids"])
        strict += a == b
        if a == b:
            tied += 1
            continue
        sa = sorted(scores_of(row["qid"], d) for d in a)
        sb = sorted(scores_of(row["qid"], d) for d in b)
        tied += len(sa) == len(sb) and all(
            abs(x - y) <= TOP_TIE_TOLERANCE for x, y in zip(sa, sb, strict=True)
        )
    return {"n": n, "strict": strict / n if n else None, "tie_tolerant": tied / n if n else None}


def classify(ratios: Sequence[float], high_label: str, low_label: str) -> str:
    """全ての有効なラウンドで比 > 1.10 なら high_label，< 1/1.10 なら low_label，全て範囲内なら「同じ程度」，それ以外は「決まらない」．"""
    if len(ratios) < MIN_VALID_ROUNDS:
        return "決まらない"
    if all(q > RATIO_THRESHOLD for q in ratios):
        return high_label
    if all(q < 1 / RATIO_THRESHOLD for q in ratios):
        return low_label
    if all(1 / RATIO_THRESHOLD <= q <= RATIO_THRESHOLD for q in ratios):
        return "同じ程度"
    return "決まらない"


def pair_ratios(
    runs: Mapping[str, Mapping[int, dict[str, Any]]], a: str, b: str, key: str
) -> dict[str, Any]:
    """条件 a 対 b のラウンドごとの比 X_a/X_b（両方の実行が有効なラウンドだけを判定に使う）．"""
    per_round = {}
    for r in ROUNDS:
        if r in runs[a] and r in runs[b]:
            ra, rb = runs[a][r], runs[b][r]
            valid = ra["valid"] and rb["valid"]
            per_round[f"r{r}"] = {"q": ra[key] / rb[key], "valid": valid}
    return per_round


def main() -> None:
    """12 実行を集計し，ゲートと判定を verdict.json・load.json に書く．"""
    runs: dict[str, dict[int, dict[str, Any]]] = {c: {} for c in CONDITIONS}
    for c in CONDITIONS:
        for r in ROUNDS:
            run_dir = Path(f"results/d0009_a6_{c}_r{r}")
            if (run_dir / "run_meta.json").exists():
                s = summarize_run(run_dir)
                s["valid"] = s["failure_rate"] <= MAX_FAILURE_RATE
                runs[c][r] = s

    # ゲート
    g_s = {
        f"r{r}": selection_agreement(
            Path(f"results/d0009_a6_p_r{r}"), Path("results/d0009_a3_p1_R3p")
        )
        for r in runs["p"]
    }
    table: dict[tuple[str, str], float] = {}
    with Path("artifacts/enronqa/ce_table/holdout.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            table[(row["qid"], row["doc_id"])] = float(row["score"])
    g_c = {
        f"{c}_r{r}": top5_agreement(
            Path(f"results/d0009_a6_{c}_r{r}"),
            Path("results/d0009_a3_c1"),
            lambda q, d: table[(q, d)],
        )
        for c in ("c", "s")
        for r in runs[c]
    }
    gates = {
        "G-S": {
            "passed": bool(g_s) and all(v["tie_tolerant"] == 1.0 for v in g_s.values()),
            "detail": g_s,
        },
        "G-C": {
            "passed": bool(g_c)
            and all(
                v["tie_tolerant"] is not None and v["tie_tolerant"] >= TOP_MATCH_MIN
                for v in g_c.values()
            ),
            "detail": g_c,
        },
        "G-F": {c: {f"r{r}": s["failure_rate"] for r, s in runs[c].items()} for c in CONDITIONS},
    }

    # 判定（有効なラウンドだけ）
    def valid_qs(a: str, b: str, key: str) -> tuple[dict[str, Any], list[float]]:
        per_round = pair_ratios(runs, a, b, key)
        return per_round, [v["q"] for v in per_round.values() if v["valid"]]

    l1_rounds, l1_qs = valid_qs("p", "c", "x_meta")
    l2_rounds, l2_qs = valid_qs("s", "p", "x_meta")
    _, l1_qs_log = valid_qs("p", "c", "x_log")
    _, l2_qs_log = valid_qs("s", "p", "x_log")
    clean = {
        c: {r: s for r, s in runs[c].items() if not s["confounded_by_other_processes"]}
        for c in CONDITIONS
    }
    sensitivity: dict[str, Any] = {}
    for name, a, b in (("P_vs_C", "p", "c"), ("S_vs_P", "s", "p")):
        qs = [
            runs[a][r]["x_meta"] / runs[b][r]["x_meta"]
            for r in ROUNDS
            if r in clean[a] and r in clean[b] and runs[a][r]["valid"] and runs[b][r]["valid"]
        ]
        sensitivity[name] = {"q": qs, "n": len(qs)}

    # H-A6L3・H-A6V（記述）
    cost = json.loads((OUT_DIR / "table" / "cost.json").read_text(encoding="utf-8"))
    t = {"p": cost["t_P"]["median_s"], "c": cost["t_C"]["median_s"], "s": cost["t_C"]["median_s"]}
    pairs = {
        "p": cost["t_P"]["pairs_per_question_mean"],
        "c": cost["t_C"]["pairs_per_question_mean"],
    }
    pairs["s"] = pairs["c"]
    x_live = {
        c: median_or_none([s["x_meta"] for s in runs[c].values() if s["valid"]]) for c in CONDITIONS
    }
    l3: dict[str, Any] = {
        "note": "記述（採否に使わない）．X_est = min(X_live の中央値, X_ce)．RTX 3060 換算は 1 組 0.126 s（F-6）×組の数（推測）"
    }
    for c in CONDITIONS:
        x_ce = 1 / t[c]
        x_ce_3060 = 1 / (F6_RTX3060_S_PER_PAIR * pairs[c])
        live = x_live[c]
        l3[c] = {
            "x_live_median": live,
            "t_gpu4_s": t[c],
            "x_ce_gpu4": x_ce,
            "x_est_gpu4": min(live, x_ce) if live is not None else None,
            "t_rtx3060_est_s": F6_RTX3060_S_PER_PAIR * pairs[c],
            "x_ce_rtx3060_est": x_ce_3060,
            "x_est_rtx3060": min(live, x_ce_3060) if live is not None else None,
        }
    v2_capacity = V2_PARALLEL / (V2_VERIFY_MEDIAN_S * V2_CANDIDATES_PER_QUESTION)
    h_a6v = {
        "note": "記述．V2 を 1 枚の RTX 5090（並列 2）で載せた場合の検証の容量（問/秒）と，委託で渡る文書",
        "v2_capacity_q_per_s": v2_capacity,
        "v2_sequential_s_per_question": V2_VERIFY_MEDIAN_S * V2_CANDIDATES_PER_QUESTION,
        "p_x_live_median": x_live["p"],
        "p_x_est_gpu4_with_v2": min(
            x for x in (x_live["p"], 1 / t["p"], v2_capacity) if x is not None
        ),
        "delegated_docs_per_question_max": V2_DELEGATED_DOCS_MAX,
        "e_any": E_ANY,
    }

    n_valid = {c: sum(s["valid"] for s in runs[c].values()) for c in CONDITIONS}
    verdict = {
        "verdict_original": {
            "H-A6L1_P_vs_C": classify(l1_qs, "P が高い", "C が高い"),
            "H-A6L2_S_vs_P": classify(l2_qs, "S が高い", "P が高い")
            if n_valid["s"] >= MIN_VALID_ROUNDS
            else "未測定または決まらない",
            "ratio_threshold": RATIO_THRESHOLD,
            "valid_rounds": n_valid,
            "success_condition_valid_rounds_ge_3_each": all(
                v >= MIN_VALID_ROUNDS for v in n_valid.values()
            ),
        },
        "H-A6L1_rounds": l1_rounds,
        "H-A6L2_rounds": l2_rounds,
        "using_x_log_instead": {
            "H-A6L1_P_vs_C": classify(l1_qs_log, "P が高い", "C が高い"),
            "H-A6L2_S_vs_P": classify(l2_qs_log, "S が高い", "P が高い"),
        },
        "sensitivity_excluding_other_processes": sensitivity,
        "gates": gates,
        "H-A6L3": l3,
        "H-A6V": h_a6v,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "verdict.json").write_text(
        json.dumps(verdict, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    load = {c: {f"r{r}": s for r, s in runs[c].items()} for c in CONDITIONS}
    (OUT_DIR / "load.json").write_text(
        json.dumps(load, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(verdict["verdict_original"], indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
