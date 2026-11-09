"""d0009 B2（Iteration 40）：採点を生成と同じ GPU に載せたときの可否（H-C0）と処理量の比の和 E_co（H-C1）の集計と，ゲートの判定．

b2co_chain.sh が呼ぶ．使い方（リポジトリの直下．GPU・実機は使わない．実行ごとの記録は results/d0009_b2_co_<name>/，
集計は results/d0009_b2_colocated_scoring/）:
    uv run python scripts/d0009/b2co_verdict.py ggen --run results/d0009_b2_co_ggen   # G-GEN：生成の経路の確かめ（生成だけ）
    uv run python scripts/d0009/b2co_verdict.py gs16 --run results/d0009_b2_co_s16pass   # G-S16：採点の対照の経路の退行の確かめ
    uv run python scripts/d0009/b2co_verdict.py gco --run results/d0009_b2_co_copass     # G-CO：共存の読み込みと得点の一致
    uv run python scripts/d0009/b2co_verdict.py check --run <dir> --host wafl502 --kind g|s|b [--smoke]
    uv run python scripts/d0009/b2co_verdict.py orders      # ラウンドごとの G・S・B の順（種 40）
    uv run python scripts/d0009/b2co_verdict.py summary     # colocate_summary.json・verdict.json・analysis_report.md
- 得点の一致（G-S16・G-CO）は b2l_verdict.gate_f16 の関数を再利用する（基準は Iteration 39 の fp16・最大長 1,024 の 1 周）．
- 実行ごとの記録：ps_pre.json・ps_post.json（Ollama の /api/ps．採点だけの実行は空でよい），oom.json（{"scorer": n, "ollama": n}．実行中に増えた
  `out of memory` の行の数），snap_<host>_{pre,post}.txt，gpu_<host>.csv（1 秒ごと），cool_wait_s.txt，gen_bench.json・gen_requests.jsonl（G・B），
  score_bench.json・requests.jsonl（S・B）．
- r_g = Σ_p t_G(p) / Σ_p t_B(p)（同じラウンドの G と B の両方の区間に終わった候補 p だけ．t = prefill_s + decode_s），r_s = X_req(B) / X_req(S)，
  E_co = r_g + r_s．採否は機械的に当てはめるだけで，評価は rc-evaluator が行う．
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import statistics
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml


def load_script(name: str) -> ModuleType:
    """scripts/ はパッケージではないので，同じディレクトリの兄弟のスクリプトをパスから読み込む．"""
    path = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


B2L = load_script("b2l_verdict.py")
B2C = B2L.B2C
B2 = B2L.B2

BASE = "d0009_b2_co"
RESULTS = Path("results")
RESULT_DIR = RESULTS / "d0009_b2_colocated_scoring"
# G-S16・G-CO の基準：Iteration 39 の fp16・最大長 1,024 の 1 周
REFERENCE_PASS = RESULTS / "d0009_b2_ml1024_l1kpass"
P1_RESULTS = RESULTS / "d0009_a3_p1" / "results.jsonl"
KINDS = ("g", "s", "b")
ROUNDS = [1, 2, 3]
ORDER_SEED = 40
GGEN_N = 30
GEN_MODEL = "llama3.1:8b"
GEN_NUM_CTX = 16384
LLM_KEYS = ("num_ctx", "num_predict", "temperature", "think", "answer_prompt_format")
# H-C0・H-C1（事前登録）
GPU_MEM_MAX_MIB = 11776
E_SMALL_LOSS_MIN = 0.90
E_LARGE_LOSS_MAX = 0.80
GPUS_TOTAL = 10
FIRST_HOST = "wafl502"
CANDIDATES_PER_QUESTION = 3


def run_dir(name: str) -> Path:
    """実行の名前（ggen・s16pass・copass・smoke・r<round>_<g|s|b>）から結果のディレクトリを返す．"""
    return RESULTS / f"{BASE}_{name}"


def read_json(path: Path) -> Any:
    """JSON を読む．"""
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSON Lines を読む．"""
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ── Ollama の /api/ps ────────────────────────────────────────────────────────


def ps_status(path: Path) -> dict[str, Any]:
    """/api/ps の記録から，載っているモデルの数・全て 100% GPU か（size_vram == size）・context_length を返す．読めなければ readable=False．"""
    try:
        models = read_json(path).get("models", [])
    except (OSError, ValueError, AttributeError):
        return {"readable": False, "n_models": 0, "all_on_gpu": False, "context_lengths": []}
    return {
        "readable": True,
        "n_models": len(models),
        "all_on_gpu": bool(models) and all(m["size_vram"] == m["size"] for m in models),
        "spilled": any(m["size_vram"] < m["size"] for m in models),
        "context_lengths": [m.get("context_length") for m in models],
        "names": [m.get("name") for m in models],
    }


# ── G-GEN ────────────────────────────────────────────────────────────────────


def expected_prompt_tokens(p1_results: Path, qids: list[str]) -> dict[tuple[str, int], int]:
    """d0009_a3_p1 の保存値：（qid，候補の番号）→ その候補を作った node の prompt_tokens（候補の source で node_answers と対応づける）．"""
    wanted = set(qids)
    out: dict[tuple[str, int], int] = {}
    for row in read_jsonl(p1_results):
        if row["qid"] not in wanted or row.get("error") is not None:
            continue
        by_source = {a["source"]: a["prompt_tokens"] for a in row["node_answers"]}
        for ci, cand in enumerate(row["owner_answer"]["candidates"]):
            out[(row["qid"], ci)] = int(by_source[cand["source"]])
    return out


def llm_config_diff(remote_config: Path, local_config: Path) -> dict[str, Any]:
    """node（GPU の PC）の config.yaml と今の config.yaml の llm の主要キーの値の比較（食い違うキーだけ differences に入る）．"""
    remote = yaml.safe_load(remote_config.read_text(encoding="utf-8"))["llm"]
    local = yaml.safe_load(local_config.read_text(encoding="utf-8"))["llm"]
    return {
        "remote": {k: remote.get(k) for k in LLM_KEYS},
        "local": {k: local.get(k) for k in LLM_KEYS},
        "differences": [k for k in LLM_KEYS if remote.get(k) != local.get(k)],
    }


def gate_gen(run: Path) -> dict[str, Any]:
    """G-GEN：先頭 30 件の失敗 0・prompt_tokens が d0009_a3_p1 の保存値と全て一致・100% GPU・context_length 16,384・node の llm の設定が同じ．ggen.json を書く．"""
    records = sorted(read_jsonl(run / "gen_requests.jsonl"), key=lambda r: r["index"])
    expected = expected_prompt_tokens(P1_RESULTS, [r["qid"] for r in records])
    mismatched = [
        {
            "index": r["index"],
            "qid": r["qid"],
            "candidate": r["candidate"],
            "got": r["prompt_tokens"],
            "expected": expected.get((r["qid"], r["candidate"])),
        }
        for r in records
        if r["error"] is None and r["prompt_tokens"] != expected.get((r["qid"], r["candidate"]))
    ]
    ps = ps_status(run / "ps_pre.json")
    config = llm_config_diff(run / "remote_config.yaml", Path("config.yaml"))
    result = {
        "n_requests": len(records),
        "n_errors": sum(r["error"] is not None for r in records),
        "n_prompt_tokens_mismatch": len(mismatched),
        "prompt_tokens_mismatches": mismatched[:5],
        "ps_pre": ps,
        "llm_config": config,
    }
    result["pass"] = bool(
        len(records) == GGEN_N
        and result["n_errors"] == 0
        and not mismatched
        and ps["all_on_gpu"]
        and ps["context_lengths"] == [GEN_NUM_CTX]
        and not config["differences"]
    )
    (run / "ggen.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return result


# ── G-S16・G-CO ──────────────────────────────────────────────────────────────


def gate_s16(run: Path) -> dict[str, Any]:
    """G-S16：採点だけの 1 周が Iteration 39 の 1,024 の 1 周と |差| ≤ 1e-3，R3′ と V2 の選択が一致（gate_f16 を基準だけ替えて再利用する）．"""
    return B2L.gate_f16(run, REFERENCE_PASS)


def feasibility_facts(run: Path) -> dict[str, Any]:
    """1 実行の可否の材料：OOM の数・採点と生成の失敗の要求の数・Ollama の前後の 100% GPU・GPU メモリの最大（実行全体の 1 秒ごとの記録）．"""
    oom = read_json(run / "oom.json") if (run / "oom.json").exists() else {}
    score_bench = read_json(run / "score_bench.json") if (run / "score_bench.json").exists() else {}
    gen_bench = read_json(run / "gen_bench.json") if (run / "gen_bench.json").exists() else {}
    ps_pre, ps_post = ps_status(run / "ps_pre.json"), ps_status(run / "ps_post.json")
    mems = [r["mem"] for csv in sorted(run.glob("gpu_*.csv")) for r in B2.read_gpu_csv(csv)]
    return {
        "oom_scorer": oom.get("scorer"),
        "oom_ollama": oom.get("ollama"),
        "score_request_errors": score_bench.get("n_request_errors_total"),
        "gen_request_errors": gen_bench.get("n_request_errors_total"),
        "ps_pre": ps_pre,
        "ps_post": ps_post,
        "gpu_mem_max_mib": max(mems, default=None),
    }


def run_not_fit(facts: dict[str, Any]) -> list[str]:
    """H-C0 の「載せられない」の理由（OOM・Ollama の溢れか 100% GPU でない・失敗の要求が 1 以上．材料が欠けているときも理由にする）．"""
    reasons: list[str] = []
    for key in ("oom_scorer", "oom_ollama", "score_request_errors", "gen_request_errors"):
        value = facts[key]
        if value is None:
            reasons.append(f"{key} missing")
        elif value > 0:
            reasons.append(f"{key}={value}")
    for key in ("ps_pre", "ps_post"):
        if not facts[key]["all_on_gpu"]:
            reasons.append(f"{key} not 100% GPU")
    return reasons


def gate_colocated(run: Path) -> dict[str, Any]:
    """G-CO：両方を載せた 1 周が可否の項目（OOM・溢れ・失敗）を満たし，得点が G-S16 と同じ基準で一致する．gco.json を書く．

    feasible が False なら本番を流さず H-C0 を「載せられない」とする．feasible で scores_match が False なら判定不能（原因を記録する）．
    """
    facts = feasibility_facts(run)
    reasons = run_not_fit(facts)
    scores = B2L.gate_f16(run, REFERENCE_PASS)
    result = {
        "facts": facts,
        "not_fit_reasons": reasons,
        "feasible": not reasons,
        "scores_match": bool(scores["pass"]),
        "scores": {k: v for k, v in scores.items() if k != "pass"},
    }
    result["pass"] = bool(result["feasible"] and result["scores_match"])
    (run / "gco.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return result


# ── 有効な実行 ───────────────────────────────────────────────────────────────


def check_run(run: Path, host: str, kind: str, smoke: bool) -> dict[str, Any]:
    """1 回の実行の有効性を判定して valid.json に書く（B2.check_run と同じ熱・他の研究者の判定．G・S は失敗 0 も要る）．

    B の失敗の要求は H-C0 の材料なので無効にしない（smoke は事前登録で両方の失敗 0 を要る）．
    """
    reasons: list[str] = []
    if (run / "aborted.txt").exists():
        reasons.append("safety abort: " + (run / "aborted.txt").read_text(encoding="utf-8").strip())
    score_path, gen_path = run / "score_bench.json", run / "gen_bench.json"
    needs_score, needs_gen = kind in ("s", "b"), kind in ("g", "b")
    score_bench = read_json(score_path) if score_path.exists() else None
    gen_bench = read_json(gen_path) if gen_path.exists() else None
    if needs_score and score_bench is None:
        reasons.append("no score_bench.json")
    if needs_gen and gen_bench is None:
        reasons.append("no gen_bench.json")
    if kind in ("g", "s") or smoke:
        if score_bench is not None and (
            score_bench.get("n_errors", 1) != 0 or score_bench.get("n_request_errors_total", 1) != 0
        ):
            reasons.append(
                f"failed score requests (questions {score_bench.get('n_errors')}, requests {score_bench.get('n_request_errors_total')})"
            )
        if gen_bench is not None and gen_bench.get("n_request_errors_total", 1) != 0:
            reasons.append(
                f"failed generation requests ({gen_bench.get('n_request_errors_total')})"
            )
    info: dict[str, Any] = {}
    try:
        pre = B2.read_snapshot(run / f"snap_{host}_pre.txt")
        post = B2.read_snapshot(run / f"snap_{host}_post.txt")
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
        rows = B2.read_gpu_csv(run / f"gpu_{host}.csv")
        info["n_gpu_samples"] = len(rows)
        info["max_temp_c"] = max((r["temp"] for r in rows), default=None)
        info["sw_thermal_active_samples"] = sum(r["sw_active"] for r in rows)
        info["hw_slowdown_active_samples"] = sum(r["hw_active"] for r in rows)
        if not rows:
            reasons.append(f"{host}: no GPU log")
        if info["max_temp_c"] is not None and info["max_temp_c"] >= B2.MAX_TEMP_C:
            reasons.append(f"{host}: max temperature {info['max_temp_c']}")
        if info["sw_thermal_active_samples"] or info["hw_slowdown_active_samples"]:
            reasons.append(
                f"{host}: slowdown Active samples {info['sw_thermal_active_samples']}/{info['hw_slowdown_active_samples']}"
            )
    except OSError as exc:
        reasons.append(f"{host}: unreadable GPU log ({exc})")
    result = {"valid": not reasons, "reasons": reasons, "hosts": {host: info}}
    (run / "valid.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


# ── 速さ（H-C1）─────────────────────────────────────────────────────────────


def candidate_times(gen_requests: Path, window: tuple[float, float]) -> dict[int, float]:
    """区間に終わった成功の要求の，候補の通し番号 → GPU の上の時間 t = prefill_s + decode_s（同じ番号が複数あれば最初のもの）．"""
    times: dict[int, float] = {}
    for r in sorted(read_jsonl(gen_requests), key=lambda r: r["done_at"]):
        if r["error"] is None and window[0] <= r["done_at"] < window[1]:
            times.setdefault(r["index"], r["prefill_s"] + r["decode_s"])
    return times


def generation_ratio(t_gen_only: dict[int, float], t_both: dict[int, float]) -> float | None:
    """r_g = Σ_p t_G(p) / Σ_p t_B(p)．和は両方の区間に終わった候補 p だけで取る（共通が無ければ None）．"""
    common = sorted(set(t_gen_only) & set(t_both))
    if not common:
        return None
    return sum(t_gen_only[p] for p in common) / sum(t_both[p] for p in common)


def decide_speed(e_values: list[float]) -> str:
    """H-C1（事前登録）：有効なラウンドの全てで E_co ≥ 0.90 なら small_loss，全てで E_co < 0.80 なら large_loss，それ以外（ラウンドが無いときも）は undetermined．"""
    if e_values and all(e >= E_SMALL_LOSS_MIN for e in e_values):
        return "small_loss"
    if e_values and all(e < E_LARGE_LOSS_MAX for e in e_values):
        return "large_loss"
    return "undetermined"


def decide_feasibility(
    not_fit_by_run: dict[str, list[str]], gpu_mem_max_by_run: dict[str, float | None]
) -> str:
    """H-C0（事前登録）：G-CO と有効な B のどれかに OOM・溢れ・失敗があれば cannot_fit．全て無くメモリの最大が全て ≤ 11,776 MiB なら fits，それ以外は undetermined．"""
    if any(reasons for reasons in not_fit_by_run.values()):
        return "cannot_fit"
    mems = list(gpu_mem_max_by_run.values())
    if not_fit_by_run and mems and all(m is not None and m <= GPU_MEM_MAX_MIB for m in mems):
        return "fits"
    return "undetermined"


def round_orders(seed: int = ORDER_SEED) -> list[list[str]]:
    """ラウンドごとの G・S・B の順（種 40 の無作為な並べ替え．1 つの乱数列から続けて並べ替える）．"""
    rng = random.Random(seed)
    orders = []
    for _ in ROUNDS:
        order = list(KINDS)
        rng.shuffle(order)
        orders.append(order)
    return orders


def balance_estimates(x_gen_cps: float, x_req_qps: float, e_co: float) -> dict[str, float]:
    """記述（採否に使わない）：GPU の PC 10 台の生成と採点が釣り合うときの処理量の上限（問/秒）．1 問 = 生成 3 候補 + 採点 1 問分（X_req は問/秒）．

    - dedicated：1 台を採点だけに回し 9 台が生成だけ．min(9 X_gen / 3, X_req)．
    - colocated_one：1 台が両方（時間を分け合い，生成の割合 ρ_g と採点の割合 ρ_s が ρ_g + ρ_s = E_co．各割合は 0〜1）で，9 台は生成だけ．
      min((9 + ρ_g) X_gen / 3, ρ_s X_req) を ρ_s について最大にする（釣り合う点か，端の点）．
    """
    per_question = CANDIDATES_PER_QUESTION
    dedicated = min((GPUS_TOTAL - 1) * x_gen_cps / per_question, x_req_qps)
    best = 0.0
    steps = 1000
    for i in range(steps + 1):
        rho_s = i / steps
        rho_g = min(max(e_co - rho_s, 0.0), 1.0)
        best = max(
            best, min((GPUS_TOTAL - 1 + rho_g) * x_gen_cps / per_question, rho_s * x_req_qps)
        )
    return {"dedicated_one_scoring_only": dedicated, "colocated_one": best}


def prod_prefix() -> str:
    """本番の実行名の前置き（機を wafl503 に替えた流し直しは `w503_`．chain が書く．無ければ空）．"""
    path = RESULT_DIR / "prod_prefix.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def load_rounds(prefix: str) -> tuple[dict[int, dict[str, Path]], dict[str, list[str]]]:
    """ラウンド → 種類（g・s・b）→ 有効な実行のディレクトリ（再実行があれば最初の有効なもの）と，無効だった実行の理由．prefix は実行名の前置き．"""
    runs: dict[int, dict[str, Path]] = {}
    invalid: dict[str, list[str]] = {}
    for rnd in ROUNDS:
        for kind in KINDS:
            for suffix in ("", "_retry"):
                path = run_dir(f"{prefix}r{rnd}_{kind}{suffix}")
                if not (path / "valid.json").exists():
                    continue
                valid = read_json(path / "valid.json")
                if not valid["valid"]:
                    invalid[path.name] = valid["reasons"]
                    continue
                runs.setdefault(rnd, {}).setdefault(kind, path)
    return runs, invalid


def describe_gen(run: Path) -> dict[str, Any]:
    """G・B の生成の記述（区間の X_gen・トークンの速さの中央値・待ち時間）．"""
    gen = read_json(run / "gen_bench.json")
    return {
        "run": run.name,
        "x_gen_cps": gen["x_gen_cps"],
        "n_ok": gen["n_ok"],
        "decode_tps_median": gen["decode_tps_median"],
        "prefill_tps_median": gen["prefill_tps_median"],
        "latency_p50_s": gen["latency_s"]["p50"],
        "latency_p95_s": gen["latency_s"]["p95"],
        "request_errors": gen["n_request_errors_total"],
    }


def answer_match_rate(
    gen_only: Path, both: Path, window_g: tuple[float, float], window_b: tuple[float, float]
) -> float | None:
    """記述：G と B の両方の区間に終わった同じ候補の，回答の文字列が一致する割合．"""

    def answers(run: Path, window: tuple[float, float]) -> dict[int, str]:
        out: dict[int, str] = {}
        for r in sorted(read_jsonl(run / "gen_requests.jsonl"), key=lambda r: r["done_at"]):
            if r["error"] is None and window[0] <= r["done_at"] < window[1]:
                out.setdefault(r["index"], r["answer"])
        return out

    a_g, a_b = answers(gen_only, window_g), answers(both, window_b)
    common = sorted(set(a_g) & set(a_b))
    return sum(a_g[p] == a_b[p] for p in common) / len(common) if common else None


def describe_rounds(
    runs: dict[int, dict[str, Path]], valid_rounds: list[int], host: str
) -> dict[str, Any]:
    """有効なラウンドごとに r_g・r_s・E_co と，生成・採点・可否の記述を作る．"""
    rounds: dict[str, Any] = {}
    for rnd in valid_rounds:
        g_run, s_run, b_run = runs[rnd]["g"], runs[rnd]["s"], runs[rnd]["b"]
        gen_g, gen_b = read_json(g_run / "gen_bench.json"), read_json(b_run / "gen_bench.json")
        win_g = (gen_g["window_start"], gen_g["window_end"])
        win_b = (gen_b["window_start"], gen_b["window_end"])
        t_g, t_b = (
            candidate_times(g_run / "gen_requests.jsonl", win_g),
            candidate_times(b_run / "gen_requests.jsonl", win_b),
        )
        r_g = generation_ratio(t_g, t_b)
        desc_s, desc_b = B2C.describe_run(s_run, host), B2C.describe_run(b_run, host)
        r_s = desc_b["x_req_qps"] / desc_s["x_req_qps"]
        rounds[str(rnd)] = {
            "r_g": r_g,
            "r_s": r_s,
            "E_co": None if r_g is None else r_g + r_s,
            "n_common_candidates": len(set(t_g) & set(t_b)),
            "gen_G": describe_gen(g_run),
            "gen_B": describe_gen(b_run),
            "score_S": desc_s,
            "score_B": desc_b,
            "answer_match_rate_G_vs_B": answer_match_rate(g_run, b_run, win_g, win_b),
            "facts_B": feasibility_facts(b_run),
        }
    return rounds


def summarize() -> dict[str, Any]:
    """H-C0・H-C1 の数値と機械的な判定．colocate_summary.json・verdict.json・analysis_report.md を書く（採否の評価は rc-evaluator が行う）．"""
    host = (RESULT_DIR / "host.txt").read_text(encoding="utf-8").split()[0]
    prefix = prod_prefix()
    gates = gate_status(prefix)
    runs, invalid = load_rounds(prefix)
    valid_rounds = [r for r in ROUNDS if set(runs.get(r, {})) == set(KINDS)]
    rounds = describe_rounds(runs, valid_rounds, host)
    e_values = [v["E_co"] for v in rounds.values() if v["E_co"] is not None]
    # H-C0：G-CO と，有効なラウンドの B（有効な B が無い／G-CO が無いときは材料が無いので undetermined）
    not_fit: dict[str, list[str]] = {}
    mem_max: dict[str, float | None] = {}
    gco_path = run_dir(f"{prefix}copass") / "gco.json"
    if gco_path.exists():
        gco = read_json(gco_path)
        not_fit[f"{prefix}copass"] = gco["not_fit_reasons"]
        mem_max[f"{prefix}copass"] = gco["facts"]["gpu_mem_max_mib"]
    for rnd in ROUNDS:
        b_path = runs.get(rnd, {}).get("b")
        if b_path is not None:
            facts = feasibility_facts(b_path)
            not_fit[b_path.name] = run_not_fit(facts)
            mem_max[b_path.name] = facts["gpu_mem_max_mib"]
    h_c0 = decide_feasibility(not_fit, mem_max)
    h_c1 = decide_speed(e_values)
    estimates = None
    if valid_rounds and e_values:
        mid = statistics.median(e_values)
        x_gen = statistics.median(v["gen_G"]["x_gen_cps"] for v in rounds.values())
        x_req = statistics.median(v["score_S"]["x_req_qps"] for v in rounds.values())
        estimates = {
            "E_co_median": mid,
            "X_gen_G_median_cps": x_gen,
            "X_req_S_median_qps": x_req,
            **balance_estimates(x_gen, x_req, mid),
        }
    first_attempt = None
    if prefix:
        # 機を替えた流し直しのとき：wafl502 の最初の試行（無効が 2 回で止まった本番）を記述として残す（採否に使わない）
        first_runs, first_invalid = load_rounds("")
        first_rounds = [r for r in ROUNDS if set(first_runs.get(r, {})) == set(KINDS)]
        first_attempt = {
            "host": FIRST_HOST,
            "valid_rounds": first_rounds,
            "rounds": describe_rounds(first_runs, first_rounds, FIRST_HOST),
            "invalid_runs": first_invalid,
        }
    summary = {
        "host": host,
        "gates": gates,
        "valid_rounds": valid_rounds,
        "rounds": rounds,
        "E_co_by_round": {k: v["E_co"] for k, v in rounds.items()},
        "invalid_runs": invalid,
        "H_C0_not_fit_reasons": not_fit,
        "H_C0_gpu_mem_max_mib": mem_max,
        "H_C0": h_c0,
        "H_C1": h_c1,
        "thresholds": {
            "gpu_mem_max_mib": GPU_MEM_MAX_MIB,
            "small_loss": E_SMALL_LOSS_MIN,
            "large_loss": E_LARGE_LOSS_MAX,
        },
        "balance_estimates_description_only": estimates,
        "first_attempt_description_only": first_attempt,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "colocate_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    verdict = {
        "H_C0": h_c0,
        "H_C1": h_c1,
        "success_condition_1_gates_passed": gates["all_passed"],
        "success_condition_2_enough_valid_rounds": len(valid_rounds) >= 2,
        "mechanical_outcome": mechanical_outcome(gates, h_c0, h_c1, len(valid_rounds)),
        "note": "mechanical application of the pre-registered rules; adoption is decided by rc-evaluator",
    }
    (RESULT_DIR / "verdict.json").write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    (RESULT_DIR / "analysis_report.md").write_text(
        render_report(summary, verdict), encoding="utf-8"
    )
    return summary


def gate_status(prefix: str) -> dict[str, Any]:
    """G-GEN・G-S16・G-CO（の可否と得点の一致）・smoke の通過状況（記録が無ければ None）．all_passed は 5 項目の全ての通過．

    prefix は G-GEN・G-CO の実行名の前置き（機を替えた流し直しは w503_）．G-S16 と smoke は wafl502 のものを使う（事前登録）．
    """

    def passed(path: Path, key: str = "pass") -> bool | None:
        return read_json(path)[key] if path.exists() else None

    gates: dict[str, Any] = {
        "G_GEN": passed(run_dir(f"{prefix}ggen") / "ggen.json"),
        "G_S16": passed(run_dir("s16pass") / "gf16.json"),
        "G_CO_feasible": passed(run_dir(f"{prefix}copass") / "gco.json", "feasible"),
        "G_CO_scores_match": passed(run_dir(f"{prefix}copass") / "gco.json", "scores_match"),
        "smoke": passed(run_dir("smoke") / "valid.json", "valid"),
    }
    gates["all_passed"] = all(
        gates[k] is True for k in ("G_GEN", "G_S16", "G_CO_feasible", "G_CO_scores_match", "smoke")
    )
    return gates


def mechanical_outcome(gates: dict[str, Any], h_c0: str, h_c1: str, n_valid_rounds: int) -> str:
    """成功条件（事前登録）の機械的な当てはめ．

    G-CO の可否の項目が不通過（載せられない）なら，他のゲートの通過を待たずに reject（本番を流さない）．G-CO の可否以外のゲート・有効なラウンド数が
    足りなければ undetermined_not_closed（値を閉じない）．
    """
    if gates["G_CO_feasible"] is False:
        return "reject_not_fit_in_g_co"
    if not gates["all_passed"] or n_valid_rounds < 2:
        return "undetermined_not_closed"
    if h_c0 == "fits" and h_c1 == "small_loss":
        return "adopt"
    if h_c0 == "cannot_fit" or h_c1 == "large_loss":
        return "reject"
    return "undetermined_closed_as_description"


def render_report(summary: dict[str, Any], verdict: dict[str, Any]) -> str:
    """r_g・r_s・E_co の表と可否の材料（Markdown．機械集計．採否は評価の担当）．"""
    lines = [
        "# d0009 B2 採点を生成と同じ GPU に載せる（機械集計．採否は評価の担当）",
        "",
        f"機: {summary['host']}　有効なラウンド: {summary['valid_rounds']}　ゲート: {json.dumps(summary['gates'])}",
        "",
        "| ラウンド | X_gen(G) | X_gen(B) | r_g | X_req(S) | X_req(B) | r_s | E_co | 共通の候補 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for rnd, v in summary["rounds"].items():
        e = v["E_co"]
        lines.append(
            f"| {rnd} | {v['gen_G']['x_gen_cps']:.3f} | {v['gen_B']['x_gen_cps']:.3f} | {v['r_g']:.3f} | "
            f"{v['score_S']['x_req_qps']:.3f} | {v['score_B']['x_req_qps']:.3f} | {v['r_s']:.3f} | "
            f"{'n/a' if e is None else f'{e:.3f}'} | {v['n_common_candidates']} |"
        )
    lines += [
        "",
        f"H-C0: {summary['H_C0']}　H-C1: {summary['H_C1']}　機械の結論: {verdict['mechanical_outcome']}",
        f"載せられない理由: {json.dumps(summary['H_C0_not_fit_reasons'], ensure_ascii=False)}",
        f"GPU メモリの最大 MiB: {json.dumps(summary['H_C0_gpu_mem_max_mib'])}",
        f"無効になった実行: {json.dumps(summary['invalid_runs'], ensure_ascii=False)}",
        f"記述（見積もり．採否に使わない）: {json.dumps(summary['balance_estimates_description_only'])}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    """サブコマンドを実行し，結果を標準出力へ JSON で出す．ggen・gs16・check は不合格のとき終了コード 1，gco は得点の不一致で 1・載せられないで 2．"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("ggen", "gs16", "gco"):
        sub.add_parser(name).add_argument("--run", required=True)
    for name in ("orders", "summary"):
        sub.add_parser(name)
    p = sub.add_parser("check")
    p.add_argument("--run", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--kind", choices=KINDS, required=True)
    p.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.cmd == "orders":
        for order in round_orders():
            print(" ".join(order))
        return 0
    if args.cmd == "summary":
        print(json.dumps(summarize(), indent=1, ensure_ascii=False)[:4000])
        return 0
    if args.cmd == "check":
        result = check_run(Path(args.run), args.host, args.kind, args.smoke)
        print(json.dumps({"valid": result["valid"], "reasons": result["reasons"]}))
        return 0 if result["valid"] else 1
    run = Path(args.run)
    if args.cmd == "gco":
        result = gate_colocated(run)
        print(json.dumps(result, indent=1, ensure_ascii=False)[:6000])
        return 0 if result["pass"] else (2 if not result["feasible"] else 1)
    result = gate_gen(run) if args.cmd == "ggen" else gate_s16(run)
    print(json.dumps(result, indent=1, ensure_ascii=False)[:6000])
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
