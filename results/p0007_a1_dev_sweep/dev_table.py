"""p0007 A1 の dev の表：統合の規則 × m の正答率（分母 300 固定）・UNKNOWN の割合・費用．選択の規則（journal の事前登録）で 1 つ選ぶ．"""
import json
import statistics as st
from pathlib import Path

ROOT = Path("results")
N_DEV = 300
ORDER = ["max_score", "abstain", "agree", "llm_select"]
rows_out = []
for m in (2, 3):
    for a in ORDER:
        run = ROOT / f"p0007_a1_dev_m{m}_{a}"
        res = [json.loads(l) for l in (run / "results.jsonl").read_text().splitlines()]
        judged = {j["qid"]: j for j in map(json.loads, (run / "judgements.jsonl").read_text().splitlines())}
        ok = [r for r in res if r["error"] is None]
        n_correct = sum(1 for r in ok if judged.get(r["qid"], {}).get("correct") is True)
        n_unparsed = sum(1 for r in ok if judged.get(r["qid"], {}).get("correct") is None)
        oa = [r["owner_answer"] for r in ok]
        e2e = sorted(r["timings"]["e2e_s"] for r in ok)
        rows_out.append(
            {
                "run": run.name, "m": m, "aggregate": a, "n": len(res), "failed": len(res) - len(ok),
                "n_correct": n_correct, "accuracy": n_correct / N_DEV, "unparsed": n_unparsed,
                "requests": st.mean(o["n_requests_total"] for o in oa),
                "generations": st.mean(o["n_generations"] for o in oa),
                "cand_unknown_rate": sum(o["n_unknown"] for o in oa) / sum(o["n_candidates"] for o in oa),
                "chosen_unknown_rate": sum(o["chosen_unknown"] for o in oa) / len(oa),
                "fallback_rate": sum(o["fell_back_to_max_score"] for o in oa) / len(oa),
                "select_parse_failed": sum(o["select_parse_failed"] for o in oa),
                "e2e_p50": e2e[len(e2e) // 2], "e2e_p95": e2e[int(len(e2e) * 0.95)],
            }
        )
eligible = [r for r in rows_out if r["failed"] / N_DEV <= 0.10]
best = sorted(
    eligible,
    key=lambda r: (-r["n_correct"], r["requests"], r["generations"], r["m"], ORDER.index(r["aggregate"])),
)[0]
for r in rows_out:
    print(" ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in r.items()))
print("SELECTED", best["run"])
(ROOT / "p0007_a1_dev_sweep" / "dev_table.json").write_text(json.dumps({"rows": rows_out, "selected": best["run"]}, indent=1))
