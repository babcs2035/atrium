"""p0007 A1 の test の補助出力：sim_vs_live.json（§10）と，dev の参照（flood_score・oracle）の表．メール本文は読まない．"""
import json
import statistics as st
from pathlib import Path

R = Path("results")


def load(run: str) -> list[dict]:
    return [json.loads(x) for x in (R / run / "results.jsonl").read_text().splitlines()]


def correct_map(run: str) -> dict[str, bool | None]:
    return {j["qid"]: j["correct"] for j in map(json.loads, (R / run / "judgements.jsonl").read_text().splitlines())}


def wall_s(run: str) -> float:
    m = json.loads((R / run / "run_meta.json").read_text())
    return m["finished_at"] - m["started_at"]


test = load("p0007_a1_test")
offline = json.loads((R / "p0006_p4_build_summary/offline_selection.json").read_text())["hybrid_probe"]
D, M = 16, 3
# (i) 選択：selected_sources は受信箱名の昇順で，得点の順位を保たない．オフラインの選択は上位 2（m = 2）だけなので，
# m = 3 の実機の上位 3 の集合が，オフラインの上位 2 を含む割合で照合する（上位 3 は上位 2 を含むので，一致すれば 1.0）
sel_sub = sum(set(offline[r["qid"]]) <= set(r["selected_sources"]) for r in test)
n = len(test)
req_live = st.mean(r["owner_answer"]["n_requests_total"] for r in test)
gen_live = st.mean(r["owner_answer"]["n_generations"] for r in test)
# (iii) 処理量：P4-1（B1．生成 1 回・要求 18）に対し，要求数の逆数（19/18）または生成回数の逆数（1/3）から予測した比
th_p41 = 2940 / wall_s("p0006_p4_hybrid_probe_b1")
th_live = n / wall_s("p0007_a1_test")
out = {
    "selection_vs_offline": {
        "n": n,
        "offline_top2_within_live_top3": sel_sub / n,
        "note": "selected_sources は受信箱名の昇順で順位を保たない．実機の上位 3（m = 3）がオフラインの上位 2 を全て含む割合で照合した",
        "offline_file": "results/p0006_p4_build_summary/offline_selection.json",
    },
    "requests_per_question": {"predicted": D + M, "live_mean": req_live},
    "generations_per_question": {"predicted": M, "live_mean": gen_live},
    "throughput_qps": {
        "p4_1_live": th_p41,
        "owner_answer_live": th_live,
        "live_ratio_to_p4_1": th_live / th_p41,
        "predicted_ratio_by_request_inverse": (D + 2) / (D + M),
        "predicted_ratio_by_generation_inverse": 1 / gen_live,
        "note": "owner_answer の生成は GPU 10 台（委託）へ分かれ，P4-1 は質問者の GPU 1 枚．要求数の逆数は通信の律速の予測で，生成が律速の本条件には当てはまらない",
    },
}
(R / "p0007_a1_test" / "sim_vs_live.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(json.dumps(out, indent=1, ensure_ascii=False))

# dev の参照：正答率（分母 300）・UNKNOWN の割合
for run in (
    "p0007_a1_dev_m3_abstain",
    "p0007_a1_dev_ref_flood_score_m3_abstain",
    "p0007_a1_dev_ref_oracle_m3_abstain",
):
    rows = load(run)
    c = correct_map(run)
    ok = [r for r in rows if r["error"] is None]
    nc = sum(c.get(r["qid"]) is True for r in ok)
    oa = [r["owner_answer"] for r in ok]
    print(run, "acc", nc / 300, "failed", len(rows) - len(ok), "req", st.mean(o["n_requests_total"] for o in oa),
          "cand_unknown", sum(o["n_unknown"] for o in oa) / sum(o["n_candidates"] for o in oa),
          "chosen_unknown", sum(o["chosen_unknown"] for o in oa) / len(oa),
          "hit_gold_in_candidates", sum(r["bank"] in o["candidate_sources"] for r, o in zip(ok, oa)) / len(ok))
