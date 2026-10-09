"""RQ-A のオフラインの評価（p0004 EQ2）：公開情報の量と，見つけてもらえる度合い・所有者の特定・質問の露出の兼ね合い．

`atrium advert-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（値の選び方は journal の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. 各方式の公開情報の大きさ（card の件名の数・語のスケッチの語数 T・中心の数 C）の候補ごとに，dev の質問で
   正解の受信箱の順位の逆数の平均（MRR）を求め，最大のものを選ぶ．
2. 問い合わせる数 m は，dev で最良の方式の hit@m が 0.9 以上になる最小の m（候補 M_CANDIDATES．届かなければ最大）．
3. 選んだ値で，test の質問の hit@1・hit@3・hit@m（ブートストラップの 95% 信頼区間付き），質問を受け取る受信箱の数，
   公開情報の大きさを求める．
4. 所有者の特定：各受信箱から test の正解のメール以外を最大 owner_probe_per_inbox 通抜き出し，公開情報だけから
   持ち主の受信箱を当てる（top-1 の正解率と AUC）．flood_score と ragroute は受信箱ごとの公開情報が無いので偶然の水準とする．

BM25 の flood_score は H-A3 の確認用である．受信箱ごとの統計（局所の IDF）と，全受信箱で共有した統計（大域の IDF）の 2 通りを比べる．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from atrium.arrays import F32Array, F64Array, I64Array
from atrium.benchmarks import load_questions
from atrium.config import AtriumConfig
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

CARD_CANDIDATES = (5, 10, 20, 40)
TERM_CANDIDATES = (10, 20, 50, 100, 200)
CENTROID_CANDIDATES = (2, 4, 8, 16, 32)
M_CANDIDATES = (1, 2, 3, 5, 10, 20)
DEV_HIT_TARGET = 0.9
BOOTSTRAP_SAMPLES = 1000
BM25_K1 = 1.2
BM25_B = 0.75
# BM25 の文書の長さを数えない極端に短いメールのための下限（0 除算を避ける）
MIN_DOC_LENGTH = 1


@dataclass(frozen=True)
class Inbox:
    """受信箱 1 個の評価用のデータ（本文は語の集合と語の列だけを持つ）．"""

    name: str
    paths: list[str]
    titles: list[str]
    texts: list[str]
    emb: F32Array


def load_inboxes(cfg: AtriumConfig, paths: DatasetPaths) -> list[Inbox]:
    """全受信箱の chunk と埋め込みを読む（data.enronqa.sources の順）．"""
    from atrium.data_enronqa import emb_path, read_inbox

    out = []
    for name in cfg.data.require_enronqa().sources:
        docs = read_inbox(paths, name)
        out.append(
            Inbox(
                name=name,
                paths=[d["id"] for d in docs],
                titles=[d["title"] for d in docs],
                texts=[d["content"] for d in docs],
                emb=np.load(emb_path(paths, name)).astype(np.float32),
            )
        )
    return out


def ranks_of_gold(scores: F32Array, gold: Sequence[int]) -> I64Array:
    """各質問の正解の受信箱の順位（1 始まり．同点は不利な側に数える）．"""
    gold_scores = scores[np.arange(len(gold)), list(gold)]
    return np.asarray((scores >= gold_scores[:, None]).sum(axis=1), dtype=np.int64)


def hit_at(ranks: I64Array, m: int) -> float:
    """正解の受信箱が上位 m 個に入る割合．"""
    return float(np.mean(ranks <= m))


def bootstrap_ci(values: F64Array, seed: int) -> tuple[float, float]:
    """平均の 95% ブートストラップ信頼区間．"""
    rng = np.random.default_rng(seed)
    means = [float(np.mean(rng.choice(values, size=len(values)))) for _ in range(BOOTSTRAP_SAMPLES)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """スピアマンの順位相関（同順位は平均順位）．どちらかが一定なら 0．"""
    from scipy.stats import spearmanr

    if len(set(x)) < 2 or len(set(y)) < 2:
        return 0.0
    return float(spearmanr(x, y).statistic)


def owner_auc(scores: F32Array, owner: Sequence[int]) -> float:
    """各抜き出しメールで，他の受信箱のうち持ち主より低く採点されたものの割合（同点は 0.5）の平均．"""
    true = scores[np.arange(len(owner)), list(owner)][:, None]
    n_other = scores.shape[1] - 1
    lower = (scores < true).sum(axis=1)
    ties = (scores == true).sum(axis=1) - 1
    return float(np.mean((lower + 0.5 * ties) / n_other))


# ── 各方式の採点（行：質問または抜き出したメール，列：受信箱）─────────────────


def dense_matrix(vectors: Sequence[F32Array]) -> F32Array:
    """受信箱ごとのベクトル（重心・説明文）を行に並べる．"""
    return np.stack([np.asarray(v, np.float32) for v in vectors])


def score_single(queries: F32Array, per_inbox: F32Array) -> F32Array:
    """内積（centroid_sim・card_sim）．"""
    out: F32Array = queries @ per_inbox.T
    return out


def score_multi(queries: F32Array, centers: Sequence[F32Array]) -> F32Array:
    """中心との内積の最大（multi_centroid）．"""
    return np.stack([(queries @ c.T).max(axis=1) for c in centers], axis=1)


def score_terms(
    token_sets: Sequence[set[str]], sketches: Sequence[tuple[list[str], list[float]]]
) -> F32Array:
    """スケッチの語のうちクエリに含まれるものの重みの和（term_sketch）．"""
    out = np.zeros((len(token_sets), len(sketches)), np.float32)
    for j, (terms, weights) in enumerate(sketches):
        weight = dict(zip(terms, weights, strict=True))
        for i, ts in enumerate(token_sets):
            out[i, j] = sum(weight[t] for t in ts if t in weight)
    return out


def score_flood_dense(queries: F32Array, inboxes: Sequence[Inbox]) -> F32Array:
    """各受信箱のメールとの内積の最大（flood_score．実機の /v1/probe と同じ）．"""
    return np.stack([(queries @ ib.emb.T).max(axis=1) for ib in inboxes], axis=1)


def score_flood_bm25(
    query_tokens: Sequence[list[str]], inboxes: Sequence[Inbox], shared_statistics: bool
) -> F32Array:
    """各受信箱のメールとの BM25 の最大（H-A3）．shared_statistics なら全受信箱で IDF と平均長を共有する．"""
    from collections import Counter

    from scipy.sparse import csr_matrix

    from atrium.routing.advert import tokenize

    vocab: dict[str, int] = {}
    tfs: list[list[Counter[str]]] = [[Counter(tokenize(t)) for t in ib.texts] for ib in inboxes]
    for inbox_tfs in tfs:
        for tf in inbox_tfs:
            for term in tf:
                vocab.setdefault(term, len(vocab))
    n_all = sum(len(x) for x in tfs)
    df_all = np.zeros(len(vocab), np.float64)
    len_all = []
    for inbox_tfs in tfs:
        for tf in inbox_tfs:
            df_all[[vocab[t] for t in tf]] += 1
            len_all.append(max(MIN_DOC_LENGTH, sum(tf.values())))
    q_rows, q_cols = [], []
    for qi, toks in enumerate(query_tokens):
        for t in set(toks):
            if t in vocab:
                q_rows.append(vocab[t])
                q_cols.append(qi)
    q_mat = csr_matrix(
        (np.ones(len(q_rows), np.float32), (q_rows, q_cols)), shape=(len(vocab), len(query_tokens))
    )
    out = np.zeros((len(query_tokens), len(inboxes)), np.float32)
    for j, inbox_tfs in enumerate(tfs):
        lengths = np.array([max(MIN_DOC_LENGTH, sum(tf.values())) for tf in inbox_tfs], np.float64)
        if shared_statistics:
            n, df, avgdl = n_all, df_all, float(np.mean(len_all))
        else:
            n = len(inbox_tfs)
            df = np.zeros(len(vocab), np.float64)
            for tf in inbox_tfs:
                df[[vocab[t] for t in tf]] += 1
            avgdl = float(np.mean(lengths))
        idf = np.log(1 + (n - df + 0.5) / (df + 0.5))
        rows, cols, vals = [], [], []
        for d, tf in enumerate(inbox_tfs):
            norm = BM25_K1 * (1 - BM25_B + BM25_B * lengths[d] / avgdl)
            for term, c in tf.items():
                k = vocab[term]
                rows.append(d)
                cols.append(k)
                vals.append(idf[k] * c * (BM25_K1 + 1) / (c + norm))
        w = csr_matrix((vals, (rows, cols)), shape=(len(inbox_tfs), len(vocab)))
        out[:, j] = (w @ q_mat).max(axis=0).toarray().ravel()
    return out


# ── 評価の本体 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class QuerySet:
    """評価に使う質問（dev または test）．"""

    qids: list[str]
    texts: list[str]
    emb: F32Array
    gold: list[int]


def load_query_set(cfg: AtriumConfig, paths: DatasetPaths, split: str) -> QuerySet:
    """dev・test の質問と，そのクエリ埋め込みと，正解の受信箱の番号を読む．"""
    from atrium.data_enronqa import QUESTION_FILES

    e = cfg.data.require_enronqa()
    questions = load_questions(paths.questions.parent / QUESTION_FILES[split])
    ids: list[str] = json.loads(paths.query_ids.read_text(encoding="utf-8"))
    row = {q: i for i, q in enumerate(ids)}
    matrix = np.load(paths.query_embeddings(e.encoder), mmap_mode="r")
    index = {name: i for i, name in enumerate(e.sources)}
    return QuerySet(
        qids=[q.qid for q in questions],
        texts=[q.question for q in questions],
        emb=np.asarray(matrix[[row[q.qid] for q in questions]], np.float32),
        gold=[index[q.bank] for q in questions],
    )


@dataclass(frozen=True)
class Method:
    """1 個の方式：公開情報の大きさの候補ごとに，（質問の採点，公開情報のバイト数，所有者の特定の採点）を返す．"""

    name: str
    candidates: tuple[int, ...]
    run: Callable[
        [int, F32Array, list[str], F32Array, list[str]], tuple[F32Array, int, F32Array | None]
    ]


def build_methods(cfg: AtriumConfig, inboxes: Sequence[Inbox], paths: DatasetPaths) -> list[Method]:
    """評価する方式の一覧（公開情報は持ち主のメールだけから計算する．atrium.data_enronqa と同じ関数）．"""
    from atrium.data_enronqa import card_text, kmeans_centroids, term_sketch
    from atrium.encoders import ArcticEncoder
    from atrium.routing.advert import tokenize

    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    encoder = ArcticEncoder(e.encoder, e.embed_batch_size)

    def card(
        n: int, q: F32Array, _qt: list[str], probe: F32Array, _pt: list[str]
    ) -> tuple[F32Array, int, F32Array | None]:
        texts = [card_text(ib.titles, n) for ib in inboxes]
        cards = encoder.encode_texts(texts)
        size = sum(len(t.encode()) for t in texts)
        return score_single(q, cards), size, score_single(probe, cards)

    def terms(
        n: int, _q: F32Array, qt: list[str], _p: F32Array, pt: list[str]
    ) -> tuple[F32Array, int, F32Array | None]:
        sketches = [term_sketch(ib.texts, n) for ib in inboxes]
        size = sum(len(json.dumps({"terms": t, "term_weights": w}).encode()) for t, w in sketches)
        q_sets = [set(tokenize(x)) for x in qt]
        p_sets = [set(tokenize(x)) for x in pt]
        return score_terms(q_sets, sketches), size, score_terms(p_sets, sketches)

    def centroid(
        _n: int, q: F32Array, _qt: list[str], probe: F32Array, _pt: list[str]
    ) -> tuple[F32Array, int, F32Array | None]:
        means = dense_matrix([ib.emb.mean(axis=0) for ib in inboxes])
        size = sum(len(str(m.tolist()).encode()) for m in means)
        return score_single(q, means), size, score_single(probe, means)

    def multi(
        n: int, q: F32Array, _qt: list[str], probe: F32Array, _pt: list[str]
    ) -> tuple[F32Array, int, F32Array | None]:
        centers = [kmeans_centroids(ib.emb, n, seed) for ib in inboxes]
        size = sum(len(json.dumps({"centroids": c.tolist()}).encode()) for c in centers)
        return score_multi(q, centers), size, score_multi(probe, centers)

    def flood(
        _n: int, q: F32Array, _qt: list[str], _p: F32Array, _pt: list[str]
    ) -> tuple[F32Array, int, F32Array | None]:
        return score_flood_dense(q, inboxes), 0, None

    def flood_bm25_local(
        _n: int, _q: F32Array, qt: list[str], _p: F32Array, _pt: list[str]
    ) -> tuple[F32Array, int, F32Array | None]:
        return score_flood_bm25([tokenize(x) for x in qt], inboxes, False), 0, None

    def flood_bm25_shared(
        _n: int, _q: F32Array, qt: list[str], _p: F32Array, _pt: list[str]
    ) -> tuple[F32Array, int, F32Array | None]:
        return score_flood_bm25([tokenize(x) for x in qt], inboxes, True), 0, None

    methods = [
        Method("card_sim", CARD_CANDIDATES, card),
        Method("term_sketch", TERM_CANDIDATES, terms),
        Method("centroid_sim", (0,), centroid),
        Method("multi_centroid", CENTROID_CANDIDATES, multi),
        Method("flood_score", (0,), flood),
        Method("flood_bm25_local_idf", (0,), flood_bm25_local),
        Method("flood_bm25_shared_idf", (0,), flood_bm25_shared),
    ]
    if (paths.router / "router.pt").exists():
        methods.append(Method("ragroute", (0,), ragroute_method(cfg, inboxes, paths)))
    return methods


def ragroute_method(
    cfg: AtriumConfig, inboxes: Sequence[Inbox], paths: DatasetPaths
) -> Callable[
    [int, F32Array, list[str], F32Array, list[str]], tuple[F32Array, int, F32Array | None]
]:
    """中央で学習した MLP（全 150 受信箱の train の質問で学習した上限．公開情報は中央に集めた学習データ）．"""
    from atrium.routing import RoutingQuery, SourceProfile
    from atrium.routing.ragroute import RagrouteRouter

    e = cfg.data.require_enronqa()
    router = RagrouteRouter(paths.router, e.sources, cfg.routing.ragroute_threshold)
    profiles = [
        SourceProfile(
            ib.name, "faiss", "", e.encoder, ib.emb.mean(axis=0), len(ib.texts), (ib.name,)
        )
        for ib in inboxes
    ]

    def run(
        _n: int, q: F32Array, _qt: list[str], _p: F32Array, _pt: list[str]
    ) -> tuple[F32Array, int, F32Array | None]:
        out = np.stack(
            [
                router.probabilities(RoutingQuery(str(i), "", {e.encoder: v}), profiles)
                for i, v in enumerate(q)
            ]
        )
        return out.astype(np.float32), 0, None

    return run


def owner_probes(
    cfg: AtriumConfig, inboxes: Sequence[Inbox], gold_paths: set[str]
) -> tuple[F32Array, list[str], list[int]]:
    """所有者の特定に使うメール（各受信箱から，test の正解のメール以外を最大 N 通）の埋め込み・本文・持ち主．"""
    e = cfg.data.require_enronqa()
    rng = np.random.default_rng(cfg.experiment.seed)
    embs: list[F32Array] = []
    texts: list[str] = []
    owners: list[int] = []
    for j, ib in enumerate(inboxes):
        pool = [i for i, p in enumerate(ib.paths) if p not in gold_paths]
        for i in sorted(rng.permutation(len(pool))[: min(e.owner_probe_per_inbox, len(pool))]):
            embs.append(ib.emb[pool[int(i)]])
            texts.append(ib.texts[pool[int(i)]])
            owners.append(j)
    return np.stack(embs), texts, owners


def evaluate(cfg: AtriumConfig, paths: DatasetPaths) -> dict[str, Any]:
    """dev で値を選び，test で評価する（手順は冒頭の説明のとおり）．"""
    from atrium.data_enronqa import read_gold

    start = time.perf_counter()
    inboxes = load_inboxes(cfg, paths)
    n = len(inboxes)
    dev = load_query_set(cfg, paths, "dev")
    test = load_query_set(cfg, paths, "test")
    gold = read_gold(paths)
    test_gold_paths = {gold[q]["path"] for q in test.qids}
    probe_emb, probe_texts, probe_owner = owner_probes(cfg, inboxes, test_gold_paths)
    seed = cfg.experiment.seed

    # 1. dev で公開情報の大きさを選ぶ（MRR が最大のもの）
    methods = build_methods(cfg, inboxes, paths)
    selection: dict[str, dict[str, Any]] = {}
    dev_ranks: dict[str, I64Array] = {}
    for method in methods:
        best: tuple[float, int, I64Array] | None = None
        per_candidate = {}
        for c in method.candidates:
            scores, _, _ = method.run(c, dev.emb, dev.texts, probe_emb[:1], probe_texts[:1])
            ranks = ranks_of_gold(scores, dev.gold)
            mrr = float(np.mean(1.0 / ranks))
            per_candidate[str(c)] = mrr
            if best is None or mrr > best[0] + 1e-12:
                best = (mrr, c, ranks)
        assert best is not None
        selection[method.name] = {
            "param": best[1],
            "dev_mrr": best[0],
            "dev_mrr_by_param": per_candidate,
        }
        dev_ranks[method.name] = best[2]
        logger.info("dev %s: param %s, MRR %.4f", method.name, best[1], best[0])

    # 2. m：dev で最良の方式（ragroute と flood を含む非オラクルの全方式）の hit@m が目標に届く最小の m
    best_method = max(selection, key=lambda k: selection[k]["dev_mrr"])
    m = next(
        (c for c in M_CANDIDATES if hit_at(dev_ranks[best_method], c) >= DEV_HIT_TARGET),
        M_CANDIDATES[-1],
    )

    # 3・4. test で評価する
    results: dict[str, dict[str, Any]] = {}
    for method in methods:
        param = selection[method.name]["param"]
        scores, size, probe_scores = method.run(param, test.emb, test.texts, probe_emb, probe_texts)
        ranks = ranks_of_gold(scores, test.gold)
        hits = {f"hit@{k}": hit_at(ranks, k) for k in (1, 3, m)}
        ci = bootstrap_ci((ranks <= 1).astype(np.float64), seed)
        is_flood = method.name.startswith("flood")
        entry: dict[str, Any] = {
            "param": param,
            **hits,
            "hit@1_ci95": ci,
            "mrr": float(np.mean(1.0 / ranks)),
            "advert_bytes": size,
            # 1 問あたりに質問（埋め込み）を受け取る受信箱の数：flood は 1 段目で全受信箱に届く
            "inboxes_receiving_query": n if is_flood else m,
        }
        if probe_scores is not None:
            owner_ranks = ranks_of_gold(probe_scores, probe_owner)
            entry["owner_top1"] = hit_at(owner_ranks, 1)
            entry["owner_top1_ci95"] = bootstrap_ci((owner_ranks <= 1).astype(np.float64), seed)
            entry["owner_auc"] = owner_auc(probe_scores, probe_owner)
        else:
            entry["owner_top1"] = 1.0 / n
            entry["owner_auc"] = 0.5
        # H-A2 の曲線：全ての大きさの候補の test の hit@1 と所有者 top-1（報告用．値の選択には使わない）
        if len(method.candidates) > 1:
            curve = []
            for c in method.candidates:
                c_scores, c_size, c_probe = method.run(
                    c, test.emb, test.texts, probe_emb, probe_texts
                )
                point = {
                    "param": c,
                    "advert_bytes": c_size,
                    "hit@1": hit_at(ranks_of_gold(c_scores, test.gold), 1),
                }
                if c_probe is not None:
                    point["owner_top1"] = hit_at(ranks_of_gold(c_probe, probe_owner), 1)
                curve.append(point)
            entry["curve"] = curve
            sizes = [float(pt["advert_bytes"]) for pt in curve]
            entry["spearman_size_hit"] = spearman(sizes, [float(pt["hit@1"]) for pt in curve])
            entry["spearman_size_owner"] = spearman(
                sizes, [float(pt.get("owner_top1", 0.0)) for pt in curve]
            )
        results[method.name] = entry
        logger.info(
            "test %s: hit@1 %.4f owner_top1 %.4f", method.name, entry["hit@1"], entry["owner_top1"]
        )
    results["random"] = {
        "hit@1": 1.0 / n,
        "hit@3": 3.0 / n,
        f"hit@{m}": m / n,
        "advert_bytes": 0,
        "inboxes_receiving_query": m,
        "owner_top1": 1.0 / n,
        "owner_auc": 0.5,
    }
    return {
        "n_inboxes": n,
        "n_dev": len(dev.qids),
        "n_test": len(test.qids),
        "n_owner_probes": len(probe_owner),
        "m": m,
        "m_rule": f"smallest m in {list(M_CANDIDATES)} with dev hit@m >= {DEV_HIT_TARGET} for {best_method}",
        "selection": selection,
        "test": results,
        "duration_s": time.perf_counter() - start,
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    lines = [
        "# EQ2：RQ-A のオフラインの評価（公開情報の量と，見つけてもらえる度合い・所有者の特定）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個，dev {metrics['n_dev']} 問，test {metrics['n_test']} 問，所有者の特定に使うメール {metrics['n_owner_probes']} 通",
        f"- m = {m}（{metrics['m_rule']}）",
        "",
        f"| 方式 | 公開情報の大きさ（選んだ値） | 公開情報（KB） | hit@1 [95%CI] | hit@3 | hit@{m} | 質問を受け取る受信箱 | 所有者 top-1 | 所有者 AUC |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r in metrics["test"].items():
        ci = r.get("hit@1_ci95")
        ci_text = f" [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""
        lines.append(
            f"| {name} | {r.get('param', '-')} | {r['advert_bytes'] / 1024:.1f} | {r['hit@1']:.3f}{ci_text} | "
            f"{r['hit@3']:.3f} | {r[f'hit@{m}']:.3f} | {r['inboxes_receiving_query']} | "
            f"{r['owner_top1']:.3f} | {r['owner_auc']:.3f} |"
        )
    lines += [
        "",
        "## 公開情報の大きさと，hit@1・所有者の特定（test．H-A2）",
        "",
        "| 方式 | 大きさの候補ごとの（KB，hit@1，所有者 top-1） | 大きさと hit@1 の順位相関 | 大きさと所有者 top-1 の順位相関 |",
        "|---|---|---|---|",
    ]
    for name, r in metrics["test"].items():
        if "curve" not in r:
            continue
        points = "; ".join(
            f"{pt['param']}: ({pt['advert_bytes'] / 1024:.1f}, {pt['hit@1']:.3f}, "
            f"{pt.get('owner_top1', 0):.3f})"
            for pt in r["curve"]
        )
        lines.append(
            f"| {name} | {points} | {r['spearman_size_hit']:.2f} | {r['spearman_size_owner']:.2f} |"
        )
    lines += [
        "",
        "## dev での選択（MRR）",
        "",
        "| 方式 | 候補ごとの MRR | 選んだ値 |",
        "|---|---|---|",
    ]
    for name, s in metrics["selection"].items():
        cands = ", ".join(f"{k}: {v:.3f}" for k, v in s["dev_mrr_by_param"].items())
        lines.append(f"| {name} | {cands} | {s['param']} |")
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
