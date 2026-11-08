"""d0009 PF（Iteration 27）：回答のプロンプトの形（fill_in 対 direct）の比較を集計し，results/d0009_pf_prompt/ に書く．

使い方（リポジトリの直下で．生成・採点（pf_score.sh）が終わった後）:
    uv run python scripts/d0009/pf_verdict.py dev      # G-D・G-S・G-J・G3・G-F（dev．H・H2 に進むかの判定）→ verdict_dev.json
    uv run python scripts/d0009/pf_verdict.py full     # Q = H ∪ H2 の H-PF1・H-PF4・G-S・G-J・G3（作り話・露出は pf_verdict_extra.py）→ verdict.json
比べる相手（fill_in）：dev は results/p0007_a6_dev（R3′ の派生 results/d0009_pf_base_dev_R3p），H は d0009_a3_p1（派生 d0009_a3_p1_R3p），
H2 は d0009_m1_h2_base（派生 d0009_m1_h2_base_R3p）．direct は results/d0009_pf_<split>_direct（派生 <run>_R3p）．
Δ = acc(direct の R3′) − acc(fill_in の R3′)．失敗・判定不能は不正解として数え，分母は質問集合の全問（事前登録）．
統計量は既存の実装を使う：McNemar の正確検定（`atrium.analysis.mcnemar_exact`・`compare_runs`），質問の対応ありのブートストラップ区間
（`atrium.verdict.paired_bootstrap_ci`．10,000 回・種 12），σ²_u を足した解析的な区間（`atrium.rerun_variance.reread_test_pair`）．
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from atrium import multi_requester_eval as mre
from atrium.analysis import compare_runs, mcnemar_exact, merge_judgements, read_results
from atrium.rerun_variance import reread_test_pair
from atrium.verdict import BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED, paired_bootstrap_ci

RESULTS = Path("results")
OUT_DIR = RESULTS / "d0009_pf_prompt"
# 分割ごとの（fill_in の生の実行，fill_in の R3′ の派生，direct の生の実行）
SPLITS = {
    "dev": ("p0007_a6_dev", "d0009_pf_base_dev_R3p", "d0009_pf_dev_direct"),
    "H": ("d0009_a3_p1", "d0009_a3_p1_R3p", "d0009_pf_h_direct"),
    "H2": ("d0009_m1_h2_base", "d0009_m1_h2_base_R3p", "d0009_pf_h2_direct"),
}
# Iteration 22 の区間の作り方 2 で足す実行の水準の揺れ（P．両側に同じ値を足す）
SIGMA2_U_P = 1.63e-7
G_D_MIN = -0.010
NON_INFERIORITY_MARGIN = 0.02
G_S_MIN = 0.99
G_J_MAX = 0.01
G3_MAX = 0.01
G_F_MAX = 0.01
NUM_PREDICT = 2048
# 候補の受信箱の一致で，融合得点の差がこの値以下なら同点とみなす（G-S）
FUSED_TIE_TOLERANCE = 1e-4


def outcome_map(derived_dir: Path) -> dict[str, bool]:
    """質問 ID → 正誤．失敗・判定不能・採点されていない問は不正解（False）にする（分母は質問集合の全問）．"""
    rows = read_results(derived_dir / "results.jsonl")
    judgements = derived_dir / "judgements.jsonl"
    if judgements.exists():
        rows = merge_judgements(rows, judgements)
    return {
        r["qid"]: bool(
            r.get("error") is None
            and "correct" in r
            and not r.get("judge_unparsed")
            and r["correct"]
        )
        for r in rows
    }


def paired_accuracy(direct: Path, base: Path) -> dict[str, Any]:
    """direct − fill_in の正答率の差：McNemar の b : c と正確検定・区間 1（質問のブートストラップ）・成功し判定の付いた問に絞った感度．"""
    d, b = outcome_map(direct), outcome_map(base)
    shared = sorted(set(d) & set(b))
    diff = np.array([float(d[q]) - float(b[q]) for q in shared], dtype=np.float64)
    only_d = sum(d[q] and not b[q] for q in shared)
    only_b = sum(b[q] and not d[q] for q in shared)
    low, high = paired_bootstrap_ci(diff)
    # 感度：成功し判定の付いた問だけ（`atrium compare` と同じ分母）
    cmp_ok = compare_runs(_rows(direct), _rows(base))
    return {
        "n": len(shared),
        "accuracy_direct": sum(d[q] for q in shared) / len(shared),
        "accuracy_fill_in": sum(b[q] for q in shared) / len(shared),
        "delta": float(diff.mean()),
        "b_only_direct_correct": only_d,
        "c_only_fill_in_correct": only_b,
        "mcnemar_p": mcnemar_exact(only_d, only_b),
        "interval1_bootstrap95": [low, high],
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED, "paired": True},
        "sensitivity_judged_only": {
            "n_shared": cmp_ok["n_shared"],
            "accuracy_direct": cmp_ok["accuracy_a"],
            "accuracy_fill_in": cmp_ok["accuracy_b"],
            "b": cmp_ok["only_a_correct"],
            "c": cmp_ok["only_b_correct"],
            "mcnemar_p": cmp_ok["mcnemar_p"],
        },
    }


def _rows(derived_dir: Path) -> list[dict[str, Any]]:
    """派生の実行に judgements.jsonl の正誤を付けた行．"""
    rows = read_results(derived_dir / "results.jsonl")
    return merge_judgements(rows, derived_dir / "judgements.jsonl")


def judge_and_failure_gates(derived_dir: Path, raw_dir: Path) -> dict[str, Any]:
    """G-J（最終回答の判定不能の割合）と G3（生の実行の失敗の割合）．"""
    rows = _rows(derived_dir)
    unparsed = sum(bool(r.get("judge_unparsed")) for r in rows) / len(rows)
    unjudged = sum("correct" not in r and r.get("error") is None for r in rows)
    raw = read_results(raw_dir / "results.jsonl")
    failed = sum(r.get("error") is not None for r in raw)
    return {
        "G_J": {"unparsed_share": unparsed, "unjudged": unjudged, "passes": unparsed <= G_J_MAX},
        "G3": {
            "n": len(raw),
            "failed": failed,
            "rate": failed / len(raw),
            "passes": failed / len(raw) <= G3_MAX,
        },
    }


def candidate_inbox_match(
    raw_direct: Path, raw_base: Path, derived_direct: Path, derived_base: Path
) -> dict[str, Any]:
    """G-S：direct の候補の受信箱の集合が fill_in と一致する割合（融合得点の差 ≤ 1e-4 は同点として許す）と，R3′ で選んだ受信箱の一致の割合．"""

    def candidates(path: Path) -> dict[str, list[dict[str, Any]]]:
        return {
            r["qid"]: (r.get("owner_answer") or {}).get("candidates") or []
            for r in read_results(path / "results.jsonl")
            if r.get("error") is None
        }

    cd, cb = candidates(raw_direct), candidates(raw_base)
    shared = sorted(set(cd) & set(cb))
    strict = tolerant = 0
    for q in shared:
        sd, sb = sorted(c["source"] for c in cd[q]), sorted(c["source"] for c in cb[q])
        if sd == sb:
            strict += 1
            tolerant += 1
            continue
        fd = sorted(float(c.get("probe_fused") or 0.0) for c in cd[q])
        fb = sorted(float(c.get("probe_fused") or 0.0) for c in cb[q])
        if len(fd) == len(fb) and all(
            abs(x - y) <= FUSED_TIE_TOLERANCE for x, y in zip(fd, fb, strict=True)
        ):
            tolerant += 1
    chosen_d = {
        r["qid"]: r.get("chosen_source") for r in read_results(derived_direct / "results.jsonl")
    }
    chosen_b = {
        r["qid"]: r.get("chosen_source") for r in read_results(derived_base / "results.jsonl")
    }
    both = sorted(set(chosen_d) & set(chosen_b))
    same_chosen = sum(chosen_d[q] == chosen_b[q] for q in both)
    rate = tolerant / len(shared)
    return {
        "n_shared": len(shared),
        "strict_same": strict,
        "tie_tolerant_same": tolerant,
        "rate_tie_tolerant": rate,
        "chosen_source_same": same_chosen,
        "chosen_source_n": len(both),
        "chosen_source_rate": same_chosen / len(both),
        "G_S": {"limit": G_S_MIN, "passes": rate >= G_S_MIN},
    }


def cost_and_shape(raw_dir: Path) -> dict[str, Any]:
    """H-PF4：候補 1 件あたりの output_tokens・decode_s・total_s の中央値と p95，上限に達した割合，採った出力に Answer: の行が無い割合，処理量．"""
    from atrium import prompts

    values: dict[str, list[float]] = {
        k: [] for k in ("output_tokens", "prefill_s", "decode_s", "total_s")
    }
    rows = read_results(raw_dir / "results.jsonl")
    for row in rows:
        for na in row.get("node_answers") or []:
            for key in values:
                if na.get(key) is not None:
                    values[key].append(float(na[key]))
    chosen_raw = [str(r.get("answer") or "") for r in rows if r.get("error") is None]
    out: dict[str, Any] = {
        key: {
            "n": len(v),
            "median": float(np.percentile(v, 50)),
            "p95": float(np.percentile(v, 95)),
        }
        for key, v in values.items()
    }
    out["hit_cap_rate"] = sum(t >= NUM_PREDICT for t in values["output_tokens"]) / len(
        values["output_tokens"]
    )
    out["chosen_without_answer_line_rate"] = sum(
        prompts._FREE_ANSWER.search(a) is None for a in chosen_raw
    ) / len(chosen_raw)
    if (raw_dir / "run_meta.json").exists():
        out["throughput"] = mre.throughput(raw_dir)
    return out


def split_report(name: str) -> dict[str, Any]:
    """1 つの分割（dev・H・H2）の Δ・ゲート・費用と形を集める．"""
    raw_base, derived_base, raw_direct = (RESULTS / x for x in SPLITS[name])
    derived_direct = RESULTS / f"{raw_direct.name}_R3p"
    return {
        "runs": {"fill_in": raw_base.name, "direct": raw_direct.name},
        "accuracy": paired_accuracy(derived_direct, derived_base),
        **judge_and_failure_gates(derived_direct, raw_direct),
        "G_S_detail": candidate_inbox_match(raw_direct, raw_base, derived_direct, derived_base),
        "cost_direct": cost_and_shape(raw_direct),
        "cost_fill_in": cost_and_shape(raw_base),
    }


def write_combined_q(name: str, dirs: list[Path]) -> Path:
    """H と H2 の派生の実行をつないだ質問集合 Q の実行（qid・error・正誤だけ．回答の文は書かない）を書く．"""
    out = OUT_DIR / f"q_{name}"
    out.mkdir(parents=True, exist_ok=True)
    with (
        (out / "results.jsonl").open("w", encoding="utf-8") as fr,
        (out / "judgements.jsonl").open("w", encoding="utf-8") as fj,
    ):
        for d in dirs:
            for r in read_results(d / "results.jsonl"):
                fr.write(json.dumps({"qid": r["qid"], "error": r.get("error")}) + "\n")
            for line in (d / "judgements.jsonl").read_text(encoding="utf-8").splitlines():
                fj.write(line + "\n")
    return out


def main() -> None:
    """dev または full の集計を書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["dev", "full"])
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.stage == "dev":
        report = split_report("dev")
        delta = report["accuracy"]["delta"]
        report["G_F"] = {
            "hit_cap_rate": report["cost_direct"]["hit_cap_rate"],
            "limit": G_F_MAX,
            "passes": report["cost_direct"]["hit_cap_rate"] <= G_F_MAX,
        }
        report["G_D"] = {"delta_dev": delta, "min": G_D_MIN, "passes": delta >= G_D_MIN}
        (OUT_DIR / "verdict_dev.json").write_text(
            json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return
    reports = {name: split_report(name) for name in ("H", "H2")}
    q_direct = write_combined_q("direct", [RESULTS / f"{SPLITS[n][2]}_R3p" for n in ("H", "H2")])
    q_base = write_combined_q("fill_in", [RESULTS / SPLITS[n][1] for n in ("H", "H2")])
    accuracy = paired_accuracy(q_direct, q_base)
    extended = reread_test_pair(q_direct, q_base, SIGMA2_U_P, SIGMA2_U_P)
    accuracy["interval2_sigma2_added95"] = extended["extended_ci95"]
    low1, high1 = accuracy["interval1_bootstrap95"]
    low2, high2 = accuracy["interval2_sigma2_added95"]
    if low1 > 0 and low2 > 0:
        conclusion = "improved"
    elif high1 < 0 and high2 < 0:
        conclusion = "worse_within_margin" if min(low1, low2) > -NON_INFERIORITY_MARGIN else "worse"
    elif min(low1, low2) > -NON_INFERIORITY_MARGIN:
        conclusion = "non_inferior"
    else:
        conclusion = "undetermined"
    accuracy["conclusion"] = conclusion
    result = {"H_PF1_Q": accuracy, "per_split": reports}
    (OUT_DIR / "verdict_full.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
