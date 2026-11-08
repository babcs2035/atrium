"""d0009 B3（Iteration 24）：オフラインのゲート（G-D・G-H）の結果から verdict.json を書く（G-H が不通過なら実機を流さずに棄却として記録する）．

使い方（リポジトリの直下で．`atrium owner-score-eval` が results/d0009_b3_owner/owner_score.json を書いた後．実機・GPU は不要）:
    uv run python scripts/d0009/b3o_verdict.py [--g-t-note "<mise run check の結果>"]
併記として，G-R の Iteration 22 の P1 との照合を同点を許して数え直す（選んだ受信箱のオフラインの得点が，3 番目に高い得点から TIE_ABS_TOL 以内）．
verdict_original は一度書いたら書き換えない（atrium.verdict.write_verdict）．
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from atrium.verdict import write_verdict

OUT = Path("results/d0009_b3_owner")
P1_RUN = Path("results/d0009_a3_p1")
OWNER_M = 3
TIE_ABS_TOL = 1e-4


def tie_tolerant_agreement() -> dict[str, Any]:
    """H の P1 の実機の選択が，基準のオフラインの得点の上位 3 個と，同点を許して一致する問の数（G-R の併記）．"""
    scores = np.load(OUT / "offline_scores_q.npz")
    names = [str(n) for n in scores["names"]]
    column = {n: i for i, n in enumerate(names)}
    row_of = {str(q): i for i, q in enumerate(scores["qids"])}
    live: dict[str, list[str]] = {}
    with (P1_RUN / "results.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("error") is None:
                live[r["qid"]] = list(r["selected_sources"])
    strict = tolerant = n = 0
    mismatches = []
    offline = json.loads((OUT / "offline_selection_q.json").read_text(encoding="utf-8"))["base"]
    for q, chosen in live.items():
        if q not in row_of or q not in offline["holdout"]:
            continue
        n += 1
        s = scores["base"][row_of[q]].astype(np.float64)
        cutoff = np.sort(s)[::-1][OWNER_M - 1]
        same = sorted(chosen) == sorted(offline["holdout"][q])
        ok = same or all(s[column[c]] >= cutoff - TIE_ABS_TOL for c in chosen)
        strict += int(same)
        tolerant += int(ok)
        if not same:
            mismatches.append(
                {"qid": q, "live": chosen, "offline": offline["holdout"][q], "tolerant_match": ok}
            )
    return {
        "n": n,
        "strict": strict,
        "tie_tolerant": tolerant,
        "tie_abs_tol": TIE_ABS_TOL,
        "mismatches": mismatches,
    }


def main() -> None:
    """owner_score.json から verdict.json を書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--g-t-note", default=None, help="G-T（mise run check）の結果の記録")
    args = parser.parse_args()
    result = json.loads((OUT / "owner_score.json").read_text(encoding="utf-8"))
    g_d, g_h = result["G_D"], result["G_H"]
    passed_offline = bool(g_d["passed"] and g_h.get("passed"))
    verdict = "rejected_gate_not_passed" if not passed_offline else "offline_gates_passed"
    original: dict[str, Any] = {
        "hypothesis": "H-B3a（持ち主の特定の得点の替え方は R3′ の正答率を上げる）．実機に進むかはオフラインの G-D・G-H で決める",
        "verdict": verdict,
        "live_run": passed_offline,
        "reason": (
            "G-H 不通過（H の hit@3 の増分が +0.010 に届かず，区間の下端も 0 以下）．実機を流さない．"
            if g_d["passed"] and not g_h.get("passed")
            else "G-D 不通過．実機を流さない．"
            if not g_d["passed"]
            else "G-D・G-H を通過．実機に進む．"
        ),
        "G_D": {
            "chosen": g_d["selection"]["chosen"],
            "dev_hit3_chosen": g_d["dev_hit3_by_candidate"][g_d["selection"]["chosen"]],
            "dev_hit3_baseline": g_d["dev_hit3_baseline_fused_max"],
            "gain": g_d["gain_over_baseline"],
            "min_gain": g_d["min_gain"],
            "passed": g_d["passed"],
            "dev_hit3_by_candidate": g_d["dev_hit3_by_candidate"],
        },
        "G_H": {
            "holdout_hit3": g_h["holdout"]["hit3"],
            "min_gain": g_h["min_gain"],
            "passed": g_h["passed"],
            "holdout2_hit3_descriptive": g_h["holdout2_descriptive"]["hit3"],
        },
        "G_R": {**result["G_R"], "holdout_tie_tolerant": tie_tolerant_agreement()},
        "G_P_offline": result["G_P_offline"],
        "G_T": args.g_t_note,
    }
    print(json.dumps(write_verdict(OUT, original), indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
