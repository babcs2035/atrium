"""中央集約ベースライン（p0006 P1）のオフラインの評価：全メールを 1 つの索引に集めた場合の検索の精度と，C1 の実機との一致．

`atrium central-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

- 中央の密ベクトルの索引：全 150 受信箱の埋め込みを 1 つに集め，test の質問ごとに内積の上位 K（= 5）通を取る．
- 中央の BM25（C1′）：全メールで IDF と平均長を共有した BM25 の上位 K 通（atrium.advert_eval の flood_bm25_shared_idf と同じ式）．
- C1 との等価性：C1（routing=all・merge=score・snippet_return）の実機の `top_doc_ids` が，中央の密ベクトルの索引の上位 K 通と
  一致する問の割合（集合として．順序も合う割合を併記する）．journal の事前登録の閾値（≥ 0.999）で判定する．
- Recall@K：正解のメールが上位 K 通に入る割合（問の再抽出による 95% ブートストラップ区間）．実機の実行（oracle・flood_score など）
  の `top_doc_ids` からも同じ指標を出し，中央との差を検索の損失として読めるようにする．
- central_local の確認（`atrium central-local-eval`）：routing=central_local が使う `CentralIndex`（質問者の起動時に作る 1 つの索引）の
  test の上位 K 通を C1 と比べる．判定は厳密な集合の一致 ≥ 0.999．満たさず，同点を許す一致 ≥ 0.999 なら，同点を許す判定に自動で切り替えて
  記録する（plans/p0007 D9）．どちらも満たさなければ not_equivalent（止まる条件）．verdict.json を書く．
- 露出：実行ごとの 1 問あたりの，質問を受け取ったデバイスの数・デバイスの外に出た文書の数（results.jsonl の値の平均）．
  中央に集める場合の一度きりの文書の数（全メール）は別に数える．
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from atrium.advert_eval import bootstrap_ci
from atrium.analysis import read_results
from atrium.arrays import F32Array, F64Array, I64Array
from atrium.config import AtriumConfig
from atrium.hybrid import BM25_B, BM25_K1, MIN_DOC_LENGTH
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

# 質問を分けて行列積を取る単位（中央の全メール × 質問の密な行列がメモリに収まるようにする）
QUERY_CHUNK = 256
# 等価性の判定の閾値（journal の事前登録．C1 の実機の文書 ID の一致率）
EQUIVALENCE_THRESHOLD = 0.999
BOOTSTRAP_SEED = 12


def top_k_indices(scores: F32Array, k: int) -> I64Array:
    """各行の上位 k 個の列の番号（得点の高い順．同点は番号の小さい方を先にする）．"""
    part = np.argpartition(-scores, k - 1, axis=1)[:, :k]
    rows = np.arange(scores.shape[0])[:, None]
    order = np.lexsort((part, -scores[rows, part]), axis=1)
    return np.asarray(part[rows, order], dtype=np.int64)


def dense_top_k(queries: F32Array, emb: F32Array, k: int) -> I64Array:
    """1 つの索引（emb の行）に対する内積の上位 k 行（質問を QUERY_CHUNK ずつ処理する）．"""
    out = [
        top_k_indices(queries[i : i + QUERY_CHUNK] @ emb.T, k)
        for i in range(0, len(queries), QUERY_CHUNK)
    ]
    return np.concatenate(out) if out else np.zeros((0, k), np.int64)


def bm25_top_k(
    query_tokens: Sequence[list[str]], doc_tokens: Sequence[list[str]], k: int
) -> I64Array:
    """全文書で IDF と平均長を共有した BM25 の上位 k 文書（質問の語は重複を除く．式は advert_eval と同じ）．"""
    from scipy.sparse import csr_matrix

    vocab: dict[str, int] = {}
    tfs = [Counter(t) for t in doc_tokens]
    for tf in tfs:
        for term in tf:
            vocab.setdefault(term, len(vocab))
    n_docs = len(tfs)
    df = np.zeros(len(vocab), np.float64)
    lengths = np.array([max(MIN_DOC_LENGTH, sum(tf.values())) for tf in tfs], np.float64)
    for tf in tfs:
        df[[vocab[t] for t in tf]] += 1
    idf = np.log(1 + (n_docs - df + 0.5) / (df + 0.5))
    avgdl = float(lengths.mean())
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for d, tf in enumerate(tfs):
        norm = BM25_K1 * (1 - BM25_B + BM25_B * lengths[d] / avgdl)
        for term, c in tf.items():
            j = vocab[term]
            rows.append(d)
            cols.append(j)
            vals.append(idf[j] * c * (BM25_K1 + 1) / (c + norm))
    weights = csr_matrix((vals, (rows, cols)), shape=(n_docs, len(vocab)))
    out: list[I64Array] = []
    for start in range(0, len(query_tokens), QUERY_CHUNK):
        chunk = query_tokens[start : start + QUERY_CHUNK]
        q_rows: list[int] = []
        q_cols: list[int] = []
        for qi, toks in enumerate(chunk):
            for t in set(toks):
                if t in vocab:
                    q_rows.append(vocab[t])
                    q_cols.append(qi)
        q_mat = csr_matrix(
            (np.ones(len(q_rows), np.float32), (q_rows, q_cols)), shape=(len(vocab), len(chunk))
        )
        scores = np.asarray((weights @ q_mat).toarray().T, dtype=np.float32)
        out.append(top_k_indices(scores, k))
    return np.concatenate(out) if out else np.zeros((0, k), np.int64)


def recall_flags(top_ids: Sequence[Sequence[str]], gold_ids: Sequence[str], k: int) -> F64Array:
    """各問で，正解のメールが上位 k 通に入るか（1.0 / 0.0）．"""
    return np.array([float(g in ids[:k]) for ids, g in zip(top_ids, gold_ids, strict=True)])


def summarize_recall(flags: F64Array) -> dict[str, Any]:
    """Recall の平均と 95% ブートストラップ区間．"""
    lo, hi = bootstrap_ci(flags, BOOTSTRAP_SEED)
    return {"value": float(flags.mean()), "ci95": [lo, hi], "n": int(len(flags))}


def agreement_with_run(
    central_ids: dict[str, list[str]], rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """実機の実行の top_doc_ids が中央の索引の上位と一致する問の割合（集合として・順序も合う，の 2 通り）．"""
    compared = [r for r in rows if r.get("error") is None and r["qid"] in central_ids]
    as_set = sum(set(r["top_doc_ids"]) == set(central_ids[r["qid"]]) for r in compared)
    ordered = sum(list(r["top_doc_ids"]) == central_ids[r["qid"]] for r in compared)
    n = max(1, len(compared))
    return {
        "n_compared": len(compared),
        "n_failed_or_missing": len(central_ids) - len(compared),
        "set_agreement": as_set / n,
        "ordered_agreement": ordered / n,
    }


def tie_aware_agreement(
    scores_of: dict[str, F32Array], index_of: dict[str, int], rows: Sequence[dict[str, Any]], k: int
) -> dict[str, Any]:
    """C1 の top_doc_ids が，同点を許して 1 索引の上位 k 通と等価な問の割合（backlog B56 の A1．事前登録の厳密な一致は別に残す）．

    等価とは，C1 の k 通の得点の並びが 1 索引の上位 k 通の得点の並びと一致し，かつ全てが k 位の得点以上であること．
    重複するメール（同じ得点）のどちらを採るかだけが違う場合を，検索の誤りと区別する．
    """
    compared = [r for r in rows if r.get("error") is None and r["qid"] in scores_of]
    ok = 0
    for r in compared:
        s = scores_of[r["qid"]]
        got = np.sort(s[[index_of[d] for d in r["top_doc_ids"]]])[::-1]
        want = np.sort(s)[::-1][:k]
        ok += len(got) == k and bool(np.allclose(got, want, rtol=0, atol=1e-6))
    return {"n_compared": len(compared), "tie_aware_agreement": ok / max(1, len(compared))}


def run_summary(rows: Sequence[dict[str, Any]], gold_of: dict[str, str], k: int) -> dict[str, Any]:
    """実行（results.jsonl）の Recall@k と，1 問あたりの露出（質問を受け取ったデバイスの数・外に出た文書の数）．"""
    ok = [r for r in rows if r.get("error") is None and r["qid"] in gold_of]
    flags = recall_flags([r["top_doc_ids"] for r in ok], [gold_of[r["qid"]] for r in ok], k)
    return {
        "recall": summarize_recall(flags),
        "mean_query_recipients": float(np.mean([r.get("query_recipients", 0) for r in ok])),
        "mean_docs_exposed_per_query": float(np.mean([r.get("snippets_exposed", 0) for r in ok])),
        "mean_shards_queried": float(np.mean([r.get("n_shards_queried", 0) for r in ok])),
    }


def evaluate(
    cfg: AtriumConfig, paths: DatasetPaths, c1_run: Path, compare_runs: dict[str, Path]
) -> dict[str, Any]:
    """中央の密ベクトル・BM25 の Recall@K を計算し，C1 の実機の文書 ID との一致と，各実行の Recall・露出を求める．"""
    from atrium.advert_eval import load_inboxes, load_query_set
    from atrium.data_enronqa import read_gold
    from atrium.routing.advert import tokenize

    k = cfg.data.require_enronqa().k_context
    inboxes = load_inboxes(cfg, paths)
    doc_ids = [p for ib in inboxes for p in ib.paths]
    emb = np.concatenate([ib.emb for ib in inboxes])
    texts = [t for ib in inboxes for t in ib.texts]
    queries = load_query_set(cfg, paths, "test")
    gold = read_gold(paths)
    gold_of = {q: gold[q]["path"] for q in queries.qids}
    logger.info("central index: %d emails, %d test questions", len(doc_ids), len(queries.qids))

    dense = dense_top_k(queries.emb, emb, k)
    central_ids = {
        q: [doc_ids[i] for i in row] for q, row in zip(queries.qids, dense.tolist(), strict=True)
    }
    bm25 = bm25_top_k([tokenize(x) for x in queries.texts], [tokenize(t) for t in texts], k)
    bm25_ids = [[doc_ids[i] for i in row] for row in bm25.tolist()]
    gold_ids = [gold_of[q] for q in queries.qids]

    c1_rows = read_results(c1_run / "results.jsonl")
    agreement = agreement_with_run(central_ids, c1_rows)
    index_of = {d: i for i, d in enumerate(doc_ids)}
    scores_of = {q: emb @ queries.emb[i] for i, q in enumerate(queries.qids)}
    tie_aware = tie_aware_agreement(scores_of, index_of, c1_rows, k)
    return {
        "n_emails": len(doc_ids),
        "n_test": len(queries.qids),
        "k": k,
        "central_dense": {
            f"recall@{j}": summarize_recall(
                recall_flags([central_ids[q] for q in queries.qids], gold_ids, j)
            )
            for j in (1, k)
        },
        "central_bm25": {
            f"recall@{j}": summarize_recall(recall_flags(bm25_ids, gold_ids, j)) for j in (1, k)
        },
        "c1_equivalence": {
            **agreement,
            **tie_aware,
            "threshold": EQUIVALENCE_THRESHOLD,
            # 事前登録の判定（厳密な集合の一致）．同点を許す一致は B56 の A1 で足した補助の指標
            "equivalent": agreement["set_agreement"] >= EQUIVALENCE_THRESHOLD,
            "equivalent_tie_aware": tie_aware["tie_aware_agreement"] >= EQUIVALENCE_THRESHOLD,
        },
        "runs": {
            name: run_summary(read_results(path / "results.jsonl"), gold_of, k)
            for name, path in {"c1": c1_run, **compare_runs}.items()
        },
        # 中央に集めるときに，デバイスの外へ一度だけ出るメールの数（全メール）
        "central_one_time_docs": len(doc_ids),
    }


def judge_equivalence(set_agreement: float, tie_aware_agreement_value: float) -> dict[str, Any]:
    """central_local と C1 の一致の判定（verdict_original の中身）．

    厳密な集合の一致が閾値以上なら equivalent．満たさず，同点を許す一致が閾値以上なら，同点を許す判定に自動で切り替えて
    equivalent_tie_aware（plans/p0007 D9．重複メールの入れ替えだけが原因の場合）．どちらも満たさなければ not_equivalent（止まる条件）．
    """
    strict_ok = set_agreement >= EQUIVALENCE_THRESHOLD
    tie_ok = tie_aware_agreement_value >= EQUIVALENCE_THRESHOLD
    return {
        "hypothesis": "central_local の上位 K 通の文書 ID が C1 と一致する（集合．閾値 0.999）",
        "threshold": EQUIVALENCE_THRESHOLD,
        "set_agreement": set_agreement,
        "tie_aware_agreement": tie_aware_agreement_value,
        "switched_to_tie_aware": (not strict_ok) and tie_ok,
        "verdict": "equivalent"
        if strict_ok
        else "equivalent_tie_aware"
        if tie_ok
        else "not_equivalent",
    }


def evaluate_central_local(cfg: AtriumConfig, paths: DatasetPaths, c1_run: Path) -> dict[str, Any]:
    """routing=central_local の索引（`CentralIndex`）の上位 K 通を，C1 の実機の top_doc_ids と比べて判定する（p0007 I4・D9）."""
    from atrium.advert_eval import load_query_set
    from atrium.routing.central import CentralIndex
    from atrium.verdict import make_verdict

    k = cfg.data.require_enronqa().k_context
    index = CentralIndex.from_corpus(cfg, paths)
    queries = load_query_set(cfg, paths, "test")
    central_ids = {
        q: [d.doc_id for d in index.search(queries.emb[i], k)] for i, q in enumerate(queries.qids)
    }
    c1_rows = read_results(c1_run / "results.jsonl")
    agreement = agreement_with_run(central_ids, c1_rows)
    index_of = {d: i for i, d in enumerate(index.doc_ids)}
    scores_of = {q: index.emb @ queries.emb[i] for i, q in enumerate(queries.qids)}
    tie_aware = tie_aware_agreement(scores_of, index_of, c1_rows, k)
    verdict = judge_equivalence(agreement["set_agreement"], tie_aware["tie_aware_agreement"])
    return {
        "n_emails": len(index.doc_ids),
        "n_test": len(queries.qids),
        "k": k,
        "c1_agreement": {**agreement, **tie_aware},
        "index_load": asdict(index.load),
        "verdict": make_verdict(verdict),
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    k = metrics["k"]

    def cell(r: dict[str, Any]) -> str:
        return f"{r['value']:.4f} [{r['ci95'][0]:.4f}, {r['ci95'][1]:.4f}]"

    eq = metrics["c1_equivalence"]
    lines = [
        "# 中央集約ベースラインのオフラインの評価（p0006 P1）",
        "",
        f"メール {metrics['n_emails']:,} 通を 1 つの索引に集めた場合．test {metrics['n_test']:,} 問，K = {k}．",
        "",
        "## 中央の検索の Recall",
        "",
        "| 方式 | Recall@1 | Recall@K |",
        "|---|---|---|",
        f"| 中央・密ベクトル | {cell(metrics['central_dense']['recall@1'])} | {cell(metrics['central_dense'][f'recall@{k}'])} |",
        f"| 中央・BM25（全体で統計を共有） | {cell(metrics['central_bm25']['recall@1'])} | {cell(metrics['central_bm25'][f'recall@{k}'])} |",
        "",
        "## C1 の実機と中央の密ベクトルの索引の一致",
        "",
        "| 比べた問 | 集合の一致 | 順序も一致 | 同点を許す一致 | 欠け・失敗 | 閾値 | 判定（厳密） | 判定（同点を許す） |",
        "|---|---|---|---|---|---|---|---|",
        f"| {eq['n_compared']} | {eq['set_agreement']:.4f} | {eq['ordered_agreement']:.4f} | {eq['tie_aware_agreement']:.4f} | {eq['n_failed_or_missing']} | {eq['threshold']} | {'等価' if eq['equivalent'] else '不一致'} | {'等価' if eq['equivalent_tie_aware'] else '不一致'} |",
        "",
        "## 実行ごとの Recall と 1 問あたりの露出",
        "",
        f"| 実行 | Recall@{k} | 質問を受け取ったデバイス | 外に出た文書 | 問い合わせたシャード |",
        "|---|---|---|---|---|",
    ]
    for name, r in metrics["runs"].items():
        lines.append(
            f"| {name} | {cell(r['recall'])} | {r['mean_query_recipients']:.2f} | "
            f"{r['mean_docs_exposed_per_query']:.2f} | {r['mean_shards_queried']:.2f} |"
        )
    lines += [
        "",
        f"中央に集める場合に，デバイスの外へ一度だけ出る文書の数は {metrics['central_one_time_docs']:,} 通（全メール）．",
        "",
    ]
    return "\n".join(lines)


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く（メールの本文は含まない）．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")


def write_central_local_outputs(out_dir: Path, result: dict[str, Any]) -> str:
    """central_local_check.json・verdict.json・analysis_report.md を書き，報告の本文を返す（メールの本文は含まない）．"""
    from atrium.verdict import write_verdict

    out_dir.mkdir(parents=True, exist_ok=True)
    body = {key: value for key, value in result.items() if key != "verdict"}
    (out_dir / "central_local_check.json").write_text(
        json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    write_verdict(out_dir, result["verdict"]["verdict_original"])
    verdict = result["verdict"]["verdict_original"]
    agreement = result["c1_agreement"]
    load = result["index_load"]
    report = "\n".join(
        [
            "# central_local の索引と C1 の一致（p0007 I4）",
            "",
            f"メール {result['n_emails']:,} 通の 1 つの索引．test {result['n_test']:,} 問，K = {result['k']}．",
            "",
            "| 比べた問 | 集合の一致（厳密） | 順序も一致 | 同点を許す一致 | 閾値 | 判定 |",
            "|---|---|---|---|---|---|",
            f"| {agreement['n_compared']} | {agreement['set_agreement']:.4f} | {agreement['ordered_agreement']:.4f} | "
            f"{agreement['tie_aware_agreement']:.4f} | {verdict['threshold']} | {verdict['verdict']}"
            f"{'（D9：同点を許す判定に切り替え）' if verdict['switched_to_tie_aware'] else ''} |",
            "",
            f"索引を作る費用：{load['total_s']:.1f} 秒（文書の読み込み {load['read_docs_s']:.1f} 秒・埋め込みの読み込み "
            f"{load['read_emb_s']:.1f} 秒．{load['n_docs']:,} 通・{load['n_inboxes']} 受信箱）．",
            "",
        ]
    )
    (out_dir / "analysis_report.md").write_text(report, encoding="utf-8")
    return report
