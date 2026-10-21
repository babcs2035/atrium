"""p0007 E1（障害）・E2（参加）の実行を集計して，表と併記の値を results/p0007_e_verdict/ に書く（判定の本体は各実行の verdict.json）．

使い方（実機の実行を済ませてから）:
    uv run python results/p0007_e_live/e_report.py
入力：results/p0007_e1_failure（failure.json）・results/p0007_e1_diag_container（診断．質問ごとの記録つきの container の再実行）・
      results/p0007_e2_join（join.json）．
出力：results/p0007_e_verdict/{e_summary.json, e_tables.md}．各実行の verdict_original は書き換えない．
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atrium.dynamics import integrate_rates

RESULTS = Path("results")
E1 = RESULTS / "p0007_e1_failure"
DIAG = RESULTS / "p0007_e1_diag_container"
E2 = RESULTS / "p0007_e2_join"
OUT = RESULTS / "p0007_e_verdict"
# probe を送るデバイスの数（config.yaml の routing.hybrid_probe_d.enronqa）と，専門家の数（EnronQA の全デバイス）
PROBE_D = 16
N_DEVICES = 30


def stall_share(k: int) -> float:
    """k 台のどれかが probe の集合（D 台）に入る確率（無作為な集合の近似．質問は止まったノードへの要求の応答を待って時間切れになる）．"""
    keep = 1.0
    for i in range(k):
        keep *= (N_DEVICES - PROBE_D - i) / (N_DEVICES - i)
    return 1.0 - keep


def e1_rows(failure: dict[str, Any]) -> list[dict[str, Any]]:
    """（止め方，k）ごとの表の行．窓の積分（主）と，中央が検出と同時に再起動を始める場合の積分（併記）を出す．"""
    central = failure["central"]
    restart_s = central["restart"]["restart_to_answer_s"]
    rate_after = central["hit_rate_after_restart"]
    rows = []
    for entry in failure["p2p"]:
        bin_s = entry["bin_s"]
        start = entry["bin_start_s"]
        # 併記：中央が障害の直後に再起動を始める（down_s = 0）場合．窓は，再起動の時間 + post_s．P2P は止めたままの同じ区間
        short_window = restart_s + failure["params"]["post_s"]
        p2p_short = integrate_rates(entry["rates"], start, bin_s, 0.0, short_window)
        central_short = rate_after * failure["params"]["post_s"]
        base = entry["baseline_hit_rate"]
        rows.append(
            {
                "mode": entry["mode"],
                "k": entry["k"],
                "baseline_hit_rate": base,
                "fault_hit_rate": entry["fault_hit_rate"],
                "fault_relative_to_baseline": entry["fault_hit_rate"] / base if base else None,
                "fault_gold_down_share": entry["fault_gold_down_share"],
                "n_questions": entry["n_questions"],
                "n_timeouts": entry["n_timeouts"],
                "detection_s_p50": entry["detection_s_p50"],
                "ready_s_max": entry["ready_s_max"],
                "first_failed_request_s": entry["first_failed_request_s"],
                "p2p_integral": entry["integral"],
                "central_integral": central["integral"],
                "window_s": entry["window_s"],
                "p2p_integral_short_window": p2p_short,
                "central_integral_short_window": central_short,
                "short_window_s": short_window,
                "stall_share_model": stall_share(entry["k"]),
            }
        )
    return rows


def e1_markdown(failure: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """E1 の表（Markdown）．"""
    c = failure["central"]
    r = c["restart"]
    lines = [
        "## E1 障害",
        "",
        f"中央の再起動：{r['restart_to_answer_s']:.3f} 秒（kill {r['kill_s']:.3f}・準備 {r['restart_to_ready_s']:.3f}．"
        f"索引の読み込み {r['index_load']['total_s']:.3f} 秒 = 本文 {r['index_load']['read_docs_s']:.3f} + 埋め込み "
        f"{r['index_load']['read_emb_s']:.3f}・BM25 {r['index_load']['bm25_s']:.3f}．{r['index_load']['n_docs']} 通・"
        f"{r['index_bytes'] / 1e6:.0f} MB）．再起動のあとの割合 {c['hit_rate_after_restart']:.3f}．"
        f"窓 W = {failure['window_s']:.2f} 秒（停止 {c['outage_s']:.2f}）．",
        "",
        "| 止め方 | k | 平常 | 停止中 | 比 | 正解が停止側 | 時間切れ / 質問 | 検出 p50 (s) | 再開 (s) | P2P 積分 | 中央 積分 | 併記：P2P（短い窓） | 併記：中央（短い窓） |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in rows:
        lines.append(
            f"| {x['mode']} | {x['k']} | {x['baseline_hit_rate']:.3f} | {x['fault_hit_rate']:.3f} | "
            f"{x['fault_relative_to_baseline']:.2f} | {x['fault_gold_down_share']:.3f} | "
            f"{x['n_timeouts']} / {x['n_questions']} | {x['detection_s_p50']:.2f} | {x['ready_s_max']:.2f} | "
            f"{x['p2p_integral']:.1f} | {x['central_integral']:.1f} | {x['p2p_integral_short_window']:.1f} | "
            f"{x['central_integral_short_window']:.1f} |"
        )
    red = c["redundancy"]
    lines += [
        "",
        f"冗長化（2 台）：hot 待機の切り替え {red['hot_standby_switch_s']:.2f} 秒（検出 + 操作 0 秒の仮定）・積分 {red['integral_hot_standby']:.1f}／"
        f"cold 待機 {red['cold_standby_switch_s']:.2f} 秒・積分 {red['integral_cold_standby']:.1f}．"
        f"複製：保存が {red['replication']['extra_storage_bytes'] / 1e6:.0f} MB 増え，更新ごとの転送が 1 回増える．",
        "",
    ]
    return "\n".join(lines)


def diag_rows(failure: dict[str, Any]) -> list[dict[str, Any]]:
    """診断（container）：止めている間の質問を，時間切れか否かで分け，答えられた割合・正解が止まった側の割合を数える．"""
    down_s = failure["params"]["down_s"]
    rows = []
    for entry in failure["p2p"]:
        during = [q for q in entry["questions"] if 0 <= q["t_s"] < down_s]
        timed_out = [q for q in during if q["timed_out"]]
        answered = [q for q in during if not q["timed_out"]]
        rows.append(
            {
                "k": entry["k"],
                "n_fault_questions": len(during),
                "timed_out_share": len(timed_out) / len(during),
                "stall_share_model": stall_share(entry["k"]),
                "hit_rate_all": sum(q["hit"] for q in during) / len(during),
                "n_answered_in_time": len(answered),
                "hit_rate_answered_in_time": sum(q["hit"] for q in answered) / len(answered)
                if answered
                else None,
                "gold_down_share_among_timed_out": sum(q["gold_down"] for q in timed_out)
                / len(timed_out)
                if timed_out
                else None,
                "integral": entry["integral"],
            }
        )
    return rows


def diag_markdown(rows: list[dict[str, Any]]) -> str:
    """診断の表（Markdown）．"""
    lines = [
        "### 診断：container の停止で答えられた割合が下がる原因（`p0007_e1_diag_container`．質問ごとの記録）",
        "",
        "| k | 停止中の質問 | 時間切れの割合 | 無作為な probe 集合のモデル | 割合（全体） | 時間内に答えた数 | その割合 | 時間切れのうち正解が停止側 | 積分 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for x in rows:
        answered = x["hit_rate_answered_in_time"]
        gold = x["gold_down_share_among_timed_out"]
        lines.append(
            f"| {x['k']} | {x['n_fault_questions']} | {x['timed_out_share']:.2f} | {x['stall_share_model']:.2f} | "
            f"{x['hit_rate_all']:.3f} | {x['n_answered_in_time']} | "
            f"{'-' if answered is None else f'{answered:.3f}'} | {'-' if gold is None else f'{gold:.2f}'} | {x['integral']:.1f} |"
        )
    return "\n".join(lines) + "\n"


def e2_markdown(join: dict[str, Any], verdict: dict[str, Any]) -> str:
    """E2 の表（Markdown）：デバイスごとの時間・転送量，P2P と中央の要約，通知の遅れの感度．"""
    v = verdict["verdict_original"]
    lines = [
        "## E2 参加",
        "",
        f"最初のメンバー {join['setup']['n_devices']} 台の公開情報の作成（準備）：デバイスの p50 {join['setup']['device_total_s_p50']:.0f} 秒．"
        f"加わる 3 台 = 15 受信箱（種 12）．参加前の hit {join['pre_join']['hit_rate']:.2f}．",
        "",
        "| ホスト | メール数 | P2P 公開情報の作成 (s) | 1 通あたり (s) | メンバー表まで (s) | 答えられるまで (s) | 公開情報 JSON (MB) | 中央 転送 (MB) | 中央 転送 (s) | 中央 埋め込み (s) | 中央 答えられるまで (s) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p, c in zip(join["p2p"]["devices"], join["central"]["devices"], strict=True):
        lines.append(
            f"| {p['host']} | {p['n_docs']} | {p['device_total_s']:.0f} | {p['device_total_s'] / p['n_docs']:.3f} | "
            f"{p['member_s']:.0f} | {p['answers'][0]['answer_s']:.0f} | {p['payload_bytes'] / 1e6:.2f} | "
            f"{c['transfer_bytes'] / 1e6:.2f} | {c['transfer_s']:.2f} | {c['embed_s']:.1f} | {c['answers'][0]['answer_s']:.0f} |"
        )
    lines += [
        "",
        f"受信箱ごとの答えられるまでの時間（15 受信箱）：P2P p50 {v['p2p']['p50']:.0f}・p95 {v['p2p']['p95']:.0f} 秒，"
        f"中央 p50 {v['central']['p50']:.1f}・p95 {v['central']['p95']:.1f} 秒（比 {v['p2p']['p50'] / v['central']['p50']:.1f} 倍）．"
        f"転送量：P2P {v['p2p_bytes'] / 1e6:.2f} MB（公開情報の JSON．`advert_bytes` の数え方では {join['p2p']['published_bytes'] / 1e6:.2f} MB），"
        f"中央 {v['central_bytes'] / 1e6:.2f} MB．デバイスの外へ出たメール：P2P {join['p2p']['exposed_mails']} 通・中央 {join['central']['exposed_mails']} 通．"
        f"判定：faster = {v['faster']}・equivalent = {v['equivalent']}・less_transfer = {v['less_transfer']} → holds = {v['holds']}．",
        "",
        "| L_join（問） | 通知前の加わる受信箱の質問 | 通知前の hit | 通知後の hit 率 | 最初の hit の位置（中央値） | 全受信箱が答えられるまでの位置 |",
        "|---|---|---|---|---|---|",
    ]
    for d in join["delays"]:
        pos = sorted(d["first_hit_position"].values())
        lines.append(
            f"| {d['delay_questions']} | {d['joiner_questions_before_notice']} | {d['joiner_hits_before_notice']} | "
            f"{d['hit_rate_after_notice']:.3f} | {pos[len(pos) // 2]} | {pos[-1]} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    """E1・E2 を集計して書く．"""
    OUT.mkdir(parents=True, exist_ok=True)
    failure = json.loads((E1 / "failure.json").read_text(encoding="utf-8"))
    rows = e1_rows(failure)
    diag = diag_rows(json.loads((DIAG / "failure.json").read_text(encoding="utf-8")))
    join = json.loads((E2 / "join.json").read_text(encoding="utf-8"))
    verdict = json.loads((E2 / "verdict.json").read_text(encoding="utf-8"))
    summary: dict[str, Any] = {"e1": rows, "e1_diagnosis_container": diag, "e2": verdict}
    text = (
        e1_markdown(failure, rows) + "\n" + diag_markdown(diag) + "\n" + e2_markdown(join, verdict)
    )
    (OUT / "e_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (OUT / "e_tables.md").write_text(text, encoding="utf-8")
    # 照合（K4）：E1 は offline の予測（正解の受信箱が動いている割合）と実測，container の時間切れの割合（無作為な probe 集合のモデル）と実測．
    # E2 は参加前の hit と，組み立てた要約とオフラインの要約の候補の一致率
    sim = json.loads((E1 / "sim_vs_live.json").read_text(encoding="utf-8"))
    join_sim = json.loads((E2 / "sim_vs_live.json").read_text(encoding="utf-8"))
    sim_vs_live = {
        "e1_fault_hit_rate": sim["rows"],
        "e1_container_timed_out_share": [
            {
                "k": d["k"],
                "model": d["stall_share_model"],
                "live": d["timed_out_share"],
                "difference": d["timed_out_share"] - d["stall_share_model"],
            }
            for d in diag
        ],
        "e2": join_sim,
        "causes": (
            "availability は予測（平常 × 正解の受信箱が動く割合）と ±0.02 で一致．container は予測より 0.35〜0.61 低く，"
            "原因は，質問者に失敗の検出・除外が無く，止まったノードが probe の集合に入った質問が時間切れまで待つこと"
            "（時間切れの割合が無作為な probe 集合のモデルと k = 1・3 で 0.01〜0.05 の差．時間内に答えた質問の割合は平常と同じ）．"
            "オフラインの予測は「止まったノードは即座に分かる」前提で，この停止の待ちを持たない"
        ),
    }
    (OUT / "sim_vs_live.json").write_text(
        json.dumps(sim_vs_live, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(text)


if __name__ == "__main__":
    main()
