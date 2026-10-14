"""RQ1 の新参者の評価を EnronQA の 150 受信箱で行う（p0005 T3）：学習のときにいなかった受信箱を，再学習せずに見つけられるか．

`atrium newcomer-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

- 新参者：150 受信箱を名前の昇順に並べ，`numpy.random.default_rng(12)` で 30 個を非復元抽出する（先頭から n_newcomers 個を使う）．
- 新参者 n ごとに，素朴な中央方式（ragroute）を 2 通りで比べる．
  - ragroute-upper：既存のルーター（150 受信箱の全てで学習済み）．
  - ragroute-frozen：n を除いた 149 受信箱で学習し直したルーター．判定のとき n の one-hot を 0 にする（再学習しない場合）．
- 学習の要らない方式（card_sim・term_sketch・centroid_sim・multi_centroid・flood_score・random）は，EQ2 と同じ計算で，n の扱いは変わらない．
- 評価する質問：test のうち，正解の受信箱が新参者である質問（受信箱あたり最大 20 問）．30 個を合わせて集計する（pooled）．m = 2（EQ2）．
- 所要時間の上限：最初の 1 個の学習の時間から，全ての新参者の合計が TIME_LIMIT_S を超える見込みなら，新参者を半分（先頭から）に減らす．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.advert_eval import (
    bootstrap_ci,
    build_methods,
    load_inboxes,
    load_query_set,
    ranks_of_gold,
)
from atrium.arrays import F32Array, I64Array
from atrium.config import AtriumConfig
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

N_NEWCOMERS = 30
NEWCOMER_SEED = 12
# 全ての新参者の学習の合計の上限（秒）．最初の 1 個の学習の時間から見積もる
TIME_LIMIT_S = 4 * 3600
# 退行の確認（EQ2 の test の hit@1・hit@2 と小数 3 桁で一致）
REGRESSION_TOLERANCE = 5e-4
# term_sketch の採点は，語の集合を走査して足す順序が文字列のハッシュの種で変わり，同点の扱いが揺れる（T2 の診断で ±0.001）
TERM_SKETCH_TOLERANCE = 2e-3
LEARNING_FREE = ("card_sim", "term_sketch", "centroid_sim", "multi_centroid", "flood_score")
PUBLIC_METHODS = ("card_sim", "term_sketch", "centroid_sim", "multi_centroid")


def choose_newcomers(sources: Sequence[str], n: int, seed: int = NEWCOMER_SEED) -> list[str]:
    """受信箱を名前の昇順に並べ，種で決めた順に非復元抽出した n 個（n を変えても先頭の部分は同じ）．"""
    ordered = sorted(sources)
    order = np.random.default_rng(seed).choice(len(ordered), size=len(ordered), replace=False)
    return [ordered[int(i)] for i in order[:n]]


def rows_of(gold: Sequence[int], inbox: int, invert: bool = False) -> list[int]:
    """正解の受信箱が inbox の質問の行（invert なら，それ以外の受信箱の質問）．"""
    return [r for r, g in enumerate(gold) if (g == inbox) != invert]


def ranks_for_rows(scores: F32Array, gold: Sequence[int], rows: Sequence[int]) -> I64Array:
    """scores の rows 行だけの，正解の受信箱の順位（同点は不利な側）．"""
    idx = np.asarray(rows, dtype=np.int64)
    return ranks_of_gold(scores[idx], [gold[i] for i in rows])


def pooled_stats(ranks: I64Array, seed: int, ms: Sequence[int] = (1, 2)) -> dict[str, Any]:
    """新参者の問を合わせた順位から，hit@m と 95% ブートストラップ信頼区間．"""
    out: dict[str, Any] = {"n": int(len(ranks))}
    for m in ms:
        hit = (ranks <= m).astype(np.float64)
        out[f"hit@{m}"] = float(hit.mean())
        out[f"hit@{m}_ci95"] = bootstrap_ci(hit, seed)
    return out


def intervals_disjoint_above(high: dict[str, Any], low: dict[str, Any], key: str = "hit@2") -> bool:
    """high の key の 95% 区間が，low の区間より上にあるか（重ならない）．"""
    return bool(high[f"{key}_ci95"][0] > low[f"{key}_ci95"][1])


def judge(pooled: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """H-N1・H-N2（事前登録）．

    H-N1：ragroute-frozen < ragroute-upper かつ区間が重ならない．
    H-N2：flood_score と，公開情報を使う方式のうち pooled hit@2 が最良のものが，それぞれ ragroute-frozen より高い（区間が重ならない）．
    """
    frozen, upper = pooled["ragroute-frozen"], pooled["ragroute-upper"]
    best_public = max(PUBLIC_METHODS, key=lambda m: pooled[m]["hit@2"])
    return {
        "h_n1": intervals_disjoint_above(upper, frozen),
        "best_public": best_public,
        "h_n2_flood": intervals_disjoint_above(pooled["flood_score"], frozen),
        "h_n2_best_public": intervals_disjoint_above(pooled[best_public], frozen),
    }


def regression_check(
    full_test: dict[str, dict[str, float]], reference: dict[str, Any]
) -> dict[str, Any]:
    """全 test の hit@1・hit@2 が EQ2 の値と一致するか（学習の要らない方式と ragroute-upper）．"""
    diffs: dict[str, dict[str, float]] = {}
    for name, got in full_test.items():
        ref = reference["test"][name]
        diffs[name] = {
            "hit@1": got["hit@1"] - ref["hit@1"],
            "hit@2": got["hit@2"] - ref[f"hit@{reference['m']}"],
        }
    passed = reference["m"] == 2 and all(
        abs(d) <= (TERM_SKETCH_TOLERANCE if name == "term_sketch" else REGRESSION_TOLERANCE)
        for name, per in diffs.items()
        for d in per.values()
    )
    return {
        "tolerance": REGRESSION_TOLERANCE,
        "tolerance_term_sketch": TERM_SKETCH_TOLERANCE,
        "diff": diffs,
        "passed": passed,
    }


def frozen_scores(
    router_dir: Path,
    cfg: AtriumConfig,
    inboxes: Sequence[Any],
    query_emb: F32Array,
) -> F32Array:
    """n を除いて学習したルーターが，全 150 受信箱に付ける確率（n の one-hot は 0）．"""
    from atrium.routing import RoutingQuery, SourceProfile
    from atrium.routing.ragroute import RagrouteRouter

    e = cfg.data.require_enronqa()
    router = RagrouteRouter(
        router_dir, e.sources, cfg.routing.ragroute_threshold, allow_unseen=True
    )
    profiles = [
        SourceProfile(
            ib.name, "faiss", "", e.encoder, ib.emb.mean(axis=0), len(ib.texts), (ib.name,)
        )
        for ib in inboxes
    ]
    out = np.stack(
        [
            router.probabilities(RoutingQuery(str(i), "", {e.encoder: v}), profiles)
            for i, v in enumerate(query_emb)
        ]
    )
    return out.astype(np.float32)


def evaluate(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    reference: dict[str, Any],
    work_dir: Path,
    n_newcomers: int = N_NEWCOMERS,
) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．reference は EQ2 の metrics.json，work_dir は再学習したルーターの置き場所（results/ の外）．"""
    from atrium.train_router import train_router

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    names = [ib.name for ib in inboxes]
    test = load_query_set(cfg, paths, "test")
    n_all = len(names)

    # 学習の要らない方式と ragroute-upper：全 test のスコアを 1 回だけ計算する（EQ2 と同じ計算）
    params = {
        "card_sim": e.card_subjects,
        "term_sketch": e.term_sketch_size,
        "centroid_sim": 0,
        "multi_centroid": e.n_centroids,
        "flood_score": 0,
        "ragroute": 0,
    }
    scores: dict[str, F32Array] = {}
    for method in build_methods(cfg, inboxes, paths):
        if method.name in params:
            scores[method.name], _, _ = method.run(
                params[method.name], test.emb, test.texts, test.emb[:1], test.texts[:1]
            )
            logger.info("scored %s", method.name)
    scores["ragroute-upper"] = scores.pop("ragroute")
    scores["random"] = (
        np.random.default_rng(seed).random((len(test.qids), n_all)).astype(np.float32)
    )
    full_test = {
        name: {
            "hit@1": float((ranks_of_gold(s, test.gold) <= 1).mean()),
            "hit@2": float((ranks_of_gold(s, test.gold) <= m).mean()),
        }
        for name, s in scores.items()
        if name != "random"
    }
    regression = regression_check(
        {("ragroute" if k == "ragroute-upper" else k): v for k, v in full_test.items()}, reference
    )

    # 新参者
    newcomers = choose_newcomers(names, n_newcomers)
    index = {n: i for i, n in enumerate(names)}
    train_seconds: list[float] = []
    per_newcomer: list[dict[str, Any]] = []
    ranks: dict[str, list[I64Array]] = {k: [] for k in (*scores, "ragroute-frozen")}
    frozen_existing: list[I64Array] = []
    used = list(newcomers)
    work_dir.mkdir(parents=True, exist_ok=True)
    i = 0
    while i < len(used):
        n = used[i]
        rows = rows_of(test.gold, index[n])
        rows_other = rows_of(test.gold, index[n], invert=True)
        t0 = time.perf_counter()
        train_router(cfg, "enronqa", paths, exclude=[n], out_dir=work_dir / n)
        train_seconds.append(time.perf_counter() - t0)
        if i == 0 and train_seconds[0] * len(used) > TIME_LIMIT_S:
            used = used[: max(1, len(used) // 2)]
            logger.warning(
                "training takes %.0f s each: using %d newcomers", train_seconds[0], len(used)
            )
        frozen = frozen_scores(work_dir / n, cfg, inboxes, test.emb)
        entry: dict[str, Any] = {
            "newcomer": n,
            "n_questions": len(rows),
            "train_s": train_seconds[-1],
        }
        for name, s in {**scores, "ragroute-frozen": frozen}.items():
            r = ranks_for_rows(s, test.gold, rows)
            ranks[name].append(r)
            entry[f"{name}_hit@{m}"] = float((r <= m).mean()) if len(r) else None
        frozen_existing.append(ranks_for_rows(frozen, test.gold, rows_other))
        per_newcomer.append(entry)
        logger.info("newcomer %d/%d %s done", i + 1, len(used), n)
        i += 1

    pooled = {name: pooled_stats(np.concatenate(rs), seed) for name, rs in ranks.items()}
    existing = pooled_stats(np.concatenate(frozen_existing), seed)
    return {
        "n_inboxes": n_all,
        "n_test": len(test.qids),
        "m": m,
        "newcomers": used,
        "newcomers_sampled": newcomers,
        "train_seconds": train_seconds,
        "regression": regression,
        "pooled": pooled,
        "frozen_on_existing_inboxes": existing,
        "judgement": judge(pooled),
        "per_newcomer": per_newcomer,
        "duration_s": time.perf_counter() - start,
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    j = metrics["judgement"]
    reg = metrics["regression"]
    lines = [
        "# T3：150 人での新参者の評価（再学習なしで新参者を見つけられるか）",
        "",
        f"- 新参者 {len(metrics['newcomers'])} 個（抽出した {len(metrics['newcomers_sampled'])} 個の先頭から），test の質問は新参者の問だけ（pooled），m = {metrics['m']}",
        f"- 退行の確認（全 test の hit@1・hit@2 が EQ2 と一致）：{'通過' if reg['passed'] else '不一致'}",
        f"- ragroute-frozen の学習時間：1 個あたり平均 {sum(metrics['train_seconds']) / len(metrics['train_seconds']):.0f} 秒",
        "",
        "| 方式 | n | hit@1 | hit@2 [95%CI] |",
        "|---|---|---|---|",
    ]
    for name, p in metrics["pooled"].items():
        lines.append(
            f"| {name} | {p['n']} | {p['hit@1']:.3f} | {p['hit@2']:.3f} [{p['hit@2_ci95'][0]:.3f}, {p['hit@2_ci95'][1]:.3f}] |"
        )
    ex = metrics["frozen_on_existing_inboxes"]
    lines += [
        "",
        f"- 参考：ragroute-frozen の，新参者ではない受信箱の質問での hit@2 = {ex['hit@2']:.3f}（n = {ex['n']}）",
        "",
        "## 判定",
        "",
        f"- H-N1（ragroute-frozen < ragroute-upper，区間が重ならない）：{'支持' if j['h_n1'] else '支持されない'}",
        f"- H-N2（flood_score が ragroute-frozen より高い，区間が重ならない）：{'支持' if j['h_n2_flood'] else '支持されない'}",
        f"- H-N2（公開情報を使う最良の方式 {j['best_public']} が ragroute-frozen より高い，区間が重ならない）：{'支持' if j['h_n2_best_public'] else '支持されない'}",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く（再学習したルーターは書かない）．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
