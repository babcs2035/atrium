"""d0009 A6 障害（Iteration 30）の事後の記述：止めた間に聞いた問を「再開の前に答えた」と「再開の後に答えた」に分ける（判定には使わない）．

使い方（リポジトリの直下．実機は使わない）:
    uv run python scripts/d0009/a6f_posthoc_completion.py
- 入力：`results/d0009_a6_failure_r{1,2,3}/failure.json`（`p2p[].questions` の `t_s`・`e2e_s`・`hit`，`start.acked_at_s`，`rates`）．
- 出力：`results/d0009_a6_failure/posthoc_completion.json`．
- 事前登録の「止めた間の割合」（`fault_hit_rate`）は聞いた時刻で問を数えるので，止めたノードの再開まで待って締め切り内に答えた問も
  「止めた間に答えられた」に入る．ここでは終わった時刻（`t_s + e2e_s`）が再開の確認（`start.acked_at_s`）より前かで分ける．
- 併せて，P2P の積分のうち止める時間の後（[down_s, W]）だけの値を出す．これが中央の積分 c × 30 を超えるなら，
  H-A6F1 の「P2P が上」は止めた間の割合に依らずに決まる（45 秒の仮定の構造）．
`verdict.json` の `verdict_original` は変えない（このファイルは別の記述）．
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from atrium.dynamics import fill_forward, integrate_rates

OUT_PATH = Path("results/d0009_a6_failure/posthoc_completion.json")
VERDICT_PATH = Path("results/d0009_a6_failure/verdict.json")
ROUNDS = (1, 2, 3)


def split_condition(cond: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """1 条件の止めた間の問を，再開の確認より前に終わったか後に終わったかで分けて数える．"""
    down_s = float(params["down_s"])
    restart_ack_s = float(cond["start"]["acked_at_s"])
    rows = cond["questions"]
    fault = [q for q in rows if 0.0 <= q["t_s"] < down_s]
    hit_before = [q for q in fault if q["hit"] and q["t_s"] + q["e2e_s"] < restart_ack_s]
    hit_after = [q for q in fault if q["hit"] and q["t_s"] + q["e2e_s"] >= restart_ack_s]
    baseline_e2e = [q["e2e_s"] for q in rows if q["t_s"] < 0 and q["e2e_s"] is not None]
    filled = fill_forward(cond["rates"], float(cond["baseline_hit_rate"]))
    post_stop_integral = integrate_rates(
        filled, float(cond["bin_start_s"]), float(cond["bin_s"]), down_s, float(cond["window_s"])
    )
    n = len(fault)
    return {
        "mode": cond["mode"],
        "k": cond["k"],
        "restart_acked_at_s": restart_ack_s,
        "fault_n": n,
        "fault_hit_rate_registered": cond["fault_hit_rate"],
        "hit_finished_before_restart": len(hit_before),
        "hit_finished_after_restart": len(hit_after),
        "rate_finished_before_restart": len(hit_before) / n if n else None,
        "e2e_p50_hits_after_restart": statistics.median(q["e2e_s"] for q in hit_after)
        if hit_after
        else None,
        "e2e_p50_baseline": statistics.median(baseline_e2e) if baseline_e2e else None,
        "integral_post_stop_only": post_stop_integral,
    }


def main() -> None:
    """3 ラウンドの failure.json から事後の記述を作って書き出す．"""
    central_integral = float(
        json.loads(VERDICT_PATH.read_text("utf-8"))["central_integral_assumed_45s"]
    )
    rounds: dict[str, list[dict[str, Any]]] = {}
    for r in ROUNDS:
        run = f"d0009_a6_failure_r{r}"
        data = json.loads(Path(f"results/{run}/failure.json").read_text("utf-8"))
        rounds[run] = [split_condition(c, data["params"]) for c in data["p2p"]]
    post_only = [c["integral_post_stop_only"] for conds in rounds.values() for c in conds]
    out = {
        "iteration": 30,
        "note": "事後の記述（判定に使わない）．止めた間に聞いた問を，終わった時刻が再開の確認より前か後かで分けた．",
        "central_integral_assumed_45s": central_integral,
        "min_integral_post_stop_only": min(post_only),
        "post_stop_only_exceeds_central_in_all": min(post_only) > central_integral,
        "rounds": rounds,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
