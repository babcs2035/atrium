"""d0009 B3（Iteration 28）：回答の検証（V1・V2）の dev のゲートと Q の結果から verdict.json を書く．

使い方（リポジトリの直下で．実機・GPU は不要）:
    uv run python scripts/d0009/b3v_verdict.py dev      # verify_dev.json の G-R・G-V・G-D．不通過なら棄却の verdict.json を書く（通過なら何も書かず，終了コード 10）
    uv run python scripts/d0009/b3v_verdict.py final --fabrication-delta <new − R3′ の作り話の割合の差>
        # answer_select_q.json（Q の当てはめ）から H-B3v と取り込みの条件を判定し verdict.json を書く
verdict_original は一度書いたら書き換えない（atrium.verdict.write_verdict）．数値は判定規則（journal の Iteration 28）の閾値に従う．
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from atrium.verdict import write_verdict

OUT = Path("results/d0009_b3_verify")
G_D_PASSED_EXIT = 10
# 主構成への取り込み：作り話の割合の増分（new − R3′）の上限
MAX_FABRICATION_INCREASE = 0.005
HYPOTHESIS = "H-B3v（検証の LLM の得点で選ぶと R3′ より正答率が上がる）"


def dev_verdict() -> int:
    """dev の G-R・G-V・G-D を見て，不通過なら棄却の verdict_original を書く（通過なら 10 を返す）．"""
    dev = json.loads((OUT / "verify_dev.json").read_text(encoding="utf-8"))
    g_d = dev["G_D"]
    summary: dict[str, Any] = {
        "G_R": dev["G_R"],
        "G_V": dev["G_V"],
        "G_D": {
            "chosen": g_d["selection"]["chosen"],
            "gain": g_d["gain_over_r3p"],
            "min_gain": g_d["min_gain"],
            "dev_accuracy": {r: v["accuracy"] for r, v in g_d["dev_accuracy"].items()},
            "paired_vs_r3p": g_d["paired_vs_r3p"],
            "passed": g_d["passed"],
        },
    }
    for gate, verdict, reason in (
        (
            "G_R",
            "rejected_gate_GR_failed",
            "G-R 不通過（R3′・S3 の dev の再現，または H の選択の一致）．",
        ),
        (
            "G_V",
            "rejected_gate_GV_failed",
            "G-V 不通過（検証の LLM が Yes / No で答えない候補が 1% を超えた）．プロンプトを変えて流し直さない．",
        ),
        (
            "G_D",
            "rejected_gate_GD_failed",
            "G-D 不通過（dev で選んだ規則の増分が +0.006 に届かない）．H・H2 の検証の得点も当てはめも行わない．",
        ),
    ):
        if not summary[gate]["passed"]:
            write_verdict(
                OUT,
                {
                    "hypothesis": HYPOTHESIS,
                    "verdict": verdict,
                    "live_run": False,
                    "reason": reason,
                    **summary,
                },
            )
            print(json.dumps({"verdict": verdict}))
            return 0
    print(
        json.dumps(
            {"G_D": "passed", "chosen": g_d["selection"]["chosen"], "gain": g_d["gain_over_r3p"]}
        )
    )
    return G_D_PASSED_EXIT


def final_verdict(fabrication_delta: float) -> int:
    """Q の当てはめの結果から H-B3v（改善・悪化・決まらない）と主構成への取り込みの可否を判定して書く．"""
    q = json.loads((OUT / "answer_select_q.json").read_text(encoding="utf-8"))
    dev = json.loads((OUT / "verify_dev.json").read_text(encoding="utf-8"))
    adopted = bool(
        q["Q"]["verdict"] == "improved" and fabrication_delta <= MAX_FABRICATION_INCREASE
    )
    write_verdict(
        OUT,
        {
            "hypothesis": HYPOTHESIS,
            "verdict": q["Q"]["verdict"],
            "rule": q["rule"],
            "adopted_into_main_configuration": adopted,
            "fabrication_delta": fabrication_delta,
            "max_fabrication_increase": MAX_FABRICATION_INCREASE,
            "Q": {
                k: q["Q"][k]
                for k in (
                    "n",
                    "acc_new",
                    "acc_base",
                    "delta",
                    "bootstrap_ci95",
                    "analytic_ci95",
                    "b_only_new",
                    "c_only_base",
                    "mcnemar_p",
                )
            },
            "dev": {
                "G_R": dev["G_R"],
                "G_V": dev["G_V"],
                "G_D": dev["G_D"]["selection"]
                | {"gain": dev["G_D"]["gain_over_r3p"], "passed": dev["G_D"]["passed"]},
            },
        },
    )
    print(json.dumps({"verdict": q["Q"]["verdict"], "adopted": adopted}))
    return 0


def main() -> int:
    """引数の段階を実行する．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["dev", "final"])
    parser.add_argument("--fabrication-delta", type=float, default=None)
    args = parser.parse_args()
    if args.stage == "dev":
        return dev_verdict()
    if args.fabrication_delta is None:
        parser.error("final requires --fabrication-delta")
    return final_verdict(args.fabrication_delta)


if __name__ == "__main__":
    sys.exit(main())
