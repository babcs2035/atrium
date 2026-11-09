"""d0009 A5 bm25_index_add（Iteration 33）：3 ラウンドの freshness-run の結果から，ゲート・H-A5B1〜B3・記述 (i)〜(vii) を出す．

使い方（リポジトリの直下．判定はオフライン．実機・GPU は使わない）:
    # 記述 (vii)：届いたメールの公開情報の差分の数え上げ（制御点 wafl-ctrl5 の data で，CPU．`uv run` の環境が要る）
    uv run python scripts/d0009/a5b_verdict.py public-diff --data-dir /home/denjo/atrium-data \
        --out results/d0009_a5_bm25_add/public_diff.json
    # ゲートと判定（3 ラウンドの results/<run>/{freshness,run_meta,verdict}.json を読む）
    uv run python scripts/d0009/a5b_verdict.py verdict --runs d0009_a5b_r1 d0009_a5b_r2 d0009_a5b_r3 \
        --node-check results/d0009_a5_bm25_add/node_check.json --public-diff results/d0009_a5_bm25_add/public_diff.json \
        --out results/d0009_a5_bm25_add

出力は `<out>/{verdict.json, bm25_add.json, analysis_report.md}`．`verdict_original` は既にあれば書き換えない（atrium.verdict）．
`node_check.json` は `{"n_nodes": 30, "n_ok": 30}`（deploy 後のノードの起動ログで `fusion=rrf, body_bm25_probe=True` の台数．G-C）．
時間の比較は freshness-run と同じ「比べたメール」（P2P で答えられ，中央の有限の T のどれかで答えられたもの）で行う．
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.dynamics import (
    durations_or_censored,
    eligible_indices,
    parse_interval,
    sign_test_less,
    summarize_times,
)
from atrium.verdict import write_verdict

RESULTS = Path("results")
P2P_P50_LIMIT_S = (
    1.0  # H-A5B1：ノードの BM25 の作り直しと P2P の答えられるまでの p50 の上限（p0007 C の閾値）
)
CENTRAL_BM25_LOW_S = 5.0  # H-A5B2：中央の BM25 の作り直しと T0 の p50 が「失われた」と言える下限
CENTRAL_T0_KEPT_S = 1.0  # H-A5B2：全ての有効なラウンドで T0 の p50 がこれ未満なら「失われない」
SIGNIFICANCE_ALPHA = 0.05  # H-A5B3：対にした片側の符号検定
MIN_COMPARED = 100  # G-N
MIN_VALID_ROUNDS = 2
EXPECTED_NODES = 30
P0007_C_QUIET = RESULTS / "p0007_c_freshness_quiet" / "freshness.json"  # 記述 (v)
INF_KEY = "inf"


def read_json(path: Path) -> Any:
    """JSON ファイルを読む．"""
    return json.loads(path.read_text(encoding="utf-8"))


def times_of(row: Mapping[str, Any]) -> list[float | None]:
    """中央の 1 条件の，メールごとの答えられるまでの時間（答えられなければ None）．"""
    return list(row["times_s"])


def compared_times(
    freshness: Mapping[str, Any],
) -> tuple[list[int], list[float], dict[str, list[float]]]:
    """比べたメール（番号）と，その P2P（direct）・中央の各 T の時間（打ち切りは無限大）．freshness-run の eligible_indices と同じ規則．"""
    direct = [r["direct_s"] for r in freshness["p2p"]["rows"]]
    central = freshness["central"]
    eligible = eligible_indices(
        direct, {parse_interval(k): times_of(v) for k, v in central.items()}
    )
    p2p = durations_or_censored([direct[i] for i in eligible])
    by_t = {
        k: durations_or_censored([times_of(v)[i] for i in eligible]) for k, v in central.items()
    }
    return eligible, p2p, by_t


def percentile_summary(values: Sequence[float]) -> dict[str, Any]:
    """件数・最小・p50・p95・最大（空なら件数 0 だけ）．"""
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "n": len(values),
        "min": float(arr.min()),
        "p50": float(np.percentile(arr, 50, method="higher")),
        "p95": float(np.percentile(arr, 95, method="higher")),
        "max": float(arr.max()),
    }


def finite_flushes(freshness: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """有限の T の条件ごとの転送の一覧．"""
    return {k: v["flushes"] for k, v in freshness["central"].items() if k != INF_KEY}


def gates_of_round(
    run_meta: Mapping[str, Any],
    freshness: Mapping[str, Any],
    verdict_body: Mapping[str, Any],
    node_check: Mapping[str, Any] | None,
) -> dict[str, bool]:
    """1 ラウンドのゲート G-C・G-P・G-B・G-N の真偽．"""
    rows = freshness["p2p"]["rows"]
    flushes = finite_flushes(freshness)
    node_ok = (
        node_check is not None
        and node_check["n_nodes"] == EXPECTED_NODES
        and node_check["n_ok"] == EXPECTED_NODES
    )
    return {
        "G-C": bool(
            run_meta.get("routing") == "bm25_probe"
            and run_meta.get("retrieval_fusion") == "rrf"
            and run_meta.get("question_split") == "holdout"
            and node_ok
        ),
        "G-P": freshness["preparation"]["pre_arrival_hit_rate"] == 0,
        "G-B": bool(
            rows
            and all(r["node_bm25_s"] > 0 for r in rows)
            and flushes
            and all(fl and all(f["bm25_s"] > 0 for f in fl) for fl in flushes.values())
            and all(
                v["n_docs_after_truncate"] == v["n_docs_base"]
                for v in freshness["central"].values()
            )
        ),
        "G-N": verdict_body["eligibility"]["n_compared"] >= MIN_COMPARED,
    }


def metrics_of_round(freshness: Mapping[str, Any]) -> dict[str, Any]:
    """1 ラウンドの H-A5B1〜B3 の元の数値．"""
    _, p2p, by_t = compared_times(freshness)
    rows = freshness["p2p"]["rows"]
    t0 = by_t["0"]
    flushes = finite_flushes(freshness)
    all_bm25 = [f["bm25_s"] for fl in flushes.values() for f in fl]
    p2p_faster = sign_test_less(p2p, t0)
    t0_faster = sign_test_less(t0, p2p)
    return {
        "n_compared": len(p2p),
        "node_bm25_s": summarize_times([r["node_bm25_s"] for r in rows]),
        "p2p_direct_compared": summarize_times(p2p),
        "central_bm25_s_all_flushes": summarize_times(all_bm25),
        "central_t0_compared": summarize_times(t0),
        "p2p_shorter": {**p2p_faster, "significant": bool(p2p_faster["p"] < SIGNIFICANCE_ALPHA)},
        "central_t0_shorter": {
            **t0_faster,
            "significant": bool(t0_faster["p"] < SIGNIFICANCE_ALPHA),
        },
    }


def judge_hypotheses(metrics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """有効なラウンドの数値から H-A5B1〜B3 を判定する（有効なラウンドが 0 なら全て「決まらない」）．"""
    if not metrics:
        return {k: "決まらない（有効なラウンドが無い）" for k in ("H-A5B1", "H-A5B2", "H-A5B3")}
    b1 = all(
        (m["node_bm25_s"]["p50"] or math.inf) < P2P_P50_LIMIT_S
        and (m["p2p_direct_compared"]["p50"] or math.inf) < P2P_P50_LIMIT_S
        for m in metrics
    )
    lost = all(
        (m["central_bm25_s_all_flushes"]["p50"] or 0.0) >= CENTRAL_BM25_LOW_S
        and (m["central_t0_compared"]["p50"] or 0.0) >= CENTRAL_BM25_LOW_S
        for m in metrics
    )
    kept = all((m["central_t0_compared"]["p50"] or math.inf) < CENTRAL_T0_KEPT_S for m in metrics)
    if all(m["p2p_shorter"]["significant"] for m in metrics):
        b3 = "P2P が中央 T0 より速い"
    elif all(m["central_t0_shorter"]["significant"] for m in metrics):
        b3 = "中央 T0 が速い"
    else:
        b3 = "決まらない"
    return {
        "H-A5B1": "BM25 を含めても P2P は 1 秒未満で追従する" if b1 else "満たさない",
        "H-A5B2": (
            "BM25 の作り直しで中央の即時の追従（T0）が失われる"
            if lost
            else "失われない"
            if kept
            else "決まらない"
        ),
        "H-A5B3": b3,
    }


def serial_rebuild_wait(arrival_gap_s: float, n: int, rebuild_s: float) -> dict[str, Any]:
    """記述 (iv)：1 通ごとに BM25 を作り直す（同期に 1 回ずつ）場合の T0 の待ちの計算（作り直しだけを数えた下限）と，T0 を保てる届く速さの上限．"""
    finish = 0.0
    waits: list[float] = []
    for i in range(n):
        arrival = i * arrival_gap_s
        finish = max(finish, arrival) + rebuild_s
        waits.append(finish - arrival)
    return {
        "rebuild_s_used": rebuild_s,
        "wait_first_s": waits[0] if waits else None,
        "wait_p50_s": float(np.percentile(waits, 50)) if waits else None,
        "wait_last_s": waits[-1] if waits else None,
        "max_arrival_rate_per_s": 1.0 / rebuild_s if rebuild_s > 0 else None,
    }


def breakdown_of_round(
    freshness: Mapping[str, Any],
    placement: Mapping[str, Any] | None,
    inbox_sizes: Mapping[str, int] | None,
) -> dict[str, Any]:
    """記述 (ii)(iii)(vi)：時間の内訳（P2P は全体と持ち主のホストごと，中央は条件ごと）・転送の回数・routed の時間．"""
    rows = freshness["p2p"]["rows"]
    host_of: dict[str, str] = {}
    if placement is not None:
        for node in placement["nodes"]:
            for shard in node["shard_ids"]:
                host_of[shard] = node["host"]
    by_host: dict[str, list[Mapping[str, Any]]] = {}
    for r in rows:
        by_host.setdefault(host_of.get(r["inbox"], "unknown"), []).append(r)

    def p2p_parts(subset: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            key: percentile_summary([r[key] for r in subset])
            for key in (
                "node_embed_s",
                "node_add_s",
                "node_bm25_s",
                "node_total_s",
                "add_rtt_s",
                "direct_s",
            )
            if any(r[key] is not None for r in subset)
        }

    central: dict[str, Any] = {}
    for key, row in freshness["central"].items():
        fl = row["flushes"]
        central[key] = {
            "n_flushes": len(fl),
            "mails_per_flush": percentile_summary([f["n_mails"] for f in fl]),
            **{
                part: percentile_summary([f[part] for f in fl])
                for part in ("transfer_s", "embed_s", "add_s", "bm25_s")
            },
        }
    bm25_by_size: list[dict[str, Any]] = []
    if inbox_sizes:
        for r in rows:
            if r["inbox"] in inbox_sizes:
                bm25_by_size.append(
                    {"n_docs": inbox_sizes[r["inbox"]], "node_bm25_s": r["node_bm25_s"]}
                )
    return {
        "p2p_all": p2p_parts(rows),
        "p2p_by_host": {h: {"n": len(v), **p2p_parts(v)} for h, v in sorted(by_host.items())},
        "p2p_bm25_vs_inbox_size": _size_bins(bm25_by_size),
        "central": central,
        "routed": freshness["p2p"]["routed"],
    }


def _size_bins(pairs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """受信箱の文書の数の四分位ごとの node_bm25_s の p50（受信箱の数の情報が無ければ空）．"""
    if not pairs:
        return []
    sizes = np.asarray([p["n_docs"] for p in pairs], dtype=np.float64)
    edges = np.percentile(sizes, [0, 25, 50, 75, 100])
    out: list[dict[str, Any]] = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        picked = [p["node_bm25_s"] for p in pairs if lo <= p["n_docs"] <= hi]
        out.append({"n_docs_range": [float(lo), float(hi)], **percentile_summary(picked)})
    return out


def p0007_c_reference() -> dict[str, Any] | None:
    """記述 (v)：p0007 C の quiet の値（質問集合と制御点の機器が違うので並べるだけ）．"""
    if not P0007_C_QUIET.exists():
        return None
    data = read_json(P0007_C_QUIET)
    return {
        "p2p_direct": data["p2p"]["direct"],
        "node_bm25_s": summarize_times([r["node_bm25_s"] for r in data["p2p"]["rows"]]),
        "central_T0": data["central"]["0"]["summary"],
        "note": "質問集合（test）・fusion（none）・制御点の機器が違うので，数値は比べない",
    }


def evaluate(
    runs: Sequence[str], node_check_path: Path | None, public_diff_path: Path | None, out_dir: Path
) -> dict[str, Any]:
    """3 ラウンドを集計して verdict.json・bm25_add.json・analysis_report.md を out_dir に書く．"""
    node_check = (
        read_json(node_check_path) if node_check_path and node_check_path.exists() else None
    )
    public_diff = (
        read_json(public_diff_path) if public_diff_path and public_diff_path.exists() else None
    )
    inbox_sizes = public_diff["inbox_sizes"] if public_diff else None
    rounds: dict[str, Any] = {}
    valid_metrics: list[Mapping[str, Any]] = []
    for run in runs:
        base = RESULTS / run
        freshness = read_json(base / "freshness.json")
        run_meta = read_json(base / "run_meta.json")
        verdict_body = read_json(base / "verdict.json")["verdict_original"]
        placement = (
            read_json(base / "placement.json") if (base / "placement.json").exists() else None
        )
        gates = gates_of_round(run_meta, freshness, verdict_body, node_check)
        metrics = metrics_of_round(freshness)
        valid = all(gates.values())
        if valid:
            valid_metrics.append(metrics)
        waits = serial_rebuild_wait(
            freshness["params"]["arrival_gap_s"],
            freshness["params"]["n_arrivals"],
            metrics["central_bm25_s_all_flushes"]["p50"] or 0.0,
        )
        rounds[run] = {
            "gates": gates,
            "valid": valid,
            "metrics": metrics,
            "judge_freshness_i": verdict_body,
            "breakdown_ii_iii_vi": breakdown_of_round(freshness, placement, inbox_sizes),
            "serial_rebuild_iv": waits,
            "t0_coalesce_flushes_iii": {
                "n_flushes": len(freshness["central"]["0"]["flushes"]),
                "n_mails": [f["n_mails"] for f in freshness["central"]["0"]["flushes"]],
            },
        }
    n_valid = len(valid_metrics)
    judged = judge_hypotheses(valid_metrics) if n_valid >= MIN_VALID_ROUNDS else None
    original = {
        "hypothesis": "H-A5B1（P2P の追加の費用）・H-A5B2（中央の追加の費用）・H-A5B3（P2P 対 中央 T0）",
        "n_valid_rounds": n_valid,
        "established": n_valid >= MIN_VALID_ROUNDS,
        "judgements": judged,
        "valid_rounds": [r for r, v in rounds.items() if v["valid"]],
        "thresholds": {
            "p2p_p50_limit_s": P2P_P50_LIMIT_S,
            "central_bm25_low_s": CENTRAL_BM25_LOW_S,
            "central_t0_kept_s": CENTRAL_T0_KEPT_S,
            "alpha": SIGNIFICANCE_ALPHA,
            "min_compared": MIN_COMPARED,
        },
    }
    verdict = write_verdict(out_dir, original)
    detail = {
        "rounds": rounds,
        "p0007_c_quiet_v": p0007_c_reference(),
        "public_diff_vii": public_diff,
    }
    (out_dir / "bm25_add.json").write_text(
        json.dumps(detail, indent=1, ensure_ascii=False), "utf-8"
    )
    (out_dir / "analysis_report.md").write_text(render_report(original, rounds), encoding="utf-8")
    return verdict


def fmt(value: float | None, digits: int = 3) -> str:
    """数値の表示（None は -）．"""
    return "-" if value is None else f"{value:.{digits}f}"


def render_report(original: Mapping[str, Any], rounds: Mapping[str, Any]) -> str:
    """ゲートと判定の表を Markdown にする（解釈は書かない．分析は phase 3）．"""
    lines = [
        "# d0009 A5 bm25_index_add（Iteration 33）の結果",
        "",
        f"有効なラウンド: {original['n_valid_rounds']}（成立: {original['established']}）．",
        "",
        "| ラウンド | G-C | G-P | G-B | G-N | 有効 | 比べた数 | ノード bm25_s p50 | P2P direct p50 | 中央 bm25_s p50 | 中央 T0 p50 | P2P 短 p | T0 短 p |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run, r in rounds.items():
        g, m = r["gates"], r["metrics"]
        lines.append(
            f"| {run} | {g['G-C']} | {g['G-P']} | {g['G-B']} | {g['G-N']} | {r['valid']} | {m['n_compared']} "
            f"| {fmt(m['node_bm25_s']['p50'])} | {fmt(m['p2p_direct_compared']['p50'])} "
            f"| {fmt(m['central_bm25_s_all_flushes']['p50'])} | {fmt(m['central_t0_compared']['p50'])} "
            f"| {m['p2p_shorter']['p']:.3g} | {m['central_t0_shorter']['p']:.3g} |"
        )
    lines += ["", "## 判定", ""]
    judgements = original["judgements"]
    if judgements is None:
        lines.append("有効なラウンドが 2 未満なので測定は成立しない．")
    else:
        lines += [f"- {k}: {v}" for k, v in judgements.items()]
    lines += ["", "詳細（内訳・計算・公開情報の差分）は bm25_add.json．", ""]
    return "\n".join(lines)


def count_public_diff(data_dir: Path, out: Path) -> dict[str, Any]:
    """記述 (vii)：届けるメール（H の正解のメールの 10%）ごとに，公開情報（語彙・n_docs・bm25_len_total）への差分を数える．"""
    from atrium.data_enronqa import read_inbox
    from atrium.device_summary import body_of, filter_terms
    from atrium.freshness_run import FreshnessParams, plan_arrivals
    from atrium.hybrid import MIN_DOC_LENGTH
    from atrium.live_common import load_split_questions
    from atrium.paths import dataset_paths
    from atrium.routing.advert import tokenize
    from atrium.routing.summary import SUMMARY_DIRNAME, load_summary

    paths = dataset_paths(data_dir, "enronqa")
    questions, gold = load_split_questions(paths, "holdout", None)
    _, arrivals = plan_arrivals(questions, gold, FreshnessParams())
    summary = load_summary(paths.root / SUMMARY_DIRNAME)
    if summary.bm25 is None:
        raise ValueError("summary has no bm25_stats.npz")
    published = np.asarray(summary.bm25.published_df)
    mails: dict[str, dict[str, str]] = {}
    for inbox in sorted({a.inbox for a in arrivals}):
        mails.update({d["id"]: d for d in read_inbox(paths, inbox)})
    inbox_sizes = {i: len(read_inbox(paths, i)) for i in sorted({a.inbox for a in arrivals})}
    per_mail: list[dict[str, int]] = []
    for a in arrivals:
        body = body_of(mails[a.doc_id]["content"])
        if not body.strip():
            per_mail.append(
                {
                    "n_docs_delta": 0,
                    "len_delta": 0,
                    "distinct_terms": 0,
                    "in_vocab": 0,
                    "published": 0,
                }
            )
            continue
        tokens = tokenize(body)
        terms = set(filter_terms(tokens))
        in_vocab = [t for t in terms if t in summary.vocab]
        per_mail.append(
            {
                "n_docs_delta": 1,
                "len_delta": max(MIN_DOC_LENGTH, len(tokens)),
                "distinct_terms": len(terms),
                "in_vocab": len(in_vocab),
                "published": sum(1 for t in in_vocab if published[summary.vocab[t]] > 0),
            }
        )
    total_len = summary.bm25.avg_doc_len * summary.bm25.n_docs
    result = {
        "n_arrivals": len(arrivals),
        "n_docs_delta_total": sum(p["n_docs_delta"] for p in per_mail),
        "len_delta_total": sum(p["len_delta"] for p in per_mail),
        "summary_n_docs": summary.bm25.n_docs,
        "summary_len_total": total_len,
        "n_docs_delta_fraction": sum(p["n_docs_delta"] for p in per_mail) / summary.bm25.n_docs,
        "len_delta_fraction": sum(p["len_delta"] for p in per_mail) / total_len
        if total_len
        else None,
        "distinct_terms": percentile_summary([p["distinct_terms"] for p in per_mail]),
        "in_vocab": percentile_summary([p["in_vocab"] for p in per_mail]),
        "published": percentile_summary([p["published"] for p in per_mail]),
        "inbox_sizes": inbox_sizes,
        "note": "要約はこの届くメールを含んで作ってあるので，差分の大きさは全体に対する割合で読む（時間は測らない）",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def main() -> None:
    """サブコマンド verdict・public-diff を実行する．"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verdict")
    v.add_argument("--runs", nargs="+", required=True)
    v.add_argument("--node-check", type=Path)
    v.add_argument("--public-diff", type=Path)
    v.add_argument("--out", type=Path, required=True)
    d = sub.add_parser("public-diff")
    d.add_argument("--data-dir", type=Path, required=True)
    d.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verdict":
        evaluate(args.runs, args.node_check, args.public_diff, args.out)
    else:
        count_public_diff(args.data_dir, args.out)


if __name__ == "__main__":
    main()
