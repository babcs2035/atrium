"""p0003 E2 の leave-one-source-out（LOSO）評価：学習に無かった新参のデータ源を，ルーターが見つけられるか．

`atrium loso` が操作端末で実行する．実機のノードは使わず，制御点から写した manifest・クエリ埋め込み・
関連ラベル・分割・学習済みルーター（`artifacts/<dataset>/`）だけを使う．

除外するデータ源 h ごとに（MedRAG は 4 通り，FeB4RAG は 13 通り），次の方式で N 源から問い合わせ先を選び，
データ源選択の指標と，新参源 h の再現率（h が関連ありの質問のうち，h を選んだ割合）を求める．

| 方式 | 新参源 h の扱い |
|---|---|
| desc_sim | 自己紹介文の MedCPT（文書側）の埋め込みとクエリの内積の上位 k 個．学習しない（MedRAG だけ） |
| centroid_sim | 重心とクエリの内積の上位 k 個．学習しない |
| ragroute-frozen | h を除いた N−1 源で学習した RAGRoute．推論で h の one-hot を 0 にする（MedRAG だけ） |
| ragroute-upper | 全データ源で学習済みのルーター（再学習した場合の上限） |
| all / none / random | E1 と同じ基準 |

k は，h を候補から外し，h の関連ラベルも消した N−1 源の世界で，train と val の質問の F1 が最大のもの
（同点は小さい方）を選ぶ．新参源の情報を k の選択に混ぜないためである．
p0003 は desc_sim・centroid_sim を新しい Router として足し，k を設定のキーに置く案だった．ここでは計算が同じ
既存の CardSimRouter（説明文の埋め込みとの内積）と CentroidSimRouter を使い，k は除外ごとにここで選ぶ
（設定のキーを増やさず，実機の質問者と同じ採点のコードで測るため）．
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import selection_metrics
from atrium.arrays import F32Array
from atrium.config import AtriumConfig, DatasetName
from atrium.manifest import Advert, combine_centroids, read_manifest
from atrium.paths import DatasetPaths
from atrium.routing import (
    AllRouter,
    NoneRouter,
    RandomRouter,
    Router,
    RoutingQuery,
    SourceProfile,
)
from atrium.routing.advert import CardSimRouter, CentroidSimRouter
from atrium.routing.ragroute import ROUTER_WEIGHTS, RagrouteRouter
from atrium.train_router import train_router

logger = logging.getLogger(__name__)

# k の候補（p0003 S3．関連源は MedRAG が平均 1.78 個／4 源，FeB4RAG が平均 9.4 個／13 源）
K_CANDIDATES: dict[DatasetName, tuple[int, ...]] = {
    "medrag": (1, 2, 3),
    "feb4rag": (5, 7, 9, 11, 13),
}
K_SELECTION_SPLITS = ("train", "val")
# desc_sim と ragroute-frozen を評価するデータセット．FeB4RAG は centroid_sim だけ（p0003 S3：自己紹介文の
# 埋め込みには各エンジンの検索器（SGPT 5.8B を含む）が要って重く，FeB4RAG は多源条件の確認用であるため）
FULL_EVALUATION_DATASETS: tuple[DatasetName, ...] = ("medrag",)
# S4 の成功条件（p0003．評価の前に固定した値）．判定は MedRAG だけで行い，FeB4RAG は参考にとどめる
JUDGED_DATASET: DatasetName = "medrag"
CANDIDATE_METHODS = ("desc_sim", "centroid_sim")
MAX_MEAN_SELECTED = 3.0
MIN_RECALL_RATIO_TO_UPPER = 0.8

Describe = Callable[[AtriumConfig, Sequence[SourceProfile]], dict[str, F32Array]]
Row = dict[str, Any]


@dataclass(frozen=True)
class LosoData:
    """LOSO の評価の入力．sources と profiles は，ルーターの one-hot と同じ順序．"""

    sources: list[str]
    profiles: list[SourceProfile]
    queries: dict[str, RoutingQuery]
    labels: dict[str, list[str]]
    split: dict[str, list[str]]


def load_loso_data(
    cfg: AtriumConfig, dataset: DatasetName, paths: DatasetPaths, describe: Describe | None
) -> LosoData:
    """manifest から各データ源の自己紹介を作り，クエリ埋め込み・ラベル・分割を読む．

    describe を与えると，自己紹介文の埋め込みを Advert.card_embedding に入れる（desc_sim が使う）．
    """
    manifest = read_manifest(paths.manifest)
    sources = cfg.data.sources_of(dataset)
    profiles: list[SourceProfile] = []
    for name in sources:
        members = manifest.shards_of(name)
        profiles.append(
            SourceProfile(
                source=name,
                kind=members[0].kind,
                description=members[0].description,
                encoder=members[0].encoder,
                centroid=combine_centroids(
                    [m.n_docs for m in members], [m.centroid for m in members]
                ),
                n_docs=sum(m.n_docs for m in members),
                shard_ids=tuple(sorted(m.shard_id for m in members)),
            )
        )
    if describe is not None:
        embeddings = describe(cfg, profiles)
        profiles = [
            dataclasses.replace(
                p,
                advert=Advert(
                    terms=[],
                    term_weights=[],
                    centroids=[],
                    card_embedding=embeddings[p.source].tolist(),
                ),
            )
            for p in profiles
        ]
    ids: list[str] = json.loads(paths.query_ids.read_text(encoding="utf-8"))
    matrices = {p.encoder: np.load(paths.query_embeddings(p.encoder)) for p in profiles}
    queries = {
        q: RoutingQuery(q, "", {enc: m[i] for enc, m in matrices.items()})
        for i, q in enumerate(ids)
    }
    labels: dict[str, list[str]] = json.loads(paths.labels.read_text(encoding="utf-8"))
    split: dict[str, list[str]] = json.loads(paths.split.read_text(encoding="utf-8"))
    return LosoData(sources, profiles, queries, labels, split)


def embed_descriptions(cfg: AtriumConfig, profiles: Sequence[SourceProfile]) -> dict[str, F32Array]:
    """自己紹介文を MedCPT の文書側のモデルで埋め込む．

    MedCPT はクエリ側と文書側で別のモデルを使い，クエリ埋め込みとの内積で検索する．重心も文書側の平均なので，
    自己紹介文も文書側で埋め込んで同じ空間に置く（p0003 S3）．MedRAG の断片は [title, content] の組で
    埋め込むが，自己紹介文には題が無いので題を空にする．
    """
    from atrium.encoders import MedcptEncoder

    m = cfg.data.medrag
    encoder = MedcptEncoder(m.query_encoder, m.article_encoder, precision="fp32")
    vectors = encoder.encode_docs([("", p.description) for p in profiles])
    return {p.source: vectors[i] for i, p in enumerate(profiles)}


def labeled_qids(data: LosoData, split_names: Sequence[str]) -> list[str]:
    """分割に属し，関連ラベルとクエリ埋め込みがある質問．"""
    return [
        q
        for name in split_names
        for q in data.split[name]
        if q in data.labels and q in data.queries
    ]


def without_source(labels: Mapping[str, Sequence[str]], source: str) -> dict[str, list[str]]:
    """関連ラベルから 1 個のデータ源を消す（N−1 源の世界のラベル）．"""
    return {q: [s for s in relevant if s != source] for q, relevant in labels.items()}


def select_all(
    router: Router, data: LosoData, qids: Sequence[str], profiles: Sequence[SourceProfile]
) -> list[Row]:
    """各質問の問い合わせ先を選び，analysis.selection_metrics が読む形の行にする．"""
    return [{"qid": q, "selected_sources": router.select(data.queries[q], profiles)} for q in qids]


def choose_k(
    make: Callable[[int], Router],
    candidates: Sequence[int],
    data: LosoData,
    holdout: str,
    qids: Sequence[str],
) -> tuple[int, dict[int, float]]:
    """新参源 holdout を候補とラベルから外した N−1 源の世界で，F1 が最大の k を選ぶ（同点は小さい k）．"""
    profiles = [p for p in data.profiles if p.source != holdout]
    labels = without_source(data.labels, holdout)
    f1 = {
        k: selection_metrics(select_all(make(k), data, qids, profiles), labels, len(profiles))["f1"]
        for k in candidates
    }
    best = max(candidates, key=lambda k: (f1[k], -k))
    return best, f1


def frozen_router_dir(
    cfg: AtriumConfig, dataset: DatasetName, paths: DatasetPaths, holdout: str
) -> Path:
    """holdout を除いた N−1 源の RAGRoute を artifacts/<dataset>/loso/<holdout>/ に学習する．

    既にあれば使い回す．学習は train と val の質問だけを使うので，train・val での確認と test の評価で同じものを使える．
    """
    out = paths.root / "loso" / holdout
    if not (out / ROUTER_WEIGHTS).exists():
        report = train_router(cfg, dataset, paths, exclude=[holdout], out_dir=out)
        logger.info("trained router without %s: val auc %s", holdout, report["best_val_auc"])
    return out


def selection_summary(
    rows: Sequence[Row], labels: Mapping[str, list[str]], n_sources: int
) -> dict[str, float]:
    """データ源選択の指標（シャード数は実機の実行にしか無いので除く）．"""
    metrics = selection_metrics(rows, dict(labels), n_sources)
    return {k: v for k, v in metrics.items() if k != "mean_shards_queried"}


def newcomer_hits(
    rows: Sequence[Row], labels: Mapping[str, list[str]], source: str
) -> tuple[int, int]:
    """source が関連ありの質問の数と，そのうち source を選んだ数．"""
    positives = [r for r in rows if source in labels.get(r["qid"], [])]
    hits = sum(source in r["selected_sources"] for r in positives)
    return hits, len(positives)


def summarize_method(
    rows_by_holdout: Mapping[str, list[Row]],
    labels: Mapping[str, list[str]],
    n_sources: int,
    k_by_holdout: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    """除外ごとの指標と，全ての除外を合わせた指標（新参源の再現率は（質問，新参源）の組を合わせて計算）．"""
    by_holdout: dict[str, Any] = {}
    total_hits = total_positives = 0
    for holdout, rows in rows_by_holdout.items():
        hits, positives = newcomer_hits(rows, labels, holdout)
        total_hits += hits
        total_positives += positives
        by_holdout[holdout] = {
            **selection_summary(rows, labels, n_sources),
            "newcomer_recall": hits / positives if positives else 0.0,
            "n_newcomer_pairs": positives,
            **k_by_holdout.get(holdout, {}),
        }
    pooled_rows = [r for rows in rows_by_holdout.values() for r in rows]
    pooled = {
        **selection_summary(pooled_rows, labels, n_sources),
        "newcomer_recall": total_hits / total_positives if total_positives else 0.0,
        "n_newcomer_pairs": total_positives,
    }
    return {"pooled": pooled, "by_holdout": by_holdout}


def judge(methods: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """S4 の成功条件：(a) 平均問い合わせ数 ≤ 3.0，(b) 新参源の再現率 ≥ ragroute-upper の 0.8 倍．

    成功した方式が複数あるときは，新参源の再現率が最大のもの（同点なら平均問い合わせ数が小さい方）を選ぶ．
    """
    upper = float(methods["ragroute-upper"]["pooled"]["newcomer_recall"])
    verdicts: dict[str, dict[str, Any]] = {}
    for name in CANDIDATE_METHODS:
        if name not in methods:
            continue
        pooled = methods[name]["pooled"]
        mean_selected = float(pooled["mean_selected_sources"])
        recall = float(pooled["newcomer_recall"])
        a = mean_selected <= MAX_MEAN_SELECTED
        b = recall >= MIN_RECALL_RATIO_TO_UPPER * upper
        verdicts[name] = {
            "mean_selected_sources": mean_selected,
            "newcomer_recall": recall,
            "recall_ratio_to_upper": recall / upper if upper else 0.0,
            "a_mean_selected_ok": a,
            "b_newcomer_recall_ok": b,
            "success": a and b,
        }
    passed = [n for n, v in verdicts.items() if v["success"]]
    chosen = (
        min(
            passed,
            key=lambda n: (-verdicts[n]["newcomer_recall"], verdicts[n]["mean_selected_sources"]),
        )
        if passed
        else None
    )
    return {
        "max_mean_selected": MAX_MEAN_SELECTED,
        "min_recall_ratio_to_upper": MIN_RECALL_RATIO_TO_UPPER,
        "upper_newcomer_recall": upper,
        "methods": verdicts,
        "chosen": chosen,
    }


def evaluate(
    cfg: AtriumConfig,
    dataset: DatasetName,
    paths: DatasetPaths,
    split_name: str,
    describe: Describe | None = None,
) -> dict[str, Any]:
    """LOSO の評価を行い，metrics.json に書く内容を返す．"""
    data = load_loso_data(cfg, dataset, paths, describe)
    n_sources = len(data.sources)
    eval_qids = labeled_qids(data, (split_name,))
    k_qids = labeled_qids(data, K_SELECTION_SPLITS)
    threshold = cfg.routing.ragroute_threshold

    # 新参源によらない方式は，1 回だけ選ぶ
    fixed: dict[str, Router] = {
        "ragroute-upper": RagrouteRouter(paths.router, data.sources, threshold),
        "all": AllRouter(),
        "none": NoneRouter(),
        "random": RandomRouter(cfg.routing.random_k[dataset], cfg.experiment.seed),
    }
    fixed_rows = {
        name: select_all(router, data, eval_qids, data.profiles) for name, router in fixed.items()
    }

    rows: dict[str, dict[str, list[Row]]] = defaultdict(dict)
    k_info: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    similarity: dict[str, Callable[[int], Router]] = {"centroid_sim": CentroidSimRouter}
    if describe is not None:
        similarity = {"desc_sim": CardSimRouter, **similarity}
    for holdout in data.sources:
        varying: dict[str, Router] = {}
        for name, make in similarity.items():
            k, f1 = choose_k(make, K_CANDIDATES[dataset], data, holdout, k_qids)
            varying[name] = make(k)
            k_info[name][holdout] = {"k": k, "k_selection_f1": {str(c): v for c, v in f1.items()}}
        if dataset in FULL_EVALUATION_DATASETS:
            router_dir = frozen_router_dir(cfg, dataset, paths, holdout)
            varying["ragroute-frozen"] = RagrouteRouter(
                router_dir, data.sources, threshold, allow_unseen=True
            )
        for name, router in varying.items():
            rows[name][holdout] = select_all(router, data, eval_qids, data.profiles)
        for name, selected in fixed_rows.items():
            rows[name][holdout] = selected

    methods = {
        name: summarize_method(by_holdout, data.labels, n_sources, k_info.get(name, {}))
        for name, by_holdout in rows.items()
    }
    return {
        "dataset": dataset,
        "split": split_name,
        "n_questions": len(eval_qids),
        "n_k_selection_questions": len(k_qids),
        "sources": data.sources,
        "methods": methods,
        "criteria": judge(methods) if dataset == JUDGED_DATASET else None,
    }


def top_doc_overlap(run_rows: Sequence[Row], reference_rows: Sequence[Row]) -> dict[str, float]:
    """実行の top_doc_ids を参照の実行（s3．routing=all）と比べる（p0003 S5 の下流確認）．

    両方で成功した質問について，参照の上位文書のうち実行にも現れた割合の平均と，上位文書の列が完全に
    一致した割合を返す．
    """
    reference = {
        r["qid"]: r["top_doc_ids"]
        for r in reference_rows
        if r.get("error") is None and r.get("top_doc_ids")
    }
    pairs = [
        (r["top_doc_ids"], reference[r["qid"]])
        for r in run_rows
        if r.get("error") is None and r["qid"] in reference and r.get("top_doc_ids") is not None
    ]
    if not pairs:
        return {"n": 0, "mean_overlap": 0.0, "exact_match_rate": 0.0}
    overlap = [len(set(run) & set(ref)) / len(ref) for run, ref in pairs]
    return {
        "n": len(pairs),
        "mean_overlap": float(np.mean(overlap)),
        "exact_match_rate": float(np.mean([run == ref for run, ref in pairs])),
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    sources: list[str] = metrics["sources"]
    lines = [
        f"# E2 LOSO（{metrics['dataset']}，split={metrics['split']}，{metrics['n_questions']} 問）",
        "",
        "新参源の再現率の「合計」は，全ての除外の（質問，新参源）の組を合わせた値．"
        "適合率・再現率・F1・平均問い合わせ源は，全ての除外の質問を合わせた値．",
        "",
        "| 方式 | 平均問い合わせ源 | 適合率 | 再現率 | F1 | 新参源の再現率（合計） | "
        + " | ".join(sources)
        + " |",
        "|---" * (6 + len(sources)) + "|",
    ]
    for name, m in metrics["methods"].items():
        p = m["pooled"]
        per = " | ".join(f"{m['by_holdout'][s]['newcomer_recall']:.3f}" for s in sources)
        lines.append(
            f"| {name} | {p['mean_selected_sources']:.2f} | {p['precision']:.3f} | {p['recall']:.3f} "
            f"| {p['f1']:.3f} | {p['newcomer_recall']:.3f} | {per} |"
        )
    lines += ["", "## 選んだ k（N−1 源・train と val の F1 が最大）", ""]
    lines += ["| 方式 | " + " | ".join(sources) + " |", "|---" * (1 + len(sources)) + "|"]
    for name, m in metrics["methods"].items():
        if "k" in next(iter(m["by_holdout"].values())):
            ks = " | ".join(str(m["by_holdout"][s]["k"]) for s in sources)
            lines.append(f"| {name} | {ks} |")
    criteria = metrics["criteria"]
    if criteria is not None:
        lines += [
            "",
            "## S4 の成功条件",
            "",
            f"(a) 平均問い合わせ源 ≤ {criteria['max_mean_selected']}，"
            f"(b) 新参源の再現率 ≥ ragroute-upper（{criteria['upper_newcomer_recall']:.3f}）の "
            f"{criteria['min_recall_ratio_to_upper']} 倍",
            "",
            "| 方式 | 平均問い合わせ源 | 新参源の再現率 | 上限との比 | (a) | (b) | 成功 |",
            "|---|---|---|---|---|---|---|",
        ]
        for name, v in criteria["methods"].items():
            lines.append(
                f"| {name} | {v['mean_selected_sources']:.2f} | {v['newcomer_recall']:.3f} "
                f"| {v['recall_ratio_to_upper']:.3f} | {v['a_mean_selected_ok']} "
                f"| {v['b_newcomer_recall_ok']} | {v['success']} |"
            )
        lines += ["", f"選ばれた方式：{criteria['chosen'] or 'なし'}"]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
