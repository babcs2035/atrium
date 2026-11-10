"""d0009 B3（Iteration 41）：受信箱の中の文脈 5 通を dev で選んだ規則 X に替えたときの正答率（H-IC）と作り話の増分（守り）の集計と判定．

使い方（リポジトリの直下．`atrium inbox-context-eval --stage q-build` と，両腕の `atrium judge --with-candidates`・
`atrium verify-features --resume` の後．GPU は使わない）:
    uv run python scripts/d0009/b3ic_verdict.py derive   # 両腕の V2・R3′ が選んだ最終回答の派生の実行（results/d0009_b3ic_q_{base,new}_{V2,R3p}）
    # （wafl502〜508 の判定モデルで）uv run atrium abstain-judge --run-dir <派生の実行> ... --out-dir results/d0009_b3_inbox_context/labels --resume
    uv run python scripts/d0009/b3ic_verdict.py report   # results/d0009_b3_inbox_context/{inbox_context.json,verdict.json,analysis_report.md}
- 主（H-IC）：Q = H ∪ H2 の V2 の最終回答の正答率の差 Δ = acc(new) − acc(base)．95% 区間は問を単位にした対応ありの bootstrap
  （10,000 回・種 41．`atrium.verdict.paired_bootstrap_ci`）．McNemar の b : c と正確検定の p（`atrium.analysis.mcnemar_exact`）を併記する．
  下端 > 0 なら「改善」，上端 < 0 なら「悪化」，それ以外は「決まらない」．
- 守り：作り話（誤答）の割合の増分（new − base）≤ +0.005（Iteration 28 と同じ定義．`atrium.fabrication_eval.build_central_table`）．
- 採否：「改善」かつ守りを満たす → 採用．「悪化」→ 棄却．それ以外 → 決まらない（記述）．ゲート（G-RRF・G-CE・G-D・G-GEN）が 1 つでも不通過なら判定不能．
- 両腕とも，作り直した候補の回答・採点・特徴量・検証の得点で V2（`results/d0009_b3_verify/answer_verifier_selector.json`．学習し直さない）を当てはめる．
  判定に使う正誤は，保存した判定（`atrium judge --with-candidates`．同じ (質問，回答) は温度 0 の同じ判定）．
統計は既存の実装を使う：`atrium.answer_select_eval`（V2・R3′・区分）・`atrium.select_eval`（天井）・`atrium.fabrication_eval`（3 分類）．
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium import answer_select_eval as ase
from atrium import inbox_context_eval as ice
from atrium import multi_requester_eval as mre
from atrium import prompts
from atrium.analysis import mcnemar_exact, read_results
from atrium.fabrication_eval import COLUMNS, build_central_table
from atrium.select_eval import (
    CANDIDATE_JUDGEMENTS,
    QuestionOutcome,
    candidate_ceilings,
    read_candidate_judgements,
    segment_table,
)
from atrium.verdict import BOOTSTRAP_SAMPLES, make_verdict, paired_bootstrap_ci, write_verdict

RESULTS = Path("results")
ARM_PREFIX = RESULTS / "d0009_b3ic_q_"
ARMS = ("base", "new")
OUT = RESULTS / "d0009_b3_inbox_context"
DEV_DIR = RESULTS / "d0009_b3ic_dev_gate"
PLAN_DIR = RESULTS / "d0009_b3ic_plan"
V2_SELECTOR = RESULTS / "d0009_b3_verify" / "answer_verifier_selector.json"
RULES = ("R3p", "V2")
BOOTSTRAP_SEED = 41
FABRICATION_INCREASE_LIMIT = 0.005
# 露出の割合の分母を読む実行（run_meta.json の sources の文書数の合計）
TOTAL_DOCS_RUN = RESULTS / "d0009_a3_p1"


def arm_dir(arm: str) -> Path:
    """腕の実行のディレクトリ（`inbox-context-eval --stage q-build` が書く）．"""
    return Path(f"{ARM_PREFIX}{arm}")


def derived_dir(arm: str, rule: str) -> Path:
    """腕の，規則 rule が選んだ最終回答の派生の実行の置き場所．"""
    return Path(f"{ARM_PREFIX}{arm}_{rule}")


# ── 判定の規則（単体の試験の対象） ───────────────────────────────────────────


def decide_h_ic(ci95: Sequence[float]) -> str:
    """H-IC：区間の下端 > 0 なら improved，上端 < 0 なら worse，それ以外は undecided．"""
    low, high = ci95
    if low > 0:
        return "improved"
    if high < 0:
        return "worse"
    return "undecided"


def decide_guard(increase: float) -> bool:
    """守り：作り話の割合の増分が +0.005 以下．"""
    return increase <= FABRICATION_INCREASE_LIMIT


def decide_overall(h_ic: str, guard_ok: bool, gates_passed: bool) -> str:
    """採否：ゲートが不通過なら indeterminate_gate，improved かつ守りを満たせば adopted，worse なら rejected，それ以外は undecided．"""
    if not gates_passed:
        return "indeterminate_gate"
    if h_ic == "improved" and guard_ok:
        return "adopted"
    if h_ic == "worse":
        return "rejected"
    return "undecided"


def paired_accuracy(new: Sequence[bool], base: Sequence[bool]) -> dict[str, Any]:
    """new − base の正答率の差・対応ありの bootstrap の 95% 区間（種 41）・McNemar（b = new だけ正解，c = base だけ正解）．"""
    y_new, y_base = np.asarray(new, dtype=np.float64), np.asarray(base, dtype=np.float64)
    diff = y_new - y_base
    low, high = paired_bootstrap_ci(diff, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED)
    b = int(((y_new == 1) & (y_base == 0)).sum())
    c = int(((y_new == 0) & (y_base == 1)).sum())
    return {
        "n": len(diff),
        "acc_new": float(y_new.mean()),
        "acc_base": float(y_base.mean()),
        "delta": float(diff.mean()),
        "bootstrap_ci95": [low, high],
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED, "paired": True},
        "b_only_new": b,
        "c_only_base": c,
        "mcnemar_p": mcnemar_exact(b, c),
    }


# ── 両腕の選択 ────────────────────────────────────────────────────────────────


def arm_questions(arm: str) -> list[ase.QuestionCandidates]:
    """腕の実行の全問の候補の特徴量（検証の得点 v を含む）．"""
    return ase.load_questions(
        arm_dir(arm),
        need_answer_features=False,
        verify_features=arm_dir(arm) / ice.VERIFY_FEATURES,
    )


def arm_outcomes(arm: str) -> dict[str, list[QuestionOutcome]]:
    """腕の R3′・V2 の選んだ結果（全問．問の順は results.jsonl）．"""
    _, selector = ase.load_selector(V2_SELECTOR)
    questions = arm_questions(arm)
    return {
        "R3p": ase.existing_r3p_outcomes(arm_dir(arm), questions),
        "V2": ase.outcomes_of(questions, ase.choose_for("V2", questions, selector)),
    }


def derive() -> dict[str, int]:
    """両腕の規則ごとに，選んだ候補の回答を final_answer にした実行と，その正誤の judgements.jsonl を書く（abstain-judge の入力）．"""
    counts: dict[str, int] = {}
    for arm in ARMS:
        outcomes = arm_outcomes(arm)
        rows = {r["qid"]: r for r in read_results(arm_dir(arm) / "results.jsonl")}
        judged = read_candidate_judgements(arm_dir(arm) / CANDIDATE_JUDGEMENTS)
        for rule in RULES:
            items: list[dict[str, Any]] = []
            judgements: list[dict[str, Any]] = []
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
                    judgements.append(
                        {"qid": o.qid, "correct": judged.get((o.qid, o.chosen_index))}
                    )
                items.append(item)
            out = derived_dir(arm, rule)
            out.mkdir(parents=True, exist_ok=True)
            (out / "results.jsonl").write_text(
                "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8"
            )
            (out / "judgements.jsonl").write_text(
                "".join(json.dumps(j) + "\n" for j in judgements), encoding="utf-8"
            )
            counts[f"{arm}_{rule}"] = len(items)
    return counts


# ── 集計 ──────────────────────────────────────────────────────────────────────


def split_of(qid: str) -> str:
    """質問 ID の分割の名前（enronqa/holdout/... → holdout．H・H2 の別）．"""
    return qid.split("/")[1]


def correct_of(outcomes: Sequence[QuestionOutcome]) -> list[bool]:
    """結果の正誤（問の順）．"""
    return [o.correct for o in outcomes]


def accuracy_blocks(
    outcomes: Mapping[str, Mapping[str, Sequence[QuestionOutcome]]],
) -> dict[str, Any]:
    """Q・H・H2 の，V2 と R3′ の new − base の差（主は Q の V2）．"""
    qids = [o.qid for o in outcomes["base"]["V2"]]
    out: dict[str, Any] = {}
    for rule in RULES:
        new, base = correct_of(outcomes["new"][rule]), correct_of(outcomes["base"][rule])
        block: dict[str, Any] = {"Q": paired_accuracy(new, base)}
        for split in sorted({split_of(q) for q in qids}):
            keep = [i for i, q in enumerate(qids) if split_of(q) == split]
            block[split] = paired_accuracy([new[i] for i in keep], [base[i] for i in keep])
        out[rule] = block
    return out


def fabrication_report() -> dict[str, Any]:
    """V2 が選んだ最終回答の 3 分類（qwen3:8b の棄権の判定）と，作り話の割合の増分（new − base．対応ありの bootstrap）．"""
    labels = OUT / "labels"
    qids = [r["qid"] for r in read_results(derived_dir("base", "V2") / "results.jsonl")]
    tables = {arm: build_central_table(derived_dir(arm, "V2"), labels, qids) for arm in ARMS}
    col = {name: i for i, name in enumerate(COLUMNS)}
    per_arm: dict[str, Any] = {}
    for arm, table in tables.items():
        n_judged = float(table.shape[0] - table[:, col["unjudged"]].sum())
        per_arm[arm] = {
            "n": int(table.shape[0]),
            "n_judged": n_judged,
            **{name: float(table[:, col[name]].sum()) for name in COLUMNS},
            "fabricated_share_of_all": float(table[:, col["fabricated"]].sum() / table.shape[0]),
            "fabricated_share_of_judged": float(table[:, col["fabricated"]].sum() / n_judged),
        }
    diff = tables["new"][:, col["fabricated"]] - tables["base"][:, col["fabricated"]]
    low, high = paired_bootstrap_ci(diff, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED)
    increase = float(diff.mean())
    return {
        "per_arm": per_arm,
        "increase_new_minus_base": increase,
        "bootstrap_ci95": [low, high],
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED, "paired": True},
        "limit": FABRICATION_INCREASE_LIMIT,
        "increase_within_limit": decide_guard(increase),
    }


def segments_and_ceilings(
    outcomes: Mapping[str, Mapping[str, Sequence[QuestionOutcome]]],
) -> dict[str, Any]:
    """区分 (a)(b)(c) の割合と正答率（V2・R3′），候補のどれかが正解の割合と持ち主の候補の正答率（腕ごと）．"""
    out: dict[str, Any] = {"segments": {}, "ceilings": {}}
    for arm in ARMS:
        out["segments"][arm] = {rule: segment_table(outcomes[arm][rule]) for rule in RULES}
        rows = read_results(arm_dir(arm) / "results.jsonl")
        judged = read_candidate_judgements(arm_dir(arm) / CANDIDATE_JUDGEMENTS)
        out["ceilings"][arm] = candidate_ceilings(rows, judged, outcomes[arm]["V2"])
    return out


def completeness() -> dict[str, Any]:
    """成功条件 (2)：両腕で V2 の選択と採点が揃った問の数．判定できなかった候補と検証の得点の無い候補の数（残る失敗は不正解として数える）．"""
    out: dict[str, Any] = {}
    for arm in ARMS:
        questions = arm_questions(arm)
        v_col = ase.FEATURE_NAMES.index("v")
        out[arm] = {
            "n_questions": len(questions),
            "n_with_candidates": sum(bool(q.sources) for q in questions),
            "n_unparsed_candidates": sum(q.unparsed for q in questions),
            "n_candidates_without_v": int(sum(np.isnan(q.raw[:, v_col]).sum() for q in questions)),
        }
    build = read_json(PLAN_DIR / "build.json")
    out["n_failed_generation_candidates"] = (
        None if build is None else build["base"]["n_failed_generation_candidates"]
    )
    return out


def recall_in_q(
    plan: Sequence[Mapping[str, Any]], q_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Q の持ち主の受信箱の中の Recall@5（持ち主が候補に入った問だけ．new と RRF〔保存した文脈〕）と，計画の割合．"""
    from atrium.data_enronqa import read_gold
    from atrium.paths import dataset_paths

    gold = read_gold(dataset_paths(Path("artifacts"), "enronqa"))
    bank = {r["qid"]: r["bank"] for r in q_rows}
    hits = {"base": 0, "new": 0}
    n = 0
    for p in plan:
        if p["source"] != bank[p["qid"]]:
            continue
        n += 1
        hits["base"] += gold[p["qid"]]["path"] in p["base_ids"]
        hits["new"] += gold[p["qid"]]["path"] in p["new_ids"]
    return {
        "n_owner_candidates": n,
        "recall_base_rrf": hits["base"] / n,
        "recall_new": hits["new"] / n,
        "delta": (hits["new"] - hits["base"]) / n,
    }


def regeneration_agreement(
    records: Mapping[str, Mapping[str, Any]], q_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """作り直した base の回答が保存値と一致する割合（生成の揺れの目安．文脈は同じ）と，prompt_tokens が node_answers と一致する割合．"""
    row_of = {r["qid"]: r for r in q_rows}
    n = same_answer = same_tokens = 0
    for rec in records.values():
        if rec["arm"] != "base" or rec["error"] is not None:
            continue
        row = row_of[rec["qid"]]
        saved = row["owner_answer"]["candidates"][rec["index"]]
        source = saved["source"]
        tokens = next(na["prompt_tokens"] for na in row["node_answers"] if na["source"] == source)
        n += 1
        same_answer += prompts.extract_free_answer(rec["answer"]) == saved["answer"]
        same_tokens += rec["prompt_tokens"] == tokens
    return {
        "n_base_regenerated": n,
        "answer_identical_share": same_answer / n if n else None,
        "prompt_tokens_identical_share": same_tokens / n if n else None,
    }


def exposure_report(rule: str) -> dict[str, Any]:
    """質問者へ渡る文書 E_any（腕ごと．N = 1）と，並べ替えを委託先に置いたときに委託先へ渡る文書 N 通の見積もり（記述）．"""
    total_docs = mre.total_docs_of(TOTAL_DOCS_RUN)
    out: dict[str, Any] = {"E_any": {}}
    for arm in ARMS:
        rows = read_results(arm_dir(arm) / "results.jsonl")
        host_of = {r["qid"]: "requester" for r in rows}
        out["E_any"][arm] = mre.exposure(rows, host_of, {}, total_docs)
    cache = ice.read_ce_cache(PLAN_DIR / ice.CE_CACHE)
    if rule != "bm25" and cache:
        per_question: dict[str, set[str]] = {}
        for (qid, _), (doc_ids, _) in cache.items():
            per_question.setdefault(qid, set()).update(doc_ids)
        union = set().union(*per_question.values())
        sizes = [len(v) for v in per_question.values()]
        out["delegated_rerank_depth"] = {
            "rule": rule,
            "docs_per_question_median": float(np.median(sizes)),
            "docs_per_question_max": int(max(sizes)),
            "unique_docs_sent": len(union),
            "share_of_all_docs": len(union) / total_docs,
            "note": "並べ替えを委託先の GPU の PC に置いた場合の追加の露出の見積もり（判定に使わない）",
        }
    return out


def read_json(path: Path) -> dict[str, Any] | None:
    """JSON を読む（無ければ None）．"""
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def gate_summary() -> dict[str, Any]:
    """G-RRF・G-CE・G-D・G-GEN の通否（ファイルが無ければ passed = None）．"""
    dev_gate = read_json(DEV_DIR / "dev_gate.json")
    files = {
        "G-RRF": read_json(PLAN_DIR / "g_rrf.json"),
        "G-CE": read_json(DEV_DIR / "g_ce.json"),
        "G-GEN": read_json(PLAN_DIR / "g_gen.json"),
    }
    gates: dict[str, Any] = {k: None if v is None else bool(v["passed"]) for k, v in files.items()}
    gates["G-D"] = None if dev_gate is None else bool(dev_gate["G_D"]["passed"])
    return {"passed_by_gate": gates, "all_passed": all(v is True for v in gates.values())}


def build_report() -> dict[str, Any]:
    """集計の全体（inbox_context.json）と，判定（verdict_original）を作る．"""
    outcomes = {arm: arm_outcomes(arm) for arm in ARMS}
    accuracy = accuracy_blocks(outcomes)
    fabrication = fabrication_report()
    gates = gate_summary()
    plan = ice.read_jsonl(PLAN_DIR / ice.PLAN)
    q_rows = ice.read_q_rows()
    dev_gate = json.loads((DEV_DIR / "dev_gate.json").read_text(encoding="utf-8"))
    rule = dev_gate["selection"]["chosen"]
    h_ic = decide_h_ic(accuracy["V2"]["Q"]["bootstrap_ci95"])
    guard_ok = fabrication["increase_within_limit"]
    overall = decide_overall(h_ic, guard_ok, gates["all_passed"])
    report: dict[str, Any] = {
        "rule": rule,
        "gates": gates,
        "dev_gate": {k: dev_gate[k] for k in ("n", "recall", "selection", "G_D")},
        "accuracy": accuracy,
        "fabrication": fabrication,
        **segments_and_ceilings(outcomes),
        "completeness": completeness(),
        "plan": read_json(PLAN_DIR / "plan.json"),
        "recall_in_q": recall_in_q(plan, q_rows),
        "regeneration": regeneration_agreement(
            ice.read_gen_records(PLAN_DIR / ice.GEN_RECORDS), q_rows
        ),
        "exposure": exposure_report(rule),
        "runs": {
            "dev_ce": read_json(DEV_DIR / "dev_ce_run.json"),
            "q_ce": read_json(PLAN_DIR / "q_ce_run.json"),
        },
        "verdict_original": {
            "hypothesis": "H-IC（候補の受信箱の中の文脈 5 通を dev で選んだ規則 X に替えると，Q の V2 の最終回答の正答率が上がる）",
            "h_ic": h_ic,
            "delta_v2_Q": accuracy["V2"]["Q"]["delta"],
            "ci95": accuracy["V2"]["Q"]["bootstrap_ci95"],
            "guard_fabrication_increase": fabrication["increase_new_minus_base"],
            "guard_within_limit": guard_ok,
            "gates_all_passed": gates["all_passed"],
            "verdict": overall,
            "rule": rule,
        },
    }
    return report


def render_report(report: Mapping[str, Any]) -> str:
    """analysis_report.md の本文（集計値だけ）．"""
    q = report["accuracy"]["V2"]["Q"]
    r3 = report["accuracy"]["R3p"]["Q"]
    fab = report["fabrication"]
    v = report["verdict_original"]
    lines = [
        "# d0009 B3 Iteration 41：受信箱の中の文脈 5 通の選び方（RRF → X）",
        "",
        f"選んだ X：`{report['rule']}`．判定：**{v['verdict']}**（H-IC：{v['h_ic']}，守り：{'満たす' if v['guard_within_limit'] else '満たさない'}，"
        f"ゲート：{'全て通過' if v['gates_all_passed'] else '不通過あり'}）．",
        "",
        "## ゲート",
        "",
        *(
            f"- {k}：{ {True: '通過', False: '不通過', None: '未実行'}[p] }"
            for k, p in report["gates"]["passed_by_gate"].items()
        ),
        "",
        "## 主（H-IC）：V2 の最終回答の正答率",
        "",
        "| 集合 | n | acc(base) | acc(new) | Δ | 95% 区間（bootstrap 10,000・種 41） | b : c | McNemar p |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rule_name, block in report["accuracy"].items():
        for split, r in block.items():
            lo, hi = r["bootstrap_ci95"]
            lines.append(
                f"| {rule_name} {split} | {r['n']} | {r['acc_base']:.4f} | {r['acc_new']:.4f} | {r['delta']:+.4f} | "
                f"[{lo:+.4f}, {hi:+.4f}] | {r['b_only_new']} : {r['c_only_base']} | {r['mcnemar_p']:.4g} |"
            )
    lo, hi = fab["bootstrap_ci95"]
    lines += [
        "",
        f"（主は V2 の Q．R3′ の Q の Δ = {r3['delta']:+.4f}．V2 の Q の Δ = {q['delta']:+.4f}）",
        "",
        "## 守り：作り話の割合",
        "",
        f"new − base の増分 {fab['increase_new_minus_base']:+.4f}（95% 区間 [{lo:+.4f}, {hi:+.4f}]．上限 +{fab['limit']}）→ "
        f"{'満たす' if fab['increase_within_limit'] else '満たさない'}",
        "",
        "| 腕 | 作り話（全問に対する割合） | 作り話（判定できた問に対する割合） | 判定不能 |",
        "|---|---|---|---|",
        *(
            f"| {arm} | {a['fabricated_share_of_all']:.4f} | {a['fabricated_share_of_judged']:.4f} | {a['unjudged']:.0f} |"
            for arm, a in fab["per_arm"].items()
        ),
        "",
        "## 記述",
        "",
        f"- 作り直す候補：{report['plan']['n_changed']} / {report['plan']['n_candidates']}（{report['plan']['share_changed']:.3f}）．"
        f"持ち主の受信箱の中の Recall@5（Q．持ち主が候補に入った問）：RRF {report['recall_in_q']['recall_base_rrf']:.4f} → "
        f"new {report['recall_in_q']['recall_new']:.4f}",
        f"- 作り直した base の回答が保存値と一致する割合：{report['regeneration']['answer_identical_share']:.4f}"
        f"（prompt_tokens の一致 {report['regeneration']['prompt_tokens_identical_share']:.4f}）",
        f"- dev の Recall@5：{json.dumps(report['dev_gate']['recall'])}",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    """段階（derive・report）を実行する．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["derive", "report"])
    args = parser.parse_args()
    if args.stage == "derive":
        print(json.dumps(derive()))
        return
    report = build_report()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "inbox_context.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (OUT / "analysis_report.md").write_text(render_report(report), encoding="utf-8")
    write_verdict(OUT, report["verdict_original"])
    print(json.dumps(make_verdict(report["verdict_original"]), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
