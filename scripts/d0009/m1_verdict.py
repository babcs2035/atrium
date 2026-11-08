"""d0009 M1（Iteration 26）：生成モデル（base の llama3.1:8b 対 Gemma 4 12B）の比較を集計し，results/d0009_m1_generator/verdict.json に書く．

使い方（リポジトリの直下で．生成・採点（m1_derive.py・m1_judge_j1.sh・abstain-judge）が終わった後）:
    uv run python scripts/d0009/m1_verdict.py [--out-dir results/d0009_m1_generator]
入力は，生成の 4 つの実行（base の H・H2，G4 の H・H2）と，派生の実行 results/d0009_m1_q_{base,g4}_R3p_{j1,jq}
（R3′ で選んだ最終回答を J1・参考の qwen3:8b で採点したもの）と，棄権の判定 <out-dir>/labels/<派生の実行>/abstain.jsonl（J1）．
統計量は既存の実装を使う：McNemar の正確検定（`atrium.analysis.compare_runs`・`mcnemar_exact`），質問の対応ありのブートストラップ区間
（`atrium.verdict.paired_bootstrap_ci`），σ²_u を足した解析的な区間（`atrium.rerun_variance.reread_test_pair`），Cohen の κ
（`atrium.judge.cohen_kappa`），3 分類（`atrium.fabrication_eval.classify`・`build_central_table`），露出（`atrium.multi_requester_eval`）．
H-M1 の結論は事前登録（journal の Iteration 26）の規則：α = 0.05 の単独の McNemar の正確検定（補正なし）．
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import numpy as np

from atrium import multi_requester_eval as mre
from atrium.analysis import compare_runs, mcnemar_exact, merge_judgements, read_results
from atrium.arrays import F64Array
from atrium.fabrication_eval import COLUMNS, build_central_table
from atrium.judge import cohen_kappa
from atrium.rerun_variance import reread_test_pair
from atrium.verdict import (
    ALPHA,
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
    paired_bootstrap_ci,
    write_verdict,
)

RESULTS = Path("results")
# 事前登録の判定の単位 Q = H ∪ H2．base の H は Iteration 22 の d0009_a3_p1
RUNS = {
    "base": {"H": "d0009_a3_p1", "H2": "d0009_m1_h2_base"},
    "g4": {"H": "d0009_m1_h_g4", "H2": "d0009_m1_h2_g4"},
}
# Iteration 22 の区間の作り方 2 で足す実行の水準の揺れ（P．両側に同じ値を足す）
SIGMA2_U_P = 1.63e-7
# 候補の受信箱の一致で，融合得点の差がこの値以下なら同点とみなす（G-S）
FUSED_TIE_TOLERANCE = 1e-4
G_S_LIMIT = 0.999
G_J_LIMIT = 0.01
G_K_LIMIT = 0.40
G3_LIMIT = 0.01
COST_TOTAL_S_RATIO_LIMIT = 2.0
FABRICATION_INCREASE_LIMIT = 0.005
# 人手での確かめに使う，判定が食い違う回答の数（生成モデルごと）と抽出の種
MANUAL_N = 20
MANUAL_SEED = 12


def derived(generator: str, judge: str) -> Path:
    """派生の実行（R3′ の最終回答を判定モデル judge（j1・jq）で採点したもの）の置き場所．"""
    return RESULTS / f"d0009_m1_q_{generator}_R3p_{judge}"


def read_rows(run_dir: Path) -> list[dict[str, Any]]:
    """results.jsonl に judgements.jsonl の正誤を付けた行（`atrium compare` と同じ）．"""
    rows = read_results(run_dir / "results.jsonl")
    judgements = run_dir / "judgements.jsonl"
    return merge_judgements(rows, judgements) if judgements.exists() else rows


def split_of(qid: str) -> str:
    """質問 ID が H（holdout）か H2（holdout2）か．"""
    return "H2" if qid.startswith("enronqa/holdout2/") else "H"


def accuracy_pair(judge: str) -> dict[str, Any]:
    """G4 − base の正答率の差：McNemar・区間 1（質問のブートストラップ）・区間 2（σ²_u を足す）・H と H2 の別・失敗を不正解とした感度．"""
    rows_g4, rows_base = read_rows(derived("g4", judge)), read_rows(derived("base", judge))
    cmp_all = compare_runs(rows_g4, rows_base)
    by_qid_g4 = {r["qid"]: r for r in rows_g4 if "correct" in r and r.get("error") is None}
    by_qid_base = {r["qid"]: r for r in rows_base if "correct" in r and r.get("error") is None}
    shared = sorted(set(by_qid_g4) & set(by_qid_base))
    diff = np.array(
        [float(by_qid_g4[q]["correct"]) - float(by_qid_base[q]["correct"]) for q in shared],
        dtype=np.float64,
    )
    low, high = paired_bootstrap_ci(diff)
    extended = reread_test_pair(
        derived("g4", judge), derived("base", judge), SIGMA2_U_P, SIGMA2_U_P
    )
    per_split: dict[str, Any] = {}
    for name in ("H", "H2"):
        sub = [q for q in shared if split_of(q) == name]
        a = [bool(by_qid_g4[q]["correct"]) for q in sub]
        b = [bool(by_qid_base[q]["correct"]) for q in sub]
        per_split[name] = {
            "n": len(sub),
            "accuracy_g4": sum(a) / len(sub),
            "accuracy_base": sum(b) / len(sub),
            "delta": (sum(a) - sum(b)) / len(sub),
            "only_g4_correct": sum(x and not y for x, y in zip(a, b, strict=True)),
            "only_base_correct": sum(y and not x for x, y in zip(a, b, strict=True)),
        }
    # 感度：失敗した行・判定が付かなかった行も不正解として数える（質問の和集合）
    all_qids = sorted({r["qid"] for r in rows_g4} | {r["qid"] for r in rows_base})
    ok_g4 = {q: bool(by_qid_g4[q]["correct"]) if q in by_qid_g4 else False for q in all_qids}
    ok_base = {q: bool(by_qid_base[q]["correct"]) if q in by_qid_base else False for q in all_qids}
    b_count = sum(ok_g4[q] and not ok_base[q] for q in all_qids)
    c_count = sum(ok_base[q] and not ok_g4[q] for q in all_qids)
    delta = float(diff.mean())
    p_value = cmp_all["mcnemar_p"]
    conclusion = (
        "improved"
        if p_value < ALPHA and delta > 0
        else "worse"
        if p_value < ALPHA and delta < 0
        else "no_detectable_difference"
    )
    return {
        "judge": judge,
        "n": len(shared),
        "accuracy_g4": cmp_all["accuracy_a"],
        "accuracy_base": cmp_all["accuracy_b"],
        "delta": delta,
        "b_only_g4_correct": cmp_all["only_a_correct"],
        "c_only_base_correct": cmp_all["only_b_correct"],
        "mcnemar_p": p_value,
        "alpha": ALPHA,
        "conclusion": conclusion,
        "interval1_bootstrap95": [low, high],
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED, "paired": True},
        "interval2_sigma2_added95": extended["extended_ci95"],
        "interval2_detail": {k: extended[k] for k in ("se_q", "se_total", "df", "t")},
        "per_split": per_split,
        "sensitivity_failures_as_wrong": {
            "n": len(all_qids),
            "b": b_count,
            "c": c_count,
            "mcnemar_p": mcnemar_exact(b_count, c_count),
            "delta": (sum(ok_g4.values()) - sum(ok_base.values())) / len(all_qids),
        },
    }


def judge_agreement() -> dict[str, Any]:
    """G-J（判定不能の割合）・G-K と κ：同じ最終回答に対する J1 と参考の qwen3:8b の正誤の Cohen の κ．"""
    out: dict[str, Any] = {"per_generator": {}}
    pooled_a: list[bool] = []
    pooled_b: list[bool] = []
    for generator in RUNS:
        j1 = {r["qid"]: r for r in read_rows(derived(generator, "j1")) if r.get("error") is None}
        jq = {r["qid"]: r for r in read_rows(derived(generator, "jq")) if r.get("error") is None}
        both = sorted(
            q
            for q in set(j1) & set(jq)
            if "correct" in j1[q]
            and "correct" in jq[q]
            and not j1[q]["judge_unparsed"]
            and not jq[q]["judge_unparsed"]
        )
        a = [bool(j1[q]["correct"]) for q in both]
        b = [bool(jq[q]["correct"]) for q in both]
        pooled_a += a
        pooled_b += b
        out["per_generator"][generator] = {
            "n": len(both),
            "kappa": cohen_kappa(a, b),
            "agreement": sum(x == y for x, y in zip(a, b, strict=True)) / len(both),
            "accuracy_j1": sum(a) / len(both),
            "accuracy_jq": sum(b) / len(both),
            "unparsed_j1": sum(bool(r.get("judge_unparsed")) for r in j1.values()) / len(j1),
            "unparsed_jq": sum(bool(r.get("judge_unparsed")) for r in jq.values()) / len(jq),
        }
    out["pooled_kappa"] = cohen_kappa(pooled_a, pooled_b)
    base = out["per_generator"]["base"]
    out["G_K"] = {
        "base_kappa": base["kappa"],
        "limit": G_K_LIMIT,
        "passes": base["kappa"] >= G_K_LIMIT,
    }
    worst = max(g[k] for g in out["per_generator"].values() for k in ("unparsed_j1", "unparsed_jq"))
    out["G_J"] = {"max_unparsed_share": worst, "limit": G_J_LIMIT, "passes": worst <= G_J_LIMIT}
    return out


def answer_length_of_correct() -> dict[str, Any]:
    """J1 が正解とした最終回答の文字数の中央値（判定モデルの好みの確かめ．記述）．"""
    out: dict[str, Any] = {}
    for generator in RUNS:
        run = derived(generator, "j1")
        answer = {
            r["qid"]: str(r.get("final_answer", "")) for r in read_results(run / "results.jsonl")
        }
        lengths = [len(answer[r["qid"]]) for r in read_rows(run) if r.get("correct") is True]
        out[generator] = {"n_correct": len(lengths), "median_chars": statistics.median(lengths)}
    return out


def fabrication(out_dir: Path) -> dict[str, Any]:
    """H-M2：J1 での 3 分類（棄権の判定は J1）．作り話の割合の base と G4 の差と，対応ありのブートストラップ区間．"""
    labels = out_dir / "labels"
    tables: dict[str, F64Array] = {}
    qids: dict[str, list[str]] = {}
    for generator in RUNS:
        run = derived(generator, "j1")
        qids[generator] = [r["qid"] for r in read_results(run / "results.jsonl")]
        tables[generator] = build_central_table(run, labels, qids[generator])
    if qids["base"] != qids["g4"]:
        shared = sorted(set(qids["base"]) & set(qids["g4"]))
        for generator in RUNS:
            index = {q: i for i, q in enumerate(qids[generator])}
            tables[generator] = tables[generator][[index[q] for q in shared]]
    col = {name: i for i, name in enumerate(COLUMNS)}
    rates: dict[str, Any] = {}
    for generator, table in tables.items():
        n_judged = float(table.shape[0] - table[:, col["unjudged"]].sum())
        rates[generator] = {
            "n": int(table.shape[0]),
            "n_judged": n_judged,
            **{name: float(table[:, col[name]].sum()) for name in COLUMNS},
            "fabricated_share_of_all": float(table[:, col["fabricated"]].sum() / table.shape[0]),
            "fabricated_share_of_judged": float(table[:, col["fabricated"]].sum() / n_judged),
            "fabricated_upper_incl_unparsed": float(
                (table[:, col["fabricated"]] + table[:, col["unparsed"]]).sum() / table.shape[0]
            ),
        }
    diff = tables["g4"][:, col["fabricated"]] - tables["base"][:, col["fabricated"]]
    low, high = paired_bootstrap_ci(diff)
    increase = float(diff.mean())
    return {
        "judge": "j1",
        "per_generator": rates,
        "increase_g4_minus_base": increase,
        "bootstrap_ci95": [low, high],
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED, "paired": True},
        "increase_within_limit": increase <= FABRICATION_INCREASE_LIMIT,
        "limit": FABRICATION_INCREASE_LIMIT,
    }


def exposure() -> dict[str, Any]:
    """H-M3：質問者 1 台（N = 1）の E_any と，G4 と base で質問者へ渡る文書・委託で送る文書の件数が同じか．"""
    out: dict[str, Any] = {"per_run": {}, "per_generator_q": {}}
    for generator, runs in RUNS.items():
        rows_all: list[dict[str, Any]] = []
        total_docs = 0
        for name, run_name in runs.items():
            run = RESULTS / run_name
            rows = read_results(run / "results.jsonl")
            total = mre.total_docs_of(run)
            total_docs = total
            host_of = {r["qid"]: "requester" for r in rows}
            out["per_run"][run_name] = mre.exposure(rows, host_of, {}, total) | {"split": name}
            rows_all += rows
        host_of_all = {r["qid"]: "requester" for r in rows_all}
        out["per_generator_q"][generator] = mre.exposure(rows_all, host_of_all, {}, total_docs)
    # 質問者へ渡る文脈の文書の件数と，委託で GPU の PC へ送る文書の件数（node_answers の docs_sent）の合計
    totals: dict[str, dict[str, int]] = {}
    for generator, runs in RUNS.items():
        context = sent = 0
        for run_name in runs.values():
            for row in read_results(RESULTS / run_name / "results.jsonl"):
                for answer in row.get("node_answers") or []:
                    context += int(answer.get("n_context_docs") or 0)
                    sent += int(answer.get("docs_sent") or 0)
        totals[generator] = {"n_context_docs": context, "docs_sent_to_generator": sent}
    out["document_counts"] = totals
    signature: dict[str, Any] = {}
    for name in ("H", "H2"):
        rows_g4 = read_results(RESULTS / RUNS["g4"][name] / "results.jsonl")
        rows_base = read_results(RESULTS / RUNS["base"][name] / "results.jsonl")
        signature[name] = {
            "context_docs_same": mre.match_rate(rows_g4, rows_base, mre.retrieval_signature),
            "candidate_answers_same": mre.match_rate(rows_g4, rows_base, mre.answer_signature),
        }
    out["context_doc_signature_g4_vs_base"] = signature
    return out


def candidate_inbox_match() -> dict[str, Any]:
    """G-S：G4 の実行の候補の受信箱の集合が base と一致する割合（融合得点の差 ≤ 1e-4 は同点として許す）．"""
    out: dict[str, Any] = {}
    total = same = 0
    for name in ("H", "H2"):
        base = {
            r["qid"]: r
            for r in read_results(RESULTS / RUNS["base"][name] / "results.jsonl")
            if r.get("error") is None
        }
        g4 = {
            r["qid"]: r
            for r in read_results(RESULTS / RUNS["g4"][name] / "results.jsonl")
            if r.get("error") is None
        }
        shared = sorted(set(base) & set(g4))
        strict = tolerant = 0
        for q in shared:
            cb = (base[q].get("owner_answer") or {}).get("candidates") or []
            cg = (g4[q].get("owner_answer") or {}).get("candidates") or []
            sb, sg = sorted(c["source"] for c in cb), sorted(c["source"] for c in cg)
            if sb == sg:
                strict += 1
                tolerant += 1
                continue
            fb = sorted(float(c.get("probe_fused") or 0.0) for c in cb)
            fg = sorted(float(c.get("probe_fused") or 0.0) for c in cg)
            if len(fb) == len(fg) and all(
                abs(x - y) <= FUSED_TIE_TOLERANCE for x, y in zip(fb, fg, strict=True)
            ):
                tolerant += 1
        out[name] = {"n": len(shared), "strict_same": strict, "tie_tolerant_same": tolerant}
        total += len(shared)
        same += tolerant
    out["rate_tie_tolerant"] = same / total
    out["G_S"] = {"limit": G_S_LIMIT, "passes": same / total >= G_S_LIMIT}
    return out


def cost() -> dict[str, Any]:
    """H-M4：候補 1 件あたりの prefill_s・decode_s・total_s・output_tokens の中央値と p95，各実行の処理量と失敗の割合（G3）．"""
    out: dict[str, Any] = {"per_generator": {}, "runs": {}}
    for generator, runs in RUNS.items():
        values: dict[str, list[float]] = {
            k: [] for k in ("prefill_s", "decode_s", "total_s", "output_tokens")
        }
        for run_name in runs.values():
            for row in read_results(RESULTS / run_name / "results.jsonl"):
                for na in row.get("node_answers") or []:
                    for key in values:
                        if na.get(key) is not None:
                            values[key].append(float(na[key]))
            rows = read_results(RESULTS / run_name / "results.jsonl")
            failed = sum(r.get("error") is not None for r in rows)
            run_info: dict[str, Any] = {
                "n": len(rows),
                "failed": failed,
                "failure_rate": failed / len(rows),
            }
            if (RESULTS / run_name / "run_meta.json").exists():
                run_info["throughput"] = mre.throughput(RESULTS / run_name)
            out["runs"][run_name] = run_info
        out["per_generator"][generator] = {
            key: {
                "n": len(v),
                "median": float(np.percentile(v, 50)),
                "p95": float(np.percentile(v, 95)),
            }
            for key, v in values.items()
        }
    ratio = (
        out["per_generator"]["g4"]["total_s"]["median"]
        / out["per_generator"]["base"]["total_s"]["median"]
    )
    out["total_s_median_ratio_g4_over_base"] = ratio
    out["total_s_within_limit"] = ratio <= COST_TOTAL_S_RATIO_LIMIT
    out["G3"] = {
        "max_failure_rate": max(r["failure_rate"] for r in out["runs"].values()),
        "limit": G3_LIMIT,
    }
    out["G3"]["passes"] = out["G3"]["max_failure_rate"] <= G3_LIMIT
    return out


def manual_check_sample() -> dict[str, Any]:
    """人手での確かめの対象：J1 と qwen3:8b の判定が食い違う最終回答から，生成モデルごとに MANUAL_N 件（種 MANUAL_SEED）を選ぶ（agent が読む表）．"""
    rng = np.random.default_rng(MANUAL_SEED)
    out: dict[str, Any] = {}
    for generator in RUNS:
        j1 = {r["qid"]: r for r in read_rows(derived(generator, "j1")) if r.get("error") is None}
        jq = {r["qid"]: r for r in read_rows(derived(generator, "jq")) if r.get("error") is None}
        answer = {
            r["qid"]: str(r.get("final_answer", ""))
            for r in read_results(derived(generator, "j1") / "results.jsonl")
        }
        disagree = sorted(
            q
            for q in set(j1) & set(jq)
            if "correct" in j1[q]
            and "correct" in jq[q]
            and bool(j1[q]["correct"]) != bool(jq[q]["correct"])
        )
        picked = [disagree[int(i)] for i in rng.permutation(len(disagree))[:MANUAL_N]]
        out[generator] = {
            "n_disagree": len(disagree),
            "picked": [
                {
                    "qid": q,
                    "answer": answer[q],
                    "j1_correct": bool(j1[q]["correct"]),
                    "jq_correct": bool(jq[q]["correct"]),
                }
                for q in picked
            ],
        }
    return out


def main() -> None:
    """集計して verdict.json（verdict_original）と補助の記録を書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(RESULTS / "d0009_m1_generator"))
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    h_m1 = accuracy_pair("j1")
    reference = accuracy_pair("jq")
    agreement = judge_agreement()
    costs = cost()
    fab = fabrication(out_dir)
    gates = {
        "G_S": candidate_inbox_match()["G_S"],
        "G3": costs["G3"],
        "G_J": agreement["G_J"],
        "G_K": agreement["G_K"],
    }
    adoption = {
        "improved": h_m1["conclusion"] == "improved",
        "fabrication_increase_within_limit": fab["increase_within_limit"],
        "total_s_within_limit": costs["total_s_within_limit"],
        "G_K_passes": agreement["G_K"]["passes"],
    }
    adoption["adopt_g4_as_main_generator"] = all(adoption.values())
    detail = {
        "H_M1_j1": h_m1,
        "reference_qwen3_8b": reference,
        "kappa": agreement,
        "answer_length_of_correct": answer_length_of_correct(),
        "H_M2_fabrication": fab,
        "H_M3_exposure": exposure(),
        "H_M4_cost": costs,
        "G_S": candidate_inbox_match(),
        "gates": gates,
        "adoption": adoption,
        "limit": (
            "判定モデルは J1（Qwen3 14B）の 1 つで，系統の違う判定モデルで確かめていない．"
            "参考の qwen3:8b も同じ Qwen の系統である．"
        ),
    }
    (out_dir / "m1_detail.json").write_text(
        json.dumps(detail, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "manual_check_sample.json").write_text(
        json.dumps(manual_check_sample(), indent=1, ensure_ascii=False), encoding="utf-8"
    )
    original = {
        "hypothesis": "H-M1：生成モデルを Gemma 4 12B に替えると R3′ の最終回答の正答率が上がる（判定モデル 1 つ（Qwen3 14B））",
        "conclusion": h_m1["conclusion"],
        "delta": h_m1["delta"],
        "n": h_m1["n"],
        "b_only_g4_correct": h_m1["b_only_g4_correct"],
        "c_only_base_correct": h_m1["c_only_base_correct"],
        "mcnemar_p": h_m1["mcnemar_p"],
        "interval1_bootstrap95": h_m1["interval1_bootstrap95"],
        "interval2_sigma2_added95": h_m1["interval2_sigma2_added95"],
        "gates": gates,
        "adoption": adoption,
        "limit": detail["limit"],
    }
    print(json.dumps(write_verdict(out_dir, original), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
