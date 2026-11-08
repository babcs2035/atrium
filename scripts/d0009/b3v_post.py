"""d0009 B3（Iteration 28）：V2 と R3′ が選んだ最終回答の派生の実行（回答の文を含む）と，作り話の 3 分類・露出・費用・記述の集計．

使い方（リポジトリの直下で．`atrium answer-select-eval --stage apply` の後．実機は使わない）:
    uv run python scripts/d0009/b3v_post.py derive     # results/d0009_b3v_q_{R3p,V2}（Q = H ∪ H2．final_answer と候補の判定から求めた judgements.jsonl）
    # （gpu4 の判定 container で）
    uv run atrium abstain-judge --ollama-url http://127.0.0.1:11436 --run-dir results/d0009_b3v_q_R3p --run-dir results/d0009_b3v_q_V2 \\
        --out-dir results/d0009_b3_verify/labels --resume
    uv run python scripts/d0009/b3v_post.py report     # results/d0009_b3_verify/post.json（3 分類・露出・費用・天井・人手の確かめの表）
採点は保存した候補の qwen3:8b の判定（`atrium judge --with-candidates`）をそのまま使う（選んだ候補の正誤）．
統計は既存の実装：3 分類 `atrium.fabrication_eval.build_central_table`，対応ありのブートストラップ `atrium.verdict.paired_bootstrap_ci`，
露出 `atrium.multi_requester_eval.exposure`．
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from atrium import multi_requester_eval as mre
from atrium.analysis import read_results
from atrium.answer_select_eval import (
    ANSWER_VERIFIER_SELECTOR,
    choose_for,
    existing_r3p_outcomes,
    load_questions,
    load_selector,
    outcomes_of,
)
from atrium.fabrication_eval import COLUMNS, build_central_table
from atrium.select_eval import CANDIDATE_JUDGEMENTS, QuestionOutcome, read_candidate_judgements
from atrium.verdict import BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED, paired_bootstrap_ci

RESULTS = Path("results")
OUT = RESULTS / "d0009_b3_verify"
# 質問集合 Q = H ∪ H2：（質問と候補の実行，検証の得点，露出の分母を読む実行）
RUNS = {
    "H": (
        RESULTS / "d0009_a3_p1",
        RESULTS / "d0009_b3v_h" / "verify_features.jsonl",
        RESULTS / "d0009_a3_p1",
    ),
    "H2": (
        RESULTS / "d0009_b3v_h2",
        RESULTS / "d0009_b3v_h2" / "verify_features.jsonl",
        RESULTS / "d0009_m1_h2_base",
    ),
}
DEV_VERIFY = RESULTS / "d0009_b3v_dev" / "verify_features.jsonl"
RULES = ("R3p", "V2")
MANUAL_N = 30
MANUAL_SEED = 12
FABRICATION_INCREASE_LIMIT = 0.005


def derived(rule: str) -> Path:
    """規則 rule の選んだ最終回答の派生の実行の置き場所．"""
    return RESULTS / f"d0009_b3v_q_{rule}"


def derive() -> dict[str, Any]:
    """Q の全問に R3′・V2 を当てはめ，選んだ候補の回答を final_answer にした実行と，その正誤の judgements.jsonl を書く．"""
    _, selector = load_selector(OUT / ANSWER_VERIFIER_SELECTOR)
    items: dict[str, list[dict[str, Any]]] = {r: [] for r in RULES}
    judgements: dict[str, list[dict[str, Any]]] = {r: [] for r in RULES}
    for run, verify, _ in RUNS.values():
        questions = load_questions(run, need_answer_features=False, verify_features=verify)
        outcomes: dict[str, list[QuestionOutcome]] = {
            "R3p": existing_r3p_outcomes(run, questions),
            "V2": outcomes_of(questions, choose_for("V2", questions, selector)),
        }
        rows = {r["qid"]: r for r in read_results(run / "results.jsonl")}
        judged = read_candidate_judgements(run / CANDIDATE_JUDGEMENTS)
        for rule in RULES:
            for o in outcomes[rule]:
                row = rows[o.qid]
                cands = (row.get("owner_answer") or {}).get("candidates") or []
                item: dict[str, Any] = {
                    "qid": o.qid,
                    "bank": row["bank"],
                    "error": row.get("error"),
                    "select_rule": rule,
                    "chosen_index": o.chosen_index,
                    "chosen_source": None,
                    "final_answer": "",
                }
                if o.chosen_index is not None:
                    item["chosen_source"] = str(cands[o.chosen_index]["source"])
                    item["final_answer"] = str(cands[o.chosen_index]["answer"])
                    judgements[rule].append(
                        {"qid": o.qid, "correct": judged.get((o.qid, o.chosen_index))}
                    )
                items[rule].append(item)
    for rule in RULES:
        out = derived(rule)
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.jsonl").write_text(
            "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items[rule]), encoding="utf-8"
        )
        (out / "judgements.jsonl").write_text(
            "".join(json.dumps(j) + "\n" for j in judgements[rule]), encoding="utf-8"
        )
    return {r: len(items[r]) for r in RULES}


def fabrication() -> dict[str, Any]:
    """作り話（誤答）の割合を R3′ と V2 で出す（3 分類．qwen3:8b の棄権の判定．対応ありのブートストラップ区間）．"""
    qids = [r["qid"] for r in read_results(derived("R3p") / "results.jsonl")]
    tables = {r: build_central_table(derived(r), OUT / "labels", qids) for r in RULES}
    col = {name: i for i, name in enumerate(COLUMNS)}
    rates: dict[str, Any] = {}
    for rule, table in tables.items():
        n_judged = float(table.shape[0] - table[:, col["unjudged"]].sum())
        rates[rule] = {
            "n": int(table.shape[0]),
            "n_judged": n_judged,
            **{name: float(table[:, col[name]].sum()) for name in COLUMNS},
            "fabricated_share_of_all": float(table[:, col["fabricated"]].sum() / table.shape[0]),
            "fabricated_share_of_judged": float(table[:, col["fabricated"]].sum() / n_judged),
            "fabricated_upper_incl_unparsed": float(
                (table[:, col["fabricated"]] + table[:, col["unparsed"]]).sum() / table.shape[0]
            ),
        }
    diff = tables["V2"][:, col["fabricated"]] - tables["R3p"][:, col["fabricated"]]
    low, high = paired_bootstrap_ci(diff)
    increase = float(diff.mean())
    return {
        "per_rule": rates,
        "increase_v2_minus_r3p": increase,
        "bootstrap_ci95": [low, high],
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED, "paired": True},
        "limit": FABRICATION_INCREASE_LIMIT,
        "increase_within_limit": increase <= FABRICATION_INCREASE_LIMIT,
    }


def exposure() -> dict[str, Any]:
    """H-B3ve：質問者 1 台（N = 1）の E_any（選択に依らず，質問者へ渡る文書は同じ）と，検証を GPU の PC へ委託したときに追加で渡る文書の数（記述）．"""
    rows_all: list[dict[str, Any]] = []
    out: dict[str, Any] = {"per_run": {}}
    total_docs = 0
    for name, (run, _, meta_run) in RUNS.items():
        rows = read_results(run / "results.jsonl")
        total_docs = mre.total_docs_of(meta_run)
        host_of = {r["qid"]: "requester" for r in rows}
        out["per_run"][name] = mre.exposure(rows, host_of, {}, total_docs)
        rows_all += rows
    host_of_all = {r["qid"]: "requester" for r in rows_all}
    out["Q_E_any_same_for_R3p_and_V2"] = mre.exposure(rows_all, host_of_all, {}, total_docs)
    # 委託で GPU の PC へ渡る文書（全候補の上位 5 通．重複を除いた数と，問あたりの数）
    per_question = [len(mre.candidate_doc_ids(r)) for r in rows_all if r.get("error") is None]
    union = set().union(*(mre.candidate_doc_ids(r) for r in rows_all))
    out["delegated_verification"] = {
        "docs_per_question_median": float(np.median(per_question)),
        "docs_per_question_max": int(max(per_question)),
        "unique_docs_sent": len(union),
        "share_of_all_docs": len(union) / total_docs,
        "note": "検証を質問者の GPU の無い PC から GPU の PC へ委託する配備での追加の露出（E_any と同じ数え方で，全候補の文脈の文書．判定に使わない）",
    }
    return out


def cost() -> dict[str, Any]:
    """H-B3vc：候補 1 件の検証の prompt_eval_count・total_duration_s の中央値と p95（gpu4・RTX 5090）．dev・H・H2 の別と合計．"""
    files = {"dev": DEV_VERIFY, **{name: v for name, (_, v, _) in RUNS.items()}}
    out: dict[str, Any] = {}
    pooled: dict[str, list[float]] = {"prompt_eval_count": [], "total_duration_s": []}
    for name, path in files.items():
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        stats: dict[str, Any] = {"n": len(rows)}
        for key in pooled:
            values = [float(r[key]) for r in rows if r.get(key) is not None]
            pooled[key] += values
            stats[key] = {
                "median": float(np.median(values)),
                "p95": float(np.percentile(values, 95)),
            }
        out[name] = stats
    out["all"] = {
        key: {"median": float(np.median(v)), "p95": float(np.percentile(v, 95)), "n": len(v)}
        for key, v in pooled.items()
    }
    return out


def ceilings_and_split() -> dict[str, Any]:
    """候補のどれかが正解の割合などの天井（H・H2 別．H2 の候補も今回の採点で全件採点済み．V2 − R3′ の H・H2 別の区間は answer_select_q.json）．"""
    from atrium.select_eval import candidate_ceilings

    out: dict[str, Any] = {}
    for name, (run, verify, _) in RUNS.items():
        questions = load_questions(run, need_answer_features=False, verify_features=verify)
        base = existing_r3p_outcomes(run, questions)
        out[name] = candidate_ceilings(
            read_results(run / "results.jsonl"),
            read_candidate_judgements(run / CANDIDATE_JUDGEMENTS),
            base,
        )
    return out


def manual_sample() -> list[dict[str, Any]]:
    """人手での確かめの表：R3′ と V2 が選んだ回答が違う問から MANUAL_N 件（種 MANUAL_SEED）．agent が読んで記録する．"""
    r3 = {r["qid"]: r for r in read_results(derived("R3p") / "results.jsonl")}
    v2 = {r["qid"]: r for r in read_results(derived("V2") / "results.jsonl")}
    j3 = {
        j["qid"]: j["correct"]
        for j in map(json.loads, (derived("R3p") / "judgements.jsonl").read_text().splitlines())
    }
    jv = {
        j["qid"]: j["correct"]
        for j in map(json.loads, (derived("V2") / "judgements.jsonl").read_text().splitlines())
    }
    gold = {}
    for run, _, _ in RUNS.values():
        gold |= {r["qid"]: r.get("gold") for r in read_results(run / "results.jsonl")}
    differ = sorted(
        q
        for q in r3
        if r3[q]["chosen_index"] != v2[q]["chosen_index"]
        and r3[q]["final_answer"] != v2[q]["final_answer"]
    )
    rng = np.random.default_rng(MANUAL_SEED)
    picked = [differ[int(i)] for i in rng.permutation(len(differ))[:MANUAL_N]]
    return [
        {
            "qid": q,
            "gold": gold[q],
            "bank": r3[q]["bank"],
            "r3p_answer": r3[q]["final_answer"],
            "r3p_source": r3[q]["chosen_source"],
            "r3p_judged_correct": j3.get(q),
            "v2_answer": v2[q]["final_answer"],
            "v2_source": v2[q]["chosen_source"],
            "v2_judged_correct": jv.get(q),
        }
        for q in picked
    ]


def main() -> None:
    """段階（derive・report）を実行する．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["derive", "report"])
    args = parser.parse_args()
    if args.stage == "derive":
        print(json.dumps(derive()))
        return
    post = {
        "fabrication": fabrication(),
        "exposure": exposure(),
        "cost": cost(),
        "ceilings": ceilings_and_split(),
    }
    (OUT / "post.json").write_text(json.dumps(post, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "manual_check_sample.json").write_text(
        json.dumps(manual_sample(), indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps({"fabrication": post["fabrication"], "cost_all": post["cost"]["all"]}, indent=1)
    )


if __name__ == "__main__":
    main()
