"""実験結果（results.jsonl）の指標の計算とレポートの作成．

指標の定義は docs/d0004_metrics.md に記す．ルーターの学習に使った質問を評価に含めないよう，
全問（all）に加えて split.json の test に限った集計（test）も出す．
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

Z_95 = 1.959963984540054
LATENCY_STAGES = ("embed_s", "probe_s", "route_s", "retrieve_s", "merge_s", "generate_s", "e2e_s")


def read_results(path: Path) -> list[dict[str, Any]]:
    """results.jsonl を読む．同じ qid が複数あれば最後の行を採る（再実行の追記に備える）．"""
    by_qid: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                by_qid[row["qid"]] = row
    return list(by_qid.values())


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """二項比率の Wilson スコア信頼区間を返す．"""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (center - half, center + half)


def mcnemar_exact(b: int, c: int) -> float:
    """McNemar の正確検定（両側）の p 値．b, c は不一致の組の数．"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (1 << n)
    return min(1.0, 2 * tail)


def selection_metrics(
    rows: Sequence[dict[str, Any]], labels: dict[str, list[str]], n_sources: int
) -> dict[str, float]:
    """データ源選択の micro 適合率・再現率・F1 と，問い合わせ数を返す．"""
    tp = fp = fn = 0
    selected_counts: list[int] = []
    label_counts: list[int] = []
    shards_counts: list[int] = []
    for row in rows:
        selected = set(row.get("selected_sources", []))
        relevant = set(labels.get(row["qid"], []))
        tp += len(selected & relevant)
        fp += len(selected - relevant)
        fn += len(relevant - selected)
        selected_counts.append(len(selected))
        label_counts.append(len(relevant))
        shards_counts.append(int(row.get("n_shards_queried", 0)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    mean_selected = float(np.mean(selected_counts)) if selected_counts else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_selected_sources": mean_selected,
        "mean_relevant_sources": float(np.mean(label_counts)) if label_counts else 0.0,
        "mean_shards_queried": float(np.mean(shards_counts)) if shards_counts else 0.0,
        "query_reduction_vs_all": 1 - mean_selected / n_sources if n_sources else 0.0,
    }


def label_consistency(rows: Sequence[dict[str, Any]], labels: dict[str, list[str]]) -> float | None:
    """全データ源に問い合わせた実行で，実機の統合結果が中継点のラベルと一致した割合．

    routing=all かつ merge=score のときに 1.0 に近くなければ，シャードの配布や索引に問題がある．
    """
    checked = [r for r in rows if "contributing_sources" in r and r["qid"] in labels]
    if not checked:
        return None
    same = sum(sorted(r["contributing_sources"]) == sorted(labels[r["qid"]]) for r in checked)
    return same / len(checked)


def accuracy_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """正答率（全体と問題集ごと．Wilson の 95% 信頼区間付き）．"""
    graded = [r for r in rows if "correct" in r]
    if not graded:
        return None
    by_bank: dict[str, list[bool]] = defaultdict(list)
    for r in graded:
        by_bank[r["bank"]].append(bool(r["correct"]))

    def summary(values: list[bool]) -> dict[str, float]:
        lo, hi = wilson_interval(sum(values), len(values))
        return {
            "n": len(values),
            "accuracy": sum(values) / len(values),
            "ci95_low": lo,
            "ci95_high": hi,
        }

    return {
        "overall": summary([bool(r["correct"]) for r in graded]),
        "by_bank": {bank: summary(v) for bank, v in sorted(by_bank.items())},
        "unparsed_choice_rate": sum(r.get("choice") is None for r in graded) / len(graded),
    }


def latency_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """段ごとの所要時間の中央値と 95 パーセンタイル（秒）．"""
    values: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for stage in LATENCY_STAGES:
            if stage in r.get("timings", {}):
                values[stage].append(float(r["timings"][stage]))
    return {
        stage: {"p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95)), "n": len(v)}
        for stage, v in values.items()
    }


def exposure_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, float] | None:
    """露出の指標（p0004 §8.4）．フィールドの無い実行（p0004 より前）では None．

    mean_docs_exposed は持ち主のデバイスの外に出た本文の数，mean_query_recipients はクエリ（文章または埋め込み）を
    受け取ったデバイスの数，mean_answer_overlap_lcs は回答文と正解のメールの最長共通部分列の比率（判定の後に付く）．
    """
    with_fields = [r for r in rows if "query_recipients" in r]
    if not with_fields:
        return None
    out = {
        "mean_docs_exposed": float(
            np.mean([sum(r.get("docs_exposed_by_source", {}).values()) for r in with_fields])
        ),
        "mean_query_recipients": float(np.mean([r["query_recipients"] for r in with_fields])),
    }
    overlap = [r["answer_overlap_lcs"] for r in rows if "answer_overlap_lcs" in r]
    if overlap:
        out["mean_answer_overlap_lcs"] = float(np.mean(overlap))
    return out


def compute_metrics(
    rows: Sequence[dict[str, Any]],
    labels: dict[str, list[str]],
    split: dict[str, list[str]],
    n_sources: int,
    check_label_consistency: bool = False,
) -> dict[str, Any]:
    """全問と test の両方について指標を計算する．

    データ源選択の指標は，関連ラベルのある質問だけで計算する（FeB4RAG には qrels に判定の無い要求がある）．
    ラベル一致は，ラベルを作ったときと同じ条件の実行（MedRAG・routing=all・merge=score）でだけ意味を持つので，
    check_label_consistency が False なら None にする（FeB4RAG のラベルは検索結果から作らないため一致しない）．
    """
    test_ids = set(split.get("test", []))
    subsets = {"all": list(rows), "test": [r for r in rows if r["qid"] in test_ids]}
    out: dict[str, Any] = {}
    for name, subset in subsets.items():
        ok = [r for r in subset if r.get("error") is None]
        labeled = [r for r in ok if r["qid"] in labels]
        out[name] = {
            "n": len(subset),
            "n_unlabeled": len(ok) - len(labeled),
            "failure_rate": 1 - len(ok) / len(subset) if subset else 0.0,
            "selection": selection_metrics(labeled, labels, n_sources),
            "label_consistency": label_consistency(labeled, labels)
            if check_label_consistency
            else None,
            "accuracy": accuracy_metrics(ok),
            "latency": latency_metrics(ok),
            "mean_bytes_received": float(np.mean([r["bytes_received"] for r in ok])) if ok else 0.0,
            "mean_snippets_exposed": float(np.mean([r["snippets_exposed"] for r in ok]))
            if ok
            else 0.0,
            "exposure": exposure_metrics(ok),
        }
    return out


def compare_runs(
    rows_a: Sequence[dict[str, Any]], rows_b: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """同じ質問に対する 2 つの実行の正答を McNemar の正確検定で比べる．"""
    a = {r["qid"]: bool(r["correct"]) for r in rows_a if "correct" in r and r.get("error") is None}
    b = {r["qid"]: bool(r["correct"]) for r in rows_b if "correct" in r and r.get("error") is None}
    shared = sorted(set(a) & set(b))
    only_a = sum(a[q] and not b[q] for q in shared)
    only_b = sum(b[q] and not a[q] for q in shared)
    n = len(shared)
    return {
        "n_shared": n,
        "accuracy_a": sum(a[q] for q in shared) / n if n else 0.0,
        "accuracy_b": sum(b[q] for q in shared) / n if n else 0.0,
        "only_a_correct": only_a,
        "only_b_correct": only_b,
        "mcnemar_p": mcnemar_exact(only_a, only_b),
    }


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render_report(meta: dict[str, Any], metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文を作る．"""
    lines = [
        f"# 実験結果：{meta.get('run_id', '')}",
        "",
        f"- dataset: `{meta.get('dataset')}`，routing: `{meta.get('routing')}`，"
        f"answer_mode: `{meta.get('answer_mode')}`，merge: `{meta.get('merge')}`",
        f"- git: `{meta.get('git_head')}`，質問数: {meta.get('n_questions')}，失敗: {meta.get('failures')}",
        "",
        "| 集計 | n | 失敗率 | 適合率 | 再現率 | F1 | 平均問い合わせ源 | 平均関連源 | 削減率 | ラベル一致 | 正答率 [95%CI] | e2e p50 / p95 (s) | 平均受信 (KB) | 平均露出断片 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, m in metrics.items():
        sel = m["selection"]
        acc = m["accuracy"]
        acc_text = (
            "-"
            if acc is None
            else (
                f"{acc['overall']['accuracy']:.3f} [{acc['overall']['ci95_low']:.3f}, {acc['overall']['ci95_high']:.3f}]"
            )
        )
        e2e = m["latency"].get("e2e_s", {})
        lines.append(
            f"| {name} | {m['n']} | {_fmt(m['failure_rate'])} | {_fmt(sel['precision'])} | "
            f"{_fmt(sel['recall'])} | {_fmt(sel['f1'])} | {_fmt(sel['mean_selected_sources'], 2)} | "
            f"{_fmt(sel['mean_relevant_sources'], 2)} | {_fmt(sel['query_reduction_vs_all'])} | "
            f"{_fmt(m['label_consistency'])} | {acc_text} | {_fmt(e2e.get('p50'), 2)} / "
            f"{_fmt(e2e.get('p95'), 2)} | {m['mean_bytes_received'] / 1024:.1f} | "
            f"{m['mean_snippets_exposed']:.1f} |"
        )
    if metrics["all"].get("exposure") is not None:
        lines += [
            "",
            "## 露出（p0004）",
            "",
            f"事前に公開された情報: {meta.get('advert_bytes', 0)} バイト",
            "",
            "| 集計 | 外に出た本文（件/問） | クエリを受け取ったデバイス（台/問） | 回答と正解メールの LCS 比 |",
            "|---|---|---|---|",
        ]
        for name, m in metrics.items():
            e = m["exposure"]
            if e is None:
                continue
            lines.append(
                f"| {name} | {e['mean_docs_exposed']:.2f} | {e['mean_query_recipients']:.2f} | "
                f"{_fmt(e.get('mean_answer_overlap_lcs'))} |"
            )
    lines += ["", "## 段ごとの待ち時間（all，秒）", "", "| 段 | p50 | p95 |", "|---|---|---|"]
    for stage, v in metrics["all"]["latency"].items():
        lines.append(f"| {stage} | {v['p50']:.3f} | {v['p95']:.3f} |")
    acc = metrics["all"]["accuracy"]
    if acc is not None:
        lines += [
            "",
            "## 問題集ごとの正答率（all）",
            "",
            "| 問題集 | n | 正答率 | 95%CI |",
            "|---|---|---|---|",
        ]
        for bank, v in acc["by_bank"].items():
            lines.append(
                f"| {bank} | {v['n']} | {v['accuracy']:.3f} | [{v['ci95_low']:.3f}, {v['ci95_high']:.3f}] |"
            )
        lines.append(f"\n選択肢を抽出できなかった割合: {acc['unparsed_choice_rate']:.3f}")
    return "\n".join(lines) + "\n"
