"""p0007 C の 3 回の実行（時間の主・事後の感度分析・正答率）を集計して，判定・表・照合を results/p0007_c_verdict/ に書く．

使い方（実機の実行と採点を済ませてから）:
    uv run python results/p0007_c_live/c_report.py
入力：results/p0007_c_freshness（主．verdict_original）・p0007_c_freshness_quiet（事後の感度分析．verdict_revised）・
      p0007_c_freshness_acc（snippet_return．採点済みの正答率）．
出力：results/p0007_c_verdict/{c_summary.json, c_tables.md, sim_vs_live.json}．主の実行の verdict.json に verdict_revised を足す
（verdict_original は書き換えない）．
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import compare_runs, merge_judgements, read_results
from atrium.dynamics import summarize_times
from atrium.verdict import write_verdict

RESULTS = Path("results")
MAIN = RESULTS / "p0007_c_freshness"
QUIET = RESULTS / "p0007_c_freshness_quiet"
ACC = RESULTS / "p0007_c_freshness_acc"
OUT = RESULTS / "p0007_c_verdict"
CONDITIONS = ("0", "60", "600", "inf")
REVISED_REASON = (
    "主の実行（original）の P2P の答えられるまでの時間は，届いたメールの順に単調に伸びた（先頭 40 通の p50 は 0.6 秒，最後の 40 通は 7 秒．"
    "答えられるのは全て 1 回目の検索）．原因は，答えられないメールを 30 秒間，間隔 0 で繰り返し検索する測定の道具（direct 33 通・routed 85 通）が，"
    "1 プロセスのノードを占有したことで，デバイスの性能ではない．繰り返しの上限を 2 秒にして測り直した（`--poll-timeout-s 2`）．"
    "判定の規則・メールの集合・中央の条件は同じ．目標に届かなかったことではなく，測定の道具が測る量に与えた負荷を根拠とする事後の見直し"
)


def percentile(times: list[float], q: float) -> float | None:
    """打ち切り（無限大）を含む時間の q パーセンタイル（無限大なら None）．"""
    if not times:
        return None
    value = float(np.percentile(np.asarray(times), q, method="higher"))
    return None if math.isinf(value) else value


def time_table(run: Path) -> dict[str, Any]:
    """1 つの実行の P2P（direct・routed）・中央 T ごとの答えられるまでの時間（全メール・判定の対象の集合）と，費用．"""
    f = json.loads((run / "freshness.json").read_text(encoding="utf-8"))
    v = json.loads((run / "verdict.json").read_text(encoding="utf-8"))["verdict_original"]
    rows = f["p2p"]["rows"]
    inf = math.inf

    def censored(x: float | None) -> float:
        return inf if x is None else float(x)

    direct = [censored(r["direct_s"]) for r in rows]
    routed = [censored(r.get("routed_s")) for r in rows]
    central = {
        k: [censored(t) for t in f["central"][k if k != "inf" else "inf"]["times_s"]]
        for k in CONDITIONS
    }
    eligible = [
        i
        for i, d in enumerate(direct)
        if not math.isinf(d) and any(not math.isinf(central[k][i]) for k in CONDITIONS)
    ]
    out: dict[str, Any] = {
        "n_arrivals": len(rows),
        "n_compared": len(eligible),
        "all": {
            "p2p_direct": summarize_times(direct),
            "p2p_routed": summarize_times(routed) if any(r.get("routed_s") for r in rows) else None,
            **{f"central_T{k}": summarize_times(central[k]) for k in CONDITIONS},
        },
        "compared": {
            "p2p_direct": summarize_times([direct[i] for i in eligible]),
            **{
                f"central_T{k}": summarize_times([central[k][i] for i in eligible])
                for k in CONDITIONS
            },
        },
        "verdict_holds": v["holds"],
        "p2p_p50_below_1s": v["p50_below_limit"],
        "sign_tests": {
            k: {kk: vv[kk] for kk in ("n_less", "n_greater", "p")}
            for k, vv in v["comparisons"].items()
        },
    }
    # 時間の推移（先頭から 40 通ごとの direct の p50．測定の道具の負荷の確認）
    answered = [(r["index"], r["direct_s"]) for r in rows if r["direct_s"] is not None]
    out["direct_p50_by_arrival_block"] = [
        round(statistics.median(t for i, t in answered if lo <= i < lo + 40), 3)
        for lo in range(0, len(rows), 40)
        if any(lo <= i < lo + 40 for i, _ in answered)
    ]
    out["p2p_cost"] = {
        key: {
            "p50": float(np.percentile(vals, 50)),
            "p95": float(np.percentile(vals, 95)),
            "max": float(max(vals)),
        }
        for key, vals in {
            "embed_s": [r["node_embed_s"] for r in rows],
            "add_s_with_bm25": [r["node_add_s"] + r["node_bm25_s"] for r in rows],
            "add_rtt_s": [r["add_rtt_s"] for r in rows],
        }.items()
    }
    # デバイスの種類ごと（i5-8350U の専門家 13.x・14.x と，GPU の PC の CPU 15.x）の埋め込みと，答えられるまでの時間
    placement = json.loads((run / "placement.json").read_text(encoding="utf-8"))
    host_of = {s: n["host"] for n in placement["nodes"] for s in n["shard_ids"]}
    out["by_device_class"] = {}
    for label, is_gpu_pc in (("i5_expert_13x_14x", False), ("gpu_pc_cpu_15x", True)):
        sel = [r for r in rows if host_of[r["inbox"]].startswith("192.168.15.") == is_gpu_pc]
        embed = [r["node_embed_s"] for r in sel]
        direct_ok = [r["direct_s"] for r in sel if r["direct_s"] is not None]
        out["by_device_class"][label] = {
            "n": len(sel),
            "embed_s_p50": float(np.percentile(embed, 50)),
            "embed_s_p95": float(np.percentile(embed, 95)),
            "direct_s_p50": float(np.percentile(direct_ok, 50)),
            "direct_s_p95": float(np.percentile(direct_ok, 95)),
        }
    out["embedder_load_s"] = [e["load_s"] for e in f["preparation"]["embedders"]]
    out["embedder_devices"] = sorted({e["device"] for e in f["preparation"]["embedders"]})
    out["central_cost"] = {}
    for k in CONDITIONS:
        flushes = f["central"][k]["flushes"]
        if not flushes:
            continue
        out["central_cost"][f"T{k}"] = {
            "n_flushes": len(flushes),
            "mails_per_flush_p50": statistics.median(x["n_mails"] for x in flushes),
            "transfer_bytes_total": sum(x["transfer_bytes"] for x in flushes),
            "transfer_bytes_per_flush_p50": statistics.median(x["transfer_bytes"] for x in flushes),
            "transfer_bytes_per_flush_max": max(x["transfer_bytes"] for x in flushes),
            "transfer_s_p50": statistics.median(x["transfer_s"] for x in flushes),
            "transfer_s_max": max(x["transfer_s"] for x in flushes),
            "embed_s_p50": statistics.median(x["embed_s"] for x in flushes),
            "embed_s_max": max(x["embed_s"] for x in flushes),
            "add_s_p50": statistics.median(x["add_s"] for x in flushes),
            "add_s_max": max(x["add_s"] for x in flushes),
            "scheduled_wait_p50_s": statistics.median(
                w for w in f["central"][k]["scheduled_wait_s"] if w is not None
            ),
        }
    return out


def accuracy_table(run: Path) -> dict[str, Any]:
    """条件ごとの正答率と，P2P に対する McNemar の正確検定（メールごとの正誤の対応）．"""
    merged: dict[str, list[dict[str, Any]]] = {}
    for cond_dir in sorted(p for p in run.iterdir() if (p / "judgements.jsonl").exists()):
        merged[cond_dir.name] = merge_judgements(
            read_results(cond_dir / "results.jsonl"), cond_dir / "judgements.jsonl"
        )
    out: dict[str, Any] = {}
    for name, rows in merged.items():
        correct = sum(bool(r.get("correct")) for r in rows)
        out[name] = {
            "n": len(rows),
            "n_failed": sum(r.get("error") is not None for r in rows),
            "n_correct": correct,
            "accuracy": correct / len(rows),
        }
        if name != "p2p" and "p2p" in merged:
            out[name]["vs_p2p_mcnemar"] = compare_runs(merged["p2p"], rows)
    return out


def fmt(x: float | None, digits: int = 2) -> str:
    """表の数値（None は打ち切り）．"""
    return "打ち切り" if x is None else f"{x:.{digits}f}"


def render_tables(main: dict[str, Any], quiet: dict[str, Any], acc: dict[str, Any]) -> str:
    """c_tables.md の本文．"""
    lines = ["# p0007 C の表（`c_report.py` が出力）", ""]
    for title, t in (
        ("主の実行（verdict_original）", main),
        ("測り直し（事後．verdict_revised）", quiet),
    ):
        lines += [
            f"## {title}",
            "",
            f"届けたメール {t['n_arrivals']} 通．判定の対象 {t['n_compared']} 通．判定 {'成立' if t['verdict_holds'] else '不成立'}．",
            "",
            "| 条件 | 全メール n_打切 | 全 p50（秒） | 全 p95 | 対象 p50 | 対象 p95 |",
            "|---|---|---|---|---|---|",
        ]
        names = (
            ["p2p_direct"]
            + (["p2p_routed"] if t["all"]["p2p_routed"] else [])
            + [f"central_T{k}" for k in CONDITIONS]
        )
        for n in names:
            a = t["all"][n]
            c = t["compared"].get(n)
            lines.append(
                f"| {n} | {a['n_censored']} | {fmt(a['p50'], 3)} | {fmt(a['p95'], 3)} | "
                f"{fmt(c['p50'], 3) if c else '-'} | {fmt(c['p95'], 3) if c else '-'} |"
            )
        lines += [
            "",
            "符号検定（P2P direct < 中央 T）："
            + "，".join(
                f"T={k}: {s['n_less']} 対 / {s['n_greater']} 対，p = {s['p']:.2e}"
                for k, s in t["sign_tests"].items()
            ),
            "",
            f"direct の p50 の推移（40 通ごと）：{t['direct_p50_by_arrival_block']}",
            "",
        ]
        c = t["p2p_cost"]
        lines += [
            "デバイスの CPU の費用（秒）：埋め込み p50 {:.3f}・p95 {:.3f}・最大 {:.3f}，索引への追加（BM25 の作り直しを含む）p50 {:.2e}・p95 {:.2e}，"
            "届ける要求の往復 p50 {:.3f}・p95 {:.3f}．".format(
                c["embed_s"]["p50"],
                c["embed_s"]["p95"],
                c["embed_s"]["max"],
                c["add_s_with_bm25"]["p50"],
                c["add_s_with_bm25"]["p95"],
                c["add_rtt_s"]["p50"],
                c["add_rtt_s"]["p95"],
            ),
            "",
            "| 中央 | 転送の回数 | 1 回のメール数 p50 | 転送の合計（byte） | 1 回の転送 p50（byte） | 転送の時間 p50 / 最大（秒） | 中央の埋め込み p50 / 最大 | 追加 p50 / 最大 | 予定の待ち p50 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for k, v in t["central_cost"].items():
            lines.append(
                f"| {k} | {v['n_flushes']} | {v['mails_per_flush_p50']:g} | {v['transfer_bytes_total']} | {v['transfer_bytes_per_flush_p50']:.0f} | "
                f"{v['transfer_s_p50']:.3f} / {v['transfer_s_max']:.3f} | {v['embed_s_p50']:.3f} / {v['embed_s_max']:.3f} | "
                f"{v['add_s_p50']:.1e} / {v['add_s_max']:.1e} | {v['scheduled_wait_p50_s']:.1f} |"
            )
        lines += [
            "",
            "| デバイスの種類 | n | 埋め込み p50 / p95（秒） | direct p50 / p95（秒） |",
            "|---|---|---|---|",
            *(
                f"| {k} | {v['n']} | {v['embed_s_p50']:.3f} / {v['embed_s_p95']:.3f} | {v['direct_s_p50']:.3f} / {v['direct_s_p95']:.3f} |"
                for k, v in t["by_device_class"].items()
            ),
            "",
        ]
    lines += [
        "## 新しいメールについての質問の正答率（届いてから 1 秒後．snippet_return．採点 qwen3:8b）",
        "",
        "| 条件 | n | 正答 | 正答率 | P2P との差の検定（McNemar．P2P のみ正 / 条件のみ正 / p） |",
        "|---|---|---|---|---|",
    ]
    for name, a in acc.items():
        m = a.get("vs_p2p_mcnemar")
        lines.append(
            f"| {name} | {a['n']} | {a['n_correct']} | {a['accuracy']:.4f} | "
            + (
                f"{m['only_a_correct']} / {m['only_b_correct']} / {m['mcnemar_p']:.3g}"
                if m
                else "-"
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    """3 回の実行から判定・表・照合を作る．"""
    OUT.mkdir(parents=True, exist_ok=True)
    main_t, quiet_t, acc_t = time_table(MAIN), time_table(QUIET), accuracy_table(ACC)
    summary = {"main": main_t, "quiet": quiet_t, "accuracy": acc_t}
    (OUT / "c_summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (OUT / "c_tables.md").write_text(render_tables(main_t, quiet_t, acc_t), encoding="utf-8")
    quiet_verdict = json.loads((QUIET / "verdict.json").read_text(encoding="utf-8"))[
        "verdict_original"
    ]
    revised = {
        "note": "事後の見直し（測定の道具の負荷を除いた測り直し．results/p0007_c_freshness_quiet）",
        "reason": REVISED_REASON,
        **quiet_verdict,
    }
    verdict = write_verdict(MAIN, None, revised)
    (OUT / "verdict.json").write_text(
        json.dumps(verdict, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    sims = {
        name: json.loads((run / "sim_vs_live.json").read_text(encoding="utf-8"))
        for name, run in (("main", MAIN), ("quiet", QUIET))
    }
    (OUT / "sim_vs_live.json").write_text(
        json.dumps(sims, indent=1, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
