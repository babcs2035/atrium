"""p0007 B の露出の評価：2,940 問で，デバイスの外に出た文書の重複なしの数を条件ごとに数え，H-B を機械的に判定する．

`atrium exposure-eval` が制御点のコンテナで実行する（メールの埋め込みは制御点にだけある）．実機のノードは使わない．
既存の `results/` は読み取りのみで，メールの本文は出力に含めない（文書 ID と数だけ）．

- 「外に出た文書」：持ち主のデバイスが，受信箱のメールの本文をそのデバイス以外へ渡した文書．
  B1（snippet_return）は選ばれた各受信箱が返した断片（統合で落ちたものを含む）が質問者へ，
  B4（delegate_gpu）は GPU の無いノードが委託先の GPU へ渡した文脈が出る（GPU のあるノードが自分で答えれば 0）．
- results.jsonl に文書 ID が無い（`docs_exposed_by_source` は数だけ）ので，受信箱ごとの密ベクトルの内積の上位 k 通を
  埋め込みから再計算して ID を復元し，記録との一致（件数・top_doc_ids の包含）で妥当性を確かめる．
- H-B：P2P の条件（`--p2p`）の重複なしの露出割合が，全て THRESHOLD 未満なら成立．
- 出力：exposure.json・verdict.json（verdict_original・verdict_revised）・sim_vs_live.json・analysis_report.md．
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import read_results
from atrium.arrays import F32Array, I64Array
from atrium.central_eval import QUERY_CHUNK, top_k_indices
from atrium.config import AtriumConfig
from atrium.paths import DatasetPaths
from atrium.verdict import make_verdict

logger = logging.getLogger(__name__)

# H-B の閾値（journal の事前登録．全メールに対する重複なしの露出割合）
THRESHOLD = 0.5
# 再構成の妥当性の閾値（記録された top_doc_ids が再構成の集合に含まれる問の割合．同点の順序の揺れを許す）
CONTAINMENT_THRESHOLD = 0.999
# 同点とみなす得点の差（central_eval.tie_aware_agreement と同じ）
TIE_ATOL = 1e-6
# B4 のキーで複数のデータ源（同じノードが持つ受信箱）をつなぐ文字（requester.py の docs_exposed_by_source）
SOURCE_JOIN = "+"
RECEIVER_REQUESTER = "requester"
RECEIVER_GPU = "delegate_gpu"
# 検索した断片を GPU の専門家へ渡して答えさせる answer_mode（owner_answer は p0007 I2．委託の経路は B4 と同じ）
ANSWER_MODES_DELEGATE = frozenset({"delegate_gpu", "owner_answer"})
# 持ち主の特定（top-1・AUC）を読む p0006 の結果の中の場所（hybrid_summary・n30・test・全問）
ATTRIBUTION_PATH = ("hypotheses", "chosen", "n30", "hybrid_summary", "test", "all")


@dataclass(frozen=True)
class InboxTop:
    """受信箱 1 個の，質問ごとの内積の上位 k 通（得点の高い順）．"""

    doc_ids: list[str]
    idx: I64Array
    scores: F32Array
    # 上位に入らなかった文書の得点を引くための参照（同点の確認用．コピーはしない）
    queries: F32Array
    emb: F32Array

    def ids_of(self, qi: int) -> list[str]:
        """質問 qi の上位の文書 ID．"""
        return [self.doc_ids[j] for j in self.idx[qi].tolist()]

    def is_tied_with_cutoff(self, qi: int, doc_id: str) -> bool:
        """文書 doc_id の得点が，質問 qi の上位 k 通の最下位の得点と同点か（重複メールの入れ替えを検索の誤りと区別する）．"""
        score = float(self.emb[self.doc_ids.index(doc_id)] @ self.queries[qi])
        return abs(score - float(self.scores[qi, -1])) <= TIE_ATOL


def inbox_top(queries: F32Array, emb: F32Array, doc_ids: list[str], k: int) -> InboxTop:
    """1 つの受信箱に対する，質問ごとの内積の上位 min(k, 文書数) 通（質問を QUERY_CHUNK ずつ処理する）．"""
    k_eff = min(k, len(doc_ids))
    idx_parts: list[I64Array] = []
    score_parts: list[F32Array] = []
    for i in range(0, len(queries), QUERY_CHUNK):
        scores = queries[i : i + QUERY_CHUNK] @ emb.T
        top = top_k_indices(scores, k_eff)
        idx_parts.append(top)
        score_parts.append(np.take_along_axis(scores, top, axis=1))
    return InboxTop(doc_ids, np.concatenate(idx_parts), np.concatenate(score_parts), queries, emb)


def exposed_ids_of_row(
    exposed_by_source: Mapping[str, int], qi: int, tops: Mapping[str, InboxTop]
) -> list[str]:
    """1 問の `docs_exposed_by_source`（データ源 → 外に出た数）から，外に出た文書の ID を復元する．

    キーが 1 つのデータ源なら，その受信箱の上位 n 通．`+` でつないだ複数のデータ源（同じノードの文脈）なら，
    各受信箱の上位の候補を得点で並べた上位 n 通（ノードの `_merge_top` と同じ）．
    """
    out: list[str] = []
    for key, n in exposed_by_source.items():
        candidates: list[tuple[float, str]] = []
        for source in key.split(SOURCE_JOIN):
            top = tops[source]
            candidates += zip(top.scores[qi].tolist(), top.ids_of(qi), strict=True)
        # 得点の降順．同点は文書 ID の順（ノードの順序は不定なので，同点の入れ替えは包含の確認で許す）
        candidates.sort(key=lambda c: (-c[0], c[1]))
        out += [doc_id for _, doc_id in candidates[:n]]
    return out


def upper_bound_docs(rows: Sequence[Mapping[str, Any]], source_n_docs: Mapping[str, int]) -> int:
    """重複なしの露出の上限：受信箱ごとに min(文書数，外に出た数の合計) を足す（再構成が使えないときの判定用）．"""
    total: dict[str, int] = {}
    for r in rows:
        for key, n in r["docs_exposed_by_source"].items():
            for source in key.split(SOURCE_JOIN):
                total[source] = total.get(source, 0) + n
    return sum(min(source_n_docs[s], n) for s, n in total.items())


def summarize_condition(
    rows: Sequence[Mapping[str, Any]],
    qindex: Mapping[str, int],
    tops: Mapping[str, InboxTop],
    source_n_docs: Mapping[str, int],
) -> dict[str, Any]:
    """1 条件（1 つの実行）の，外に出た文書の重複なしの数・受け手の内訳・再構成の妥当性・1 問あたりの平均．"""
    ok = [r for r in rows if r.get("error") is None and r["qid"] in qindex]
    by_receiver: dict[str, set[str]] = {RECEIVER_REQUESTER: set(), RECEIVER_GPU: set()}
    n_recorded = n_rebuilt = n_count_match = n_with_top = n_contained = n_tie_explained = 0
    recorded_top: set[str] = set()
    delegate_gpus: list[int] = []
    for r in ok:
        exposed = r["docs_exposed_by_source"]
        qi = qindex[r["qid"]]
        ids = exposed_ids_of_row(exposed, qi, tops)
        receiver = RECEIVER_GPU if r["answer_mode"] in ANSWER_MODES_DELEGATE else RECEIVER_REQUESTER
        by_receiver[receiver].update(ids)
        n_recorded += sum(exposed.values())
        n_rebuilt += len(ids)
        n_count_match += len(ids) == sum(exposed.values())
        # B1 だけが統合後の top_doc_ids を持つ．再構成した集合に全て含まれるかを見る（B4 には ID が無い）
        if r["answer_mode"] not in ANSWER_MODES_DELEGATE and r.get("top_doc_ids"):
            n_with_top += 1
            missing = set(r["top_doc_ids"]) - set(ids)
            n_contained += not missing
            # 事後の補助：欠けた文書が全て，その受信箱の上位 k 通の最下位と同点なら，同点の入れ替えとみなす（B56 と同じ見立て）
            n_tie_explained += all(
                tops[d.split("/", 1)[0]].is_tied_with_cutoff(qi, d) for d in missing
            )
            recorded_top.update(r["top_doc_ids"])
        delegate_gpus.append(
            len({a["delegated_to"] for a in r.get("node_answers", []) if a.get("delegated_to")})
        )
    distinct = by_receiver[RECEIVER_REQUESTER] | by_receiver[RECEIVER_GPU]
    n_total = sum(source_n_docs.values())
    n_ok = max(1, len(ok))
    containment = n_contained / n_with_top if n_with_top else None
    return {
        "n_questions": len(ok),
        "n_skipped": len(rows) - len(ok),
        "distinct_docs": len(distinct),
        "fraction": len(distinct) / n_total,
        "distinct_docs_by_receiver": {k: len(v) for k, v in by_receiver.items()},
        "mean_docs_exposed_per_query": n_rebuilt / n_ok,
        # 失敗した問だけの実行でも 0 除算にならないよう，n_ok（最小 1）で割る
        "mean_query_recipients": sum(r.get("query_recipients", 0) for r in ok) / n_ok,
        "mean_probe_recipients": sum(r.get("probe_recipients", 0) for r in ok) / n_ok,
        "mean_shards_queried": sum(r.get("n_shards_queried", 0) for r in ok) / n_ok,
        "mean_delegate_gpus": sum(delegate_gpus) / n_ok,
        "bounds": {
            # 下限：記録された統合後の top_doc_ids だけ（B4 は ID が無いので 0）．上限は受信箱の文書数で抑える
            "lower_docs": len(recorded_top),
            "upper_docs": upper_bound_docs(ok, source_n_docs),
        },
        "validation": {
            "mean_docs_recorded_per_query": n_recorded / n_ok,
            "count_match_rate": n_count_match / n_ok,
            "top_doc_ids_containment": containment,
            # 事後の補助の指標（判定の規則には使わない）：欠けが全て同点の入れ替えで説明できる問まで含めた割合
            "top_doc_ids_containment_tie_aware": n_tie_explained / n_with_top
            if n_with_top
            else None,
            "valid": n_count_match == len(ok)
            and (containment is None or containment >= CONTAINMENT_THRESHOLD),
        },
    }


def judge_hb(
    conditions: Mapping[str, Mapping[str, Any]], p2p: Sequence[str], n_total: int
) -> dict[str, Any]:
    """H-B の機械的な判定：P2P の全ての条件で重複なしの露出割合が THRESHOLD 未満なら supported．

    再構成が妥当でない条件があるときは，下限・上限の割合で判定する（上限も下限も同じ側なら同じ判定，跨げば inconclusive）．
    """
    per = {name: conditions[name] for name in p2p}
    if all(c["validation"]["valid"] for c in per.values()):
        holds = all(c["fraction"] < THRESHOLD for c in per.values())
        verdict = "supported" if holds else "rejected"
        basis = "reconstructed_distinct_docs"
    else:
        upper_all_below = all(c["bounds"]["upper_docs"] / n_total < THRESHOLD for c in per.values())
        lower_any_above = any(
            c["bounds"]["lower_docs"] / n_total >= THRESHOLD for c in per.values()
        )
        verdict = (
            "supported" if upper_all_below else "rejected" if lower_any_above else "inconclusive"
        )
        basis = "bounds"
    return {
        "hypothesis": "H-B: P2P の重複なしの露出が全メールの 50% 未満",
        "threshold": THRESHOLD,
        "p2p_conditions": list(p2p),
        "fractions": {name: c["fraction"] for name, c in per.items()},
        "basis": basis,
        # 判定の根拠が下限・上限でも，再構成した値が同じ側かを併記する（補助．判定には使わない）
        "reconstructed_fractions_all_below_threshold": all(
            c["fraction"] < THRESHOLD for c in per.values()
        ),
        "verdict": verdict,
    }


def read_attribution(metrics_path: Path) -> dict[str, float]:
    """p0006 の結果から，公開情報（hybrid_summary）による持ち主の特定の top-1 と AUC を読む．"""
    node: Any = json.loads(metrics_path.read_text(encoding="utf-8"))
    for key in ATTRIBUTION_PATH:
        node = node[key]
    return {"top1": float(node["attribution_top1"]), "auc": float(node["attribution_auc"])}


def evaluate(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    runs: Mapping[str, Path],
    p2p: Sequence[str],
    expected_per_query: Mapping[str, float],
    attribution_metrics: Path,
) -> dict[str, Any]:
    """条件ごとの露出を数え，H-B の判定・実機の記録との照合をまとめる（出力 4 ファイルの内容）．"""
    from atrium.advert_eval import load_inboxes, load_query_set

    unknown = [name for name in p2p if name not in runs]
    if unknown:
        raise ValueError(f"--p2p names not in --run: {unknown}")
    k = cfg.data.require_enronqa().k_context
    inboxes = load_inboxes(cfg, paths)
    queries = load_query_set(cfg, paths, "test")
    qindex = {q: i for i, q in enumerate(queries.qids)}
    source_n_docs = {ib.name: len(ib.paths) for ib in inboxes}
    n_total = sum(source_n_docs.values())
    tops = {ib.name: inbox_top(queries.emb, ib.emb, ib.paths, k) for ib in inboxes}
    logger.info("inboxes: %d, emails: %d, test questions: %d", len(inboxes), n_total, len(qindex))

    attribution = read_attribution(attribution_metrics)
    conditions: dict[str, dict[str, Any]] = {}
    for name, run in runs.items():
        summary = summarize_condition(
            read_results(run / "results.jsonl"), qindex, tops, source_n_docs
        )
        meta = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
        summary.update(
            {
                "run": run.name,
                "routing": meta["routing"],
                "answer_mode": meta["answer_mode"],
                # 公開情報（要約）の大きさと持ち主の特定率は，要約を配る方式にだけ付く
                "advert_bytes": meta["advert_bytes"],
                "owner_attribution": attribution if meta["advert_bytes"] else None,
            }
        )
        conditions[name] = summary

    verdict = judge_hb(conditions, p2p, n_total)
    sim_vs_live = {
        name: {
            "offline_mean_docs_exposed_per_query": c["mean_docs_exposed_per_query"],
            "live_recorded_mean_docs_exposed_per_query": c["validation"][
                "mean_docs_recorded_per_query"
            ],
            "journal_docs_per_query": expected_per_query.get(name),
            "count_match_rate": c["validation"]["count_match_rate"],
            "top_doc_ids_containment": c["validation"]["top_doc_ids_containment"],
            "top_doc_ids_containment_tie_aware": c["validation"][
                "top_doc_ids_containment_tie_aware"
            ],
            "valid": c["validation"]["valid"],
        }
        for name, c in conditions.items()
    }
    return {
        "exposure": {
            "n_total_docs": n_total,
            "n_test": len(qindex),
            "k_per_inbox": k,
            # 物理的に中央へ集める場合は，最初に全メールが外に出る
            "central": {"distinct_docs": n_total, "fraction": 1.0},
            "conditions": conditions,
        },
        "verdict": make_verdict(verdict),
        "sim_vs_live": {
            "note": "オフラインで再構成した値と，実機の results.jsonl の記録（journal の 1 問あたり外に出た文書）の照合",
            "conditions": sim_vs_live,
        },
    }


def render_report(result: Mapping[str, Any]) -> str:
    """analysis_report.md の本文（文書 ID・本文は含めない）．"""
    exp = result["exposure"]
    verdict = result["verdict"]["verdict_original"]
    lines = [
        "# p0007 B：露出の評価",
        "",
        f"全メール {exp['n_total_docs']:,} 通，test {exp['n_test']:,} 問．「外に出た」は持ち主のデバイス以外（質問者・委託先 GPU）へ本文が渡ったこと．",
        "受信箱ごとの上位 5 通は埋め込みから再構成した（記録は数だけ）．",
        "",
        "| 条件 | 重複なしの文書 | 割合 | 質問者へ | 委託先 GPU へ | 1 問あたり | 質問を受けたデバイス | 公開情報 | 持ち主の特定 top-1・AUC |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| 中央（物理的に集める） | {exp['central']['distinct_docs']:,} | 100.0% | — | — | — | — | — | — |",
    ]
    for name, c in exp["conditions"].items():
        a = c["owner_attribution"]
        attr = f"{a['top1']:.3f}・{a['auc']:.3f}" if a else "—"
        by = c["distinct_docs_by_receiver"]
        lines.append(
            f"| {name} | {c['distinct_docs']:,} | {c['fraction']:.1%} | {by[RECEIVER_REQUESTER]:,} | "
            f"{by[RECEIVER_GPU]:,} | {c['mean_docs_exposed_per_query']:.2f} | "
            f"{c['mean_query_recipients']:.2f} | {c['advert_bytes'] / 1e6:.1f} MB | {attr} |"
        )
    lines += [
        "",
        "## 再構成の妥当性",
        "",
        "| 条件 | 件数の一致 | top_doc_ids の包含 | 同点の入れ替えを許す包含（事後の補助） | 妥当 | 下限 | 上限 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, c in exp["conditions"].items():
        v = c["validation"]
        cont = [
            "—" if v[key] is None else f"{v[key]:.4f}"
            for key in ("top_doc_ids_containment", "top_doc_ids_containment_tie_aware")
        ]
        lines.append(
            f"| {name} | {v['count_match_rate']:.4f} | {cont[0]} | {cont[1]} | {'はい' if v['valid'] else 'いいえ'} | "
            f"{c['bounds']['lower_docs']:,} | {c['bounds']['upper_docs']:,} |"
        )
    fractions = "，".join(f"{n} = {f:.1%}" for n, f in verdict["fractions"].items())
    lines += [
        "",
        "## 判定（verdict_original）",
        "",
        f"H-B（P2P の重複なしの露出が全メールの {verdict['threshold']:.0%} 未満）：**{verdict['verdict']}**（{fractions}．根拠：{verdict['basis']}．再構成した値でも全て閾値未満：{'はい' if verdict['reconstructed_fractions_all_below_threshold'] else 'いいえ'}）．",
        "",
    ]
    return "\n".join(lines)


def write_outputs(out_dir: Path, result: Mapping[str, Any]) -> None:
    """exposure.json・verdict.json・sim_vs_live.json・analysis_report.md を書く（メールの本文は含まない）．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("exposure", "verdict", "sim_vs_live"):
        (out_dir / f"{name}.json").write_text(
            json.dumps(result[name], indent=1, ensure_ascii=False), encoding="utf-8"
        )
    (out_dir / "analysis_report.md").write_text(render_report(result), encoding="utf-8")
