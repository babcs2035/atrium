"""質問者を 1 台（N = 1）から 3 台（N = 3）にしたときの正答率・負荷・露出・障害の集計と判定（d0009 A1．Iteration 23）．

`atrium multi-requester-eval` が，実機で流した実行（`results/<prefix>_n1_r1`・`_n1_r2`・`_n3_r1`・`_n3_r2`．必要なら `_n3_hi`・`_f_n1`・`_f_n3`）と，
採点・R3′ の当てはめ（`<run>_R3p`）の済んだ結果から，journal の Iteration 23 の事前登録の判定を `verdict.json` と
`multi_requester.json` に書く．実機のノードは使わない（オフライン）．

- ゲート：G-N1（N = 1 の退行：Iteration 22 の P1 と検索が同じ），G-N3（N = 3 でも検索が N = 1 と同じ），
  G-A（各問がちょうど 1 台の質問者で処理され，質問者の番号が並べた順の i mod N と一致），G3（失敗率）．
- H-A1a（精度．R3′）・H-A1a′（実機で選んだ max_score）：Δ = 平均（N3 の 2 回）− 平均（N1 の 2 回）．Iteration 22 の区間の作り方 1（2 段のブートストラップ）と
  2（分散の分解）の両方の 95% 区間が [−0.005, +0.005] の中なら「同等」，両方が 0 を含まず |Δ| > 0.005 なら「変わる」，それ以外は「決まらない」．
- H-A1L（負荷）：処理量 X（成功した問の数 ÷（全質問者の最後の finished_at − 最初の started_at））の比 X(N3_rk) / X(N1_rk)．
- H-A1E（露出）：質問者 r が受け取る文書の集合 D_r（r が受け持った問の候補の context_doc_ids から，r の PC が持つ受信箱の文書を除く）から
  E_any・E_pair・E_max を出し，質問者の数 N を変えたオフラインの曲線も出す．
- H-A1F（障害．記録）：止めたノードに当たった質問者ごとの失敗の割合など．

統計量は既存の実装を使う：2 段のブートストラップ・分散の分解・同じ設定の組の McNemar（`atrium.rerun_variance`），
McNemar の正確検定（`atrium.analysis.mcnemar_exact`），verdict.json の書式（`atrium.verdict.write_verdict`）．
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import mcnemar_exact, read_results
from atrium.benchmarks import load_questions
from atrium.data_enronqa import QUESTION_FILES
from atrium.paths import dataset_paths
from atrium.requester_procs import shuffled_questions
from atrium.rerun_variance import (
    analytic_interval,
    build_matrices,
    read_outcomes,
    two_stage_bootstrap,
)
from atrium.routing.central import INBOX_SEPARATOR
from atrium.verdict import ALPHA, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED, write_verdict

logger = logging.getLogger(__name__)

# H-A1a：同等とみなす差の幅（Iteration 22 の同じ設定の組の差の最大（P 0.001・中央 0.006）の範囲で，非劣性の δ = 0.02 の 1/4）
EQUIVALENCE_MARGIN = 0.005
# H-A1L：処理量の比がこの範囲なら「変わらない」（F-6・D の流し直しの処理量の揺れの幅）
LOAD_RATIO_RANGE = (0.90, 1.10)
# ゲートの閾値
MATCH_THRESHOLD = 0.999
FAILURE_LIMIT = 0.01
# 採点の分散の推定：質問者 1 台の cross-encoder の採点の処理量（F-6 の制御点の RTX 3060 での実測．その GPU は廃止済み）
CE_SCORING_RATE_PER_S = 0.60
# 露出の曲線の質問者の数
CURVE_REQUESTER_COUNTS = (1, 2, 3, 5, 10, 20, 31)
RUN_SUFFIXES = ("n1_r1", "n1_r2", "n3_r1", "n3_r2")
GATE_RUN_SUFFIX = "n1_r1"
# 障害の記録：止めたノードへの再試行を数える requester.log の行の目印
RETRY_LOG_PATTERN = re.compile(r"retry", re.IGNORECASE)


def ordered_qids(questions_path: Path, seed: int) -> list[str]:
    """`atrium run`（質問者）が EnronQA の質問を並べ替える順（種で決まる shuffle）の質問 ID．質問者の割り当て i mod N の基準になる．"""
    return [q.qid for q in shuffled_questions(load_questions(questions_path, None), seed)]


def requester_of_position(position: int, n_requesters: int) -> int:
    """並べた順で position 番目の問を受け持つ質問者の番号（i mod N）．"""
    return position % n_requesters


# ── ゲート ────────────────────────────────────────────────────────────────────


def check_assignment(
    rows: Sequence[Mapping[str, Any]], order: Sequence[str], n_requesters: int
) -> dict[str, Any]:
    """G-A：各問がちょうど 1 台で処理され，質問者の番号が並べた順の i mod N と一致し，各行に requester_host が残っているか．"""
    expected = {qid: requester_of_position(i, n_requesters) for i, qid in enumerate(order)}
    by_qid: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_qid.setdefault(r["qid"], []).append(r)
    missing = sorted(set(expected) - set(by_qid))
    unexpected = sorted(set(by_qid) - set(expected))
    # 失敗した問を同じ run_id で流し直すと，同じ質問者の行が複数になる（別の質問者が処理した行があれば誤り）
    mismatched = sorted(
        qid
        for qid, rs in by_qid.items()
        if qid in expected and {r.get("requester_index") for r in rs} != {expected[qid]}
    )
    no_host = sorted(r["qid"] for r in rows if not r.get("requester_host"))
    return {
        "n_expected": len(expected),
        "n_missing": len(missing),
        "n_unexpected": len(unexpected),
        "n_wrong_requester": len(mismatched),
        "n_without_requester_host": len(no_host),
        "passes": not (missing or unexpected or mismatched or no_host),
    }


def retrieval_signature(row: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """1 問の候補の受信箱 → その候補の文脈の文書 ID（検索の同一性の比較用）．"""
    candidates = (row.get("owner_answer") or {}).get("candidates") or []
    return {c["source"]: tuple(c.get("context_doc_ids") or ()) for c in candidates}


def answer_signature(row: Mapping[str, Any]) -> dict[str, str]:
    """1 問の候補の受信箱 → 候補の回答の文字列．"""
    candidates = (row.get("owner_answer") or {}).get("candidates") or []
    return {c["source"]: c.get("answer") or "" for c in candidates}


def match_rate(
    rows_a: Sequence[Mapping[str, Any]],
    rows_b: Sequence[Mapping[str, Any]],
    signature: Any,
) -> dict[str, Any]:
    """2 つの実行の，両方で失敗していない同じ問のうち signature が一致する割合（G-N1・G-N3・候補の文字列の一致率）．"""
    a = {r["qid"]: r for r in rows_a if r.get("error") is None}
    b = {r["qid"]: r for r in rows_b if r.get("error") is None}
    shared = sorted(set(a) & set(b))
    matched = sum(signature(a[q]) == signature(b[q]) for q in shared)
    return {"n": len(shared), "matched": matched, "rate": matched / len(shared) if shared else None}


def chosen_match_rate(run_a: Path, run_b: Path) -> dict[str, Any]:
    """R3′ の選んだ候補の番号の一致率（派生の実行の chosen_index）．"""
    a = {r["qid"]: r.get("chosen_index") for r in read_results(run_a / "results.jsonl")}
    b = {r["qid"]: r.get("chosen_index") for r in read_results(run_b / "results.jsonl")}
    shared = sorted(set(a) & set(b))
    matched = sum(a[q] == b[q] for q in shared)
    return {"n": len(shared), "matched": matched, "rate": matched / len(shared) if shared else None}


# ── 精度（H-A1a・H-A1a′）─────────────────────────────────────────────────────


def decide_equivalence(
    boot: Sequence[float],
    analytic: Sequence[float],
    delta: float,
    margin: float = EQUIVALENCE_MARGIN,
) -> dict[str, Any]:
    """区間 1・2 の両方が [−margin, +margin] の中なら同等，両方が 0 を含まず |Δ| > margin なら変わる，それ以外は決まらない．"""
    inside = all(low >= -margin and high <= margin for low, high in (boot, analytic))
    excludes_zero = all(low > 0 or high < 0 for low, high in (boot, analytic))
    if inside:
        verdict = "equivalent"
    elif excludes_zero and abs(delta) > margin:
        verdict = "differs"
    else:
        verdict = "undecided"
    return {"margin": margin, "verdict": verdict}


def accuracy_comparison(
    n3_dirs: Sequence[Path],
    n1_dirs: Sequence[Path],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """N3 の実行（各回の採点済みの結果）と N1 の実行の正答率の差 Δ と，区間 1・2，同等の判定，N1 と N3 の組の McNemar．"""
    qids, y, info = build_matrices({"N3": n3_dirs, "N1": n1_dirs})
    y3, y1 = y["N3"], y["N1"]
    delta = float(y3.mean(axis=0).mean() - y1.mean(axis=0).mean())
    boot = two_stage_bootstrap(y3, y1, samples, seed)
    analytic = analytic_interval(y3, y1)
    pairs = []
    for i in range(y3.shape[0]):
        for j in range(y1.shape[0]):
            only_n3 = int(((y3[i] == 1) & (y1[j] == 0)).sum())
            only_n1 = int(((y3[i] == 0) & (y1[j] == 1)).sum())
            pairs.append(
                {
                    "n3_run": i + 1,
                    "n1_run": j + 1,
                    "diff": float(y3[i].mean() - y1[j].mean()),
                    "only_n3_correct": only_n3,
                    "only_n1_correct": only_n1,
                    "mcnemar_p": mcnemar_exact(only_n3, only_n1),
                }
            )
    return {
        "n_questions": len(qids),
        "inputs": info,
        "accuracy_by_run": {c: [float(v) for v in y[c].mean(axis=1)] for c in y},
        "delta": delta,
        "bootstrap_two_stage": {"ci95": list(boot), "samples": samples, "seed": seed},
        "analytic": analytic,
        "equivalence": decide_equivalence(boot, analytic["ci95"], delta),
        "n3_vs_n1_pairs": pairs,
        "qids": qids,
        "y_n3_mean": [float(v) for v in y3.mean(axis=0)],
        "y_n1_mean": [float(v) for v in y1.mean(axis=0)],
    }


def per_requester_difference(
    comparison: Mapping[str, Any], requester_of_qid: Mapping[str, int]
) -> dict[str, Any]:
    """質問者ごとの部分集合の差（同じ問の N1 との対応あり．N3 の平均 − N1 の平均）．"""
    groups: dict[int, list[float]] = {}
    for qid, a, b in zip(
        comparison["qids"], comparison["y_n3_mean"], comparison["y_n1_mean"], strict=True
    ):
        if qid in requester_of_qid:
            groups.setdefault(requester_of_qid[qid], []).append(a - b)
    return {str(k): {"n": len(v), "diff": float(np.mean(v))} for k, v in sorted(groups.items())}


# ── 負荷（H-A1L）─────────────────────────────────────────────────────────────


def throughput(run_dir: Path) -> dict[str, Any]:
    """処理量 X = 成功した問の数 ÷（全質問者の最後の finished_at − 最初の started_at）と，質問者ごとの処理量・e2e の p50・p95．"""
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    rows = read_results(run_dir / "results.jsonl")
    ok = [r for r in rows if r.get("error") is None]
    wall = meta["finished_at"] - meta["started_at"]
    e2e = [r["timings"]["e2e_s"] for r in ok if r.get("timings")]
    per_requester = []
    for req in meta.get("requesters") or []:
        n_ok = sum(r.get("requester_index") == req["requester_index"] for r in ok)
        span = req["finished_at"] - req["started_at"]
        per_requester.append(
            {
                "requester_index": req["requester_index"],
                "requester_host": req["requester_host"],
                "n_ok": n_ok,
                "wall_s": span,
                "throughput": n_ok / span if span > 0 else None,
                "started_at_offset_s": req["started_at"] - meta["started_at"],
                "requester_cpu_util": req.get("requester_cpu_util"),
                "control_cpu_busy": req.get("control_cpu_busy"),
            }
        )
    # 失敗した問を同じ run_id で流し直すと run_meta.json の finished_at・CPU の欄は流し直しのものに上書きされる．
    # phase1_run_meta.json（a1_phase1_snapshot.sh が 1 回目の終わりに写したもの）があれば，1 回目だけの処理量も出す
    first_pass = None
    phase1 = run_dir / "phase1_run_meta.json"
    if phase1.exists():
        meta1 = json.loads(phase1.read_text(encoding="utf-8"))
        n_rows1 = int((run_dir / "phase1_n_rows.txt").read_text(encoding="utf-8"))
        n_ok1 = n_rows1 - meta1["failures"]
        wall1 = meta1["finished_at"] - meta1["started_at"]
        first_pass = {"n_ok": n_ok1, "wall_s": wall1, "throughput": n_ok1 / wall1}
    return {
        "run": run_dir.name,
        "first_pass": first_pass,
        "n_ok": len(ok),
        "n_failed": len(rows) - len(ok),
        "wall_s": wall,
        "throughput": len(ok) / wall,
        "e2e_p50": float(np.percentile(e2e, 50)),
        "e2e_p95": float(np.percentile(e2e, 95)),
        "requesters": per_requester,
        "requester_cpu_util": meta.get("requester_cpu_util"),
        "control_cpu_busy": meta.get("control_cpu_busy"),
        "expert_cpu_util_max": meta.get("expert_cpu_util_max"),
        "expert_cpu_util": meta.get("expert_cpu_util"),
        "failure_rate": (len(rows) - len(ok)) / len(rows) if rows else None,
    }


def decide_load(ratios: Sequence[float], low_high: tuple[float, float] = LOAD_RATIO_RANGE) -> str:
    """H-A1L：全ての組の比が範囲内なら unchanged，全て上なら rises，全て下なら falls，それ以外は undecided．"""
    low, high = low_high
    if all(low <= r <= high for r in ratios):
        return "unchanged"
    if all(r > high for r in ratios):
        return "rises"
    if all(r < low for r in ratios):
        return "falls"
    return "undecided"


def scoring_dispersion_estimate(x_gen: float, max_requesters: int = 5) -> dict[str, Any]:
    """採点の分散の推定（記録．実機ではない）：X_live(N) = min(N × 0.60, X_gen)．律速が採点から生成へ移る最小の N も出す．"""
    curve = {n: min(n * CE_SCORING_RATE_PER_S, x_gen) for n in range(1, max_requesters + 1)}
    crossover = next((n for n in curve if n * CE_SCORING_RATE_PER_S >= x_gen), None)
    return {
        "scoring_rate_per_requester": CE_SCORING_RATE_PER_S,
        "x_gen": x_gen,
        "x_live_by_n": {str(n): v for n, v in curve.items()},
        "bottleneck_moves_to_generation_at_n": crossover,
        "note": "推定．採点の 0.60 問/秒は廃止された制御点の GPU での実測で，GPU を持たない質問者の PC の容量は測っていない",
    }


# ── 露出（H-A1E）─────────────────────────────────────────────────────────────


def inbox_of_doc_id(doc_id: str) -> str:
    """文書 ID（"<受信箱>/…"）の受信箱．"""
    return doc_id.split(INBOX_SEPARATOR, 1)[0]


def inboxes_by_host(placement: Mapping[str, Any]) -> dict[str, set[str]]:
    """placement.json（nodes[].host・shard_ids）から，PC → その PC が持つ受信箱の集合．"""
    return {n["host"]: set(n["shard_ids"]) for n in placement["nodes"]}


def candidate_doc_ids(row: Mapping[str, Any]) -> set[str]:
    """R3′ を実機で採るなら質問者へ渡る文書：全候補の context_doc_ids（失敗した行は空）．"""
    if row.get("error") is not None:
        return set()
    return {d for ids in retrieval_signature(row).values() for d in ids}


def exposure(
    rows: Sequence[Mapping[str, Any]],
    host_of_qid: Mapping[str, str],
    inboxes: Mapping[str, set[str]],
    total_docs: int,
) -> dict[str, Any]:
    """質問者 r が受け取る文書の集合 D_r から E_any = |∪ D_r| / 総数，E_pair = Σ|D_r| / 総数，E_max = max|D_r| / 総数を出す．

    D_r は，r が受け持った問の候補の文脈の文書から，r の PC が持つ受信箱の文書（その PC から出ていない）を除いたもの．
    """
    per_host: dict[str, set[str]] = {}
    for r in rows:
        host = host_of_qid.get(r["qid"])
        if host is None:
            continue
        own = inboxes.get(host, set())
        docs = {d for d in candidate_doc_ids(r) if inbox_of_doc_id(d) not in own}
        per_host.setdefault(host, set()).update(docs)
    union: set[str] = set().union(*per_host.values()) if per_host else set()
    sizes = {h: len(v) for h, v in per_host.items()}
    return {
        "total_docs": total_docs,
        "n_requesters": len(per_host),
        "docs_per_requester": sizes,
        "E_any": len(union) / total_docs,
        "E_pair": sum(sizes.values()) / total_docs,
        "E_max": max(sizes.values()) / total_docs if sizes else None,
    }


def exposure_curve(
    rows: Sequence[Mapping[str, Any]],
    order: Sequence[str],
    requester_hosts: Sequence[str],
    inboxes: Mapping[str, set[str]],
    total_docs: int,
    counts: Sequence[int] = CURVE_REQUESTER_COUNTS,
) -> dict[str, Any]:
    """質問者を requester_hosts の先頭 N 台にし，並べた順の i 番目の問を i mod N 番目の質問者へ割り当てたときの露出（オフライン）．"""
    out: dict[str, Any] = {}
    for n in counts:
        if n > len(requester_hosts):
            continue
        hosts = list(requester_hosts[:n])
        host_of_qid = {qid: hosts[requester_of_position(i, n)] for i, qid in enumerate(order)}
        out[str(n)] = exposure(rows, host_of_qid, inboxes, total_docs)
    return out


# ── 障害（H-A1F．記録）───────────────────────────────────────────────────────


def fault_record(run_dir: Path, stopped_hosts: Sequence[str]) -> dict[str, Any]:
    """障害の記録：質問者ごとの失敗の割合・失敗した問の e2e・止めたホストへの再試行の行の数，止めた各ホストに当たった質問者の数．"""
    rows = read_results(run_dir / "results.jsonl")
    by_requester: dict[int, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_requester.setdefault(r.get("requester_index", 0), []).append(r)
    per_requester: dict[str, Any] = {}
    for idx, rs in sorted(by_requester.items()):
        failed = [r for r in rs if r.get("error") is not None]
        e2e_failed = [r["timings"]["e2e_s"] for r in failed if r.get("timings")]
        log_path = run_dir / f"req{idx}" / "requester.log"
        if not log_path.exists() and idx == 0:
            log_path = run_dir / "requester.log"
        lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
        per_requester[str(idx)] = {
            "n": len(rs),
            "failure_rate": len(failed) / len(rs),
            "failed_e2e_p50": float(np.percentile(e2e_failed, 50)) if e2e_failed else None,
            "retry_lines_by_stopped_host": {
                h: sum(1 for line in lines if h in line and RETRY_LOG_PATTERN.search(line))
                for h in stopped_hosts
            },
        }
    requesters_hit = {
        h: sum(v["retry_lines_by_stopped_host"][h] > 0 for v in per_requester.values())
        for h in stopped_hosts
    }
    fault_log = run_dir / "fault_log.txt"
    return {
        "run": run_dir.name,
        "stopped_hosts": list(stopped_hosts),
        "per_requester": per_requester,
        "requesters_hitting_each_stopped_host": requesters_hit,
        "fault_log": fault_log.read_text(encoding="utf-8").splitlines()
        if fault_log.exists()
        else None,
    }


# ── 全体 ──────────────────────────────────────────────────────────────────────


def total_docs_of(run_dir: Path) -> int:
    """run_meta.json の sources（受信箱ごとの文書数）の合計（露出の割合の分母．EnronQA は 73,772 通）．"""
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    return int(sum(s["n_docs"] for s in meta["sources"]))


def evaluate(
    results_dir: Path,
    prefix: str,
    data_dir: Path,
    prior_run: Path | None,
    a6_run: Path | None,
    expert_hosts: Sequence[str],
    requester_host: str,
    stopped_hosts: Sequence[str],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """事前登録（journal の Iteration 23）の全ての量を求める．各回の実行は results_dir/<prefix>_<suffix>/ にある．"""
    run = {s: results_dir / f"{prefix}_{s}" for s in RUN_SUFFIXES}
    derived = {s: results_dir / f"{prefix}_{s}_R3p" for s in RUN_SUFFIXES}
    rows = {s: read_results(run[s] / "results.jsonl") for s in RUN_SUFFIXES}
    meta3 = json.loads((run["n3_r1"] / "run_meta.json").read_text(encoding="utf-8"))
    n_requesters = meta3["requester_count"]
    paths = dataset_paths(data_dir, "enronqa")
    questions_path = paths.questions.parent / QUESTION_FILES["holdout"]
    order = ordered_qids(questions_path, meta3["config"]["experiment"]["seed"])

    # ゲート
    gates: dict[str, Any] = {
        "G_A": {s: check_assignment(rows[s], order, n_requesters) for s in ("n3_r1", "n3_r2")}
    }
    if prior_run is not None:
        gates["G_N1"] = match_rate(
            rows[GATE_RUN_SUFFIX], read_results(prior_run / "results.jsonl"), retrieval_signature
        )
    gates["G_N3"] = {
        s: match_rate(rows[s], rows[GATE_RUN_SUFFIX], retrieval_signature)
        for s in ("n3_r1", "n3_r2")
    }
    gates["G3"] = {s: throughput(run[s])["failure_rate"] for s in RUN_SUFFIXES}
    for key in ("G_N1",):
        if key in gates:
            gates[key]["passes"] = (gates[key]["rate"] or 0) >= MATCH_THRESHOLD
    for g in gates["G_N3"].values():
        g["passes"] = (g["rate"] or 0) >= MATCH_THRESHOLD
    gates["G3_passes"] = all((v or 0) <= FAILURE_LIMIT for v in gates["G3"].values())

    # 精度
    requester_of_qid = {
        r["qid"]: r["requester_index"]
        for r in rows["n3_r1"]
        if r.get("requester_index") is not None
    }
    acc_r3 = accuracy_comparison(
        [derived["n3_r1"], derived["n3_r2"]], [derived["n1_r1"], derived["n1_r2"]], samples, seed
    )
    acc_r0 = accuracy_comparison(
        [run["n3_r1"], run["n3_r2"]], [run["n1_r1"], run["n1_r2"]], samples, seed
    )
    acc_r3["per_requester_difference"] = per_requester_difference(acc_r3, requester_of_qid)
    acc_r3["candidate_answer_match"] = {
        f"n3_{a}_vs_n1_{b}": match_rate(rows[f"n3_{a}"], rows[f"n1_{b}"], answer_signature)
        for a in ("r1", "r2")
        for b in ("r1", "r2")
    }
    acc_r3["chosen_index_match"] = {
        f"n3_{a}_vs_n1_{b}": chosen_match_rate(derived[f"n3_{a}"], derived[f"n1_{b}"])
        for a in ("r1", "r2")
        for b in ("r1", "r2")
    }
    unjudged = {
        s: read_outcomes(derived[s])[1]
        for s in RUN_SUFFIXES
        if (derived[s] / "judgements.jsonl").exists()
    }
    # 問ごとの大きな配列は，質問者ごとの差を出した後は出力に残さない
    for comparison in (acc_r3, acc_r0):
        for key in ("qids", "y_n3_mean", "y_n1_mean"):
            comparison.pop(key)
    # 負荷
    loads = {s: throughput(run[s]) for s in RUN_SUFFIXES}
    ratios = [
        loads["n3_r1"]["throughput"] / loads["n1_r1"]["throughput"],
        loads["n3_r2"]["throughput"] / loads["n1_r2"]["throughput"],
    ]
    x_gen = float(np.mean([loads["n1_r1"]["throughput"], loads["n1_r2"]["throughput"]]))
    load: dict[str, Any] = {
        "runs": loads,
        "ratio_n3_over_n1": {"r1": ratios[0], "r2": ratios[1]},
        "verdict": decide_load(ratios),
        "range": list(LOAD_RATIO_RANGE),
        "scoring_dispersion_estimate": scoring_dispersion_estimate(x_gen),
    }
    hi_dir = results_dir / f"{prefix}_n3_hi"
    if (hi_dir / "run_meta.json").exists():
        hi = throughput(hi_dir)
        load["n3_hi"] = {
            **hi,
            "throughput_ratio_to_n3_r2": hi["throughput"] / loads["n3_r2"]["throughput"],
            "e2e_p50_ratio_to_n3_r2": hi["e2e_p50"] / loads["n3_r2"]["e2e_p50"],
        }

    # 露出
    placement = json.loads((run[GATE_RUN_SUFFIX] / "placement.json").read_text(encoding="utf-8"))
    inboxes = inboxes_by_host(placement)
    total = total_docs_of(run[GATE_RUN_SUFFIX])
    exposure_runs: dict[str, Any] = {}
    for s in RUN_SUFFIXES:
        host_of_qid = {r["qid"]: r.get("requester_host") or requester_host for r in rows[s]}
        exposure_runs[s] = exposure(rows[s], host_of_qid, inboxes, total)
    extras = [d["host"] for d in meta3["config"]["cluster"].get("extra_requesters", [])]
    curve_hosts = [requester_host, *extras, *(h for h in expert_hosts if h not in extras)]
    curve: dict[str, Any] = {
        "requester_hosts_in_order": curve_hosts[: max(CURVE_REQUESTER_COUNTS)],
        "holdout_n1_r1": exposure_curve(rows[GATE_RUN_SUFFIX], order, curve_hosts, inboxes, total),
    }
    if a6_run is not None and (a6_run / "results.jsonl").exists():
        a6_meta = json.loads((a6_run / "run_meta.json").read_text(encoding="utf-8"))
        a6_order = ordered_qids(paths.questions, a6_meta["config"]["experiment"]["seed"])
        a6_rows = read_results(a6_run / "results.jsonl")
        curve["test_a6_descriptive_only"] = exposure_curve(
            a6_rows, a6_order, curve_hosts, inboxes, total_docs_of(a6_run)
        )
    exposure_result = {"runs": exposure_runs, "offline_curve": curve}

    # 障害（記録）
    fault: dict[str, Any] = {}
    for tag in ("f_n1", "f_n3"):
        d = results_dir / f"{prefix}_{tag}"
        fault[tag] = fault_record(d, stopped_hosts) if (d / "results.jsonl").exists() else "not run"

    return {
        "n_requesters": n_requesters,
        "gates": gates,
        "unjudged_counts_R3p": unjudged,
        "H_A1a": acc_r3,
        "H_A1a_prime": acc_r0,
        "H_A1L": load,
        "H_A1E": exposure_result,
        "H_A1F": fault,
        "alpha": ALPHA,
    }


def verdict_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """verdict.json の verdict_original に書く要約（仮説ごとの判定と，その根拠の数値）．"""
    a = result["H_A1a"]
    a0 = result["H_A1a_prime"]
    load = result["H_A1L"]
    return {
        "H_A1a": {
            "verdict": a["equivalence"]["verdict"],
            "margin": EQUIVALENCE_MARGIN,
            "delta": a["delta"],
            "bootstrap_two_stage_ci95": a["bootstrap_two_stage"]["ci95"],
            "analytic_ci95": a["analytic"]["ci95"],
        },
        "H_A1a_prime": {
            "verdict": a0["equivalence"]["verdict"],
            "delta": a0["delta"],
            "bootstrap_two_stage_ci95": a0["bootstrap_two_stage"]["ci95"],
            "analytic_ci95": a0["analytic"]["ci95"],
        },
        "H_A1L": {
            "verdict": load["verdict"],
            "ratio_n3_over_n1": load["ratio_n3_over_n1"],
            "range": load["range"],
        },
        "gates_pass": {
            "G_A": all(g["passes"] for g in result["gates"]["G_A"].values()),
            "G_N1": result["gates"].get("G_N1", {}).get("passes"),
            "G_N3": all(g["passes"] for g in result["gates"]["G_N3"].values()),
            "G3": result["gates"]["G3_passes"],
        },
    }


def write_outputs(out_dir: Path, result: Mapping[str, Any]) -> None:
    """multi_requester.json（全ての量）と verdict.json（verdict_original．一度書いたら書き換えない）を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "multi_requester.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    write_verdict(out_dir, verdict_summary(result), None)
