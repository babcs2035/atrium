"""d0009 A6 参加（Iteration 31）：主構成 P の参加の測定 3 ラウンドの集計・ゲート・判定（`results/d0009_a6_join/verdict.json`・`join_summary.json`）．

使い方（リポジトリの直下．実機は使わない．実行の後，commit の前に results の質問文を含むログを確かめる）:
    uv run python scripts/d0009/a6j_verdict.py
- 入力：`results/d0009_a6_join_r{1,2,3}/`（join.json・sim_vs_live.json・g_r_not_serving_after.txt）・`results/d0009_a6_join/table/table_check_{owner,central}.json`（G-TB）・
  `results/d0009_a6_load/table/cost.json`（cross-encoder の計算の費用の見積もり）・`results/p0007_e2_join/{join,verdict}.json`（並べるだけ．比べない）．
- 判定は p0007 E2 と同じ `atrium.dynamics.judge_join`（P2P の受信箱ごとの答えられるまでの時間の中央値が中央より短い，または中央の 1.25 倍以内で
  P2P の転送量が小さい）を各ラウンドで呼ぶ．有効なラウンドの全てで成り立てば「E2 成立」，全てで成り立たなければ「E2 不成立」，それ以外は「決まらない」．
- 有効なラウンド：G-A・G-J・G-R を満たし，表に無い組による失敗が P2P・中央とも 0 で，join.json があるもの（journal の Iteration 31）．
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from atrium.dynamics import durations_or_censored, judge_join, summarize_times

OUT_DIR = Path("results/d0009_a6_join")
ROUNDS = (1, 2, 3)
MIN_VALID_ROUNDS = 2
MIN_CANDIDATE_AGREEMENT = 0.90
E2_HOLDS = "E2 成立"
E2_FAILS = "E2 不成立"
UNDECIDED = "決まらない"
COST_FILE = Path("results/d0009_a6_load/table/cost.json")
P2P_E2 = Path("results/p0007_e2_join")


def three_way(holds: Sequence[bool]) -> str:
    """有効なラウンドの全てで成り立てば E2_HOLDS，全てで成り立たなければ E2_FAILS，それ以外は UNDECIDED．"""
    if not holds:
        return UNDECIDED
    if all(holds):
        return E2_HOLDS
    if not any(holds):
        return E2_FAILS
    return UNDECIDED


def try_rows(side: Mapping[str, Any]) -> list[dict[str, Any]]:
    """側（p2p・central）の全試行の記録．"""
    return [r for d in side["devices"] for a in d["answers"] for r in a["rows"]]


def answers_of(side: Mapping[str, Any]) -> list[dict[str, Any]]:
    """側の受信箱ごとの答え（host を足す）．"""
    return [{**a, "host": d["host"]} for d in side["devices"] for a in d["answers"]]


def tries_summary(side: Mapping[str, Any]) -> dict[str, Any]:
    """試行の数の分布・1 問目で答えられた受信箱の割合・打ち切りの数・失敗の形の内訳（質問者の記録のとおり）．"""
    answers = answers_of(side)
    tries = [a["tries"] for a in answers]
    kinds: dict[str, int] = {}
    for row in try_rows(side):
        kinds[str(row["failure_kind"])] = kinds.get(str(row["failure_kind"]), 0) + 1
    return {
        "tries_distribution": {str(t): tries.count(t) for t in sorted(set(tries))},
        "first_try_answered_rate": sum(
            a["tries"] == 1 and a["answer_s"] is not None for a in answers
        )
        / len(answers),
        "n_censored": sum(a["answer_s"] is None for a in answers),
        "failure_kinds": kinds,
        "n_tries_total": len(try_rows(side)),
    }


def by_host(side: Mapping[str, Any], per_try_cost_s: float) -> dict[str, dict[str, Any]]:
    """ホストごとの答えられるまでの時間の中央値（記録のとおり）と，cross-encoder の計算の費用（1 試行 per_try_cost_s 秒）を足した見積もり．"""
    out: dict[str, dict[str, Any]] = {}
    for host in sorted({a["host"] for a in answers_of(side)}):
        mine = [a for a in answers_of(side) if a["host"] == host]
        times = durations_or_censored([a["answer_s"] for a in mine])
        estimated = durations_or_censored(
            [
                None if a["answer_s"] is None else a["answer_s"] + per_try_cost_s * a["tries"]
                for a in mine
            ]
        )
        out[host] = {
            "n_inboxes": len(mine),
            "answer_s": summarize_times(times),
            "answer_s_with_ce_cost_estimate": summarize_times(estimated),
        }
    return out


def round_summary(n: int, cost: Mapping[str, Any]) -> dict[str, Any]:
    """1 ラウンドの有効性・判定・時間の比・内訳を作る．"""
    run = Path(f"results/d0009_a6_join_r{n}")
    join = json.loads((run / "join.json").read_text("utf-8"))
    sim = json.loads((run / "sim_vs_live.json").read_text("utf-8"))
    not_serving = (run / "g_r_not_serving_after.txt").read_text("utf-8").strip()
    e2 = json.loads((P2P_E2 / "join.json").read_text("utf-8"))
    p2p, central = join["p2p"], join["central"]
    stats = sim.get("bm25_stats_vs_offline") or {}
    agreement = sim.get("candidate_agreement_with_offline_summary")
    table_misses = sum(
        r["failure_kind"] == "table_miss" for side in (p2p, central) for r in try_rows(side)
    )
    gates = {
        "G_A_stats_match": bool(stats.get("g_a_stats_match")),
        "G_A_candidate_agreement": agreement,
        "G_A_agreement_ok": agreement is not None and agreement >= MIN_CANDIDATE_AGREEMENT,
        "G_J_same_plan": (
            join["plan"]["joiner_hosts"] == e2["plan"]["joiner_hosts"]
            and join["plan"]["joiner_inboxes"] == e2["plan"]["joiner_inboxes"]
        ),
        "G_R_not_serving_after": not_serving,
        "G_R_ok": not_serving == "none",
        "table_miss": table_misses,
    }
    valid = (
        gates["G_A_stats_match"]
        and gates["G_A_agreement_ok"]
        and gates["G_J_same_plan"]
        and gates["G_R_ok"]
        and table_misses == 0
    )
    p2p_times = durations_or_censored(p2p["answer_s"])
    central_times = durations_or_censored(central["answer_s"])
    verdict = judge_join(p2p_times, central_times, p2p["payload_bytes"], central["transfer_bytes"])
    p50_p2p, p50_central = verdict["p2p"]["p50"], verdict["central"]["p50"]
    t_p, t_c = cost["t_P"]["median_s"], cost["t_C"]["median_s"]
    estimated = judge_join(
        durations_or_censored(
            [
                None if a["answer_s"] is None else a["answer_s"] + t_p * a["tries"]
                for a in answers_of(p2p)
            ]
        ),
        durations_or_censored(
            [
                None if a["answer_s"] is None else a["answer_s"] + t_c * a["tries"]
                for a in answers_of(central)
            ]
        ),
        p2p["payload_bytes"],
        central["transfer_bytes"],
    )
    return {
        "round": n,
        "valid": bool(valid),
        "gates": gates,
        "verdict_original": verdict,
        "ratio_p50_p2p_over_central": (
            p50_p2p / p50_central if p50_p2p is not None and p50_central else None
        ),
        "ce_cost_estimate": {
            "t_P_median_s": t_p,
            "t_C_median_s": t_c,
            "verdict": estimated,
            "note": "答えられた最初の試行までの試行の数 × 1 問あたりの採点時間の中央値を，答えられるまでの時間に足した見積もり（表を引くので実機の時間には入っていない）",
        },
        "p2p_devices": [
            {
                k: d[k]
                for k in (
                    "host",
                    "device",
                    "n_docs",
                    "up_s",
                    "device_tokenize_s",
                    "device_embed_s",
                    "device_kmeans_s",
                    "device_total_s",
                    "rtt_s",
                    "published_s",
                    "profile_s",
                    "assemble_s",
                    "member_s",
                    "payload_bytes",
                    "published_bytes",
                )
            }
            | {
                "first_answer_after_member_s": [
                    a["answer_s"] - d["member_s"] if a["answer_s"] is not None else None
                    for a in d["answers"]
                ]
            }
            for d in p2p["devices"]
        ],
        "central_devices": [
            {
                k: d[k]
                for k in (
                    "host",
                    "n_mails",
                    "transfer_bytes",
                    "transfer_s",
                    "embed_s",
                    "add_s",
                    "bm25_s",
                    "indexed_s",
                )
            }
            | {
                "first_answer_after_indexed_s": [
                    a["answer_s"] - d["indexed_s"] if a["answer_s"] is not None else None
                    for a in d["answers"]
                ]
            }
            for d in central["devices"]
        ],
        "p2p_by_host": by_host(p2p, t_p),
        "central_by_host": by_host(central, t_c),
        "p2p_tries": tries_summary(p2p),
        "central_tries": tries_summary(central),
        "p2p_payload_bytes": p2p["payload_bytes"],
        "p2p_published_bytes": p2p["published_bytes"],
        "central_transfer_bytes": central["transfer_bytes"],
        "central_exposed_mails": central["exposed_mails"],
        "pre_join": join["pre_join"],
        "setup": join["setup"],
    }


def main() -> None:
    """3 ラウンドを集計して verdict.json と join_summary.json を書く（`verdict_original` は規則どおりに 1 度だけ書く）．"""
    cost = json.loads(COST_FILE.read_text("utf-8"))
    rounds = [
        round_summary(n, cost)
        for n in ROUNDS
        if Path(f"results/d0009_a6_join_r{n}/join.json").exists()
    ]
    valid = [r for r in rounds if r["valid"]]
    holds = [bool(r["verdict_original"]["holds"]) for r in valid]
    ratios = [
        r["ratio_p50_p2p_over_central"]
        for r in valid
        if r["ratio_p50_p2p_over_central"] is not None
    ]
    table_owner = json.loads((OUT_DIR / "table/table_check_owner.json").read_text("utf-8"))
    table_central = json.loads((OUT_DIR / "table/table_check_central.json").read_text("utf-8"))
    g_tb = bool(table_owner["G_TB_owner_passed"] and table_central["G_TB_central_passed"])
    e2_old = json.loads((P2P_E2 / "verdict.json").read_text("utf-8"))["verdict_original"]
    adopted = g_tb and len(valid) >= MIN_VALID_ROUNDS
    verdict = {
        "verdict_original": {
            "H_A6J1": three_way(holds),
            "n_valid_rounds": len(valid),
            "valid_rounds": [r["round"] for r in valid],
            "holds_per_valid_round": holds,
            "H_A6J2_ratio_p50": {
                "per_round": ratios,
                "min": min(ratios) if ratios else None,
                "max": max(ratios) if ratios else None,
                "median": statistics.median(ratios) if ratios else None,
            },
            "G_TB": g_tb,
            "adopted": adopted,
            "independent_units_note": "受信箱は 15 だが，時間はほぼデバイス（3 台）の単位で決まるので，独立な単位は 3 である",
        },
        "verdict_revised": None,
    }
    (OUT_DIR / "verdict.json").write_text(
        json.dumps(verdict, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    summary = {
        "rounds": rounds,
        "table_check_owner": table_owner,
        "table_check_central": table_central,
        "p0007_e2_reference": {
            "note": "構成・質問が違うので比べない（並べるだけ）",
            "verdict_original": e2_old,
        },
    }
    (OUT_DIR / "join_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(verdict, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
