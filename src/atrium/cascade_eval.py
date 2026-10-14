"""二段ルーティング centroid_probe のオフラインの評価（Iteration 1）：候補の数 K と，見つけてもらえる度合い・質問の露出の兼ね合い．

`atrium cascade-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

手順（規則は journal の事前登録と同じ．コードに固定し，結果を見て変えない）:
1. 1 段目の採点（multi_centroid．中心の数は data.enronqa.n_centroids）と，2 段目の採点（flood_score の密ベクトル）を全受信箱で求める．
2. K ごとに，1 段目の上位 K 個だけを候補とし，候補の 2 段目の採点の上位 m 個を選ぶ（候補以外は選ばれない）．
   hit@m は「正解の受信箱が選ばれた m 個に入る割合」で，1 問あたりにクエリを受け取る受信箱の数は K である．
3. K の選択：dev の hit@m が，dev の flood_score の hit@m の DEV_RATIO 倍以上になる最小の K（届かなければ最大の K）．
4. test の全 K の曲線と，選んだ K の hit@1・hit@m と区間，退行の確認（K = 受信箱数で flood_score，K = m で multi_centroid が
   EQ2 の値と一致），成功条件 S1・S2 の判定を出す．T3 の新参者の質問での参考値も出す（判定には使わない）．
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
    hit_at,
    load_inboxes,
    load_query_set,
    ranks_of_gold,
    score_flood_dense,
    score_multi,
)
from atrium.arrays import F32Array, I64Array
from atrium.config import AtriumConfig
from atrium.newcomer_eval import choose_newcomers, rows_of
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

K_CANDIDATES = (4, 8, 15, 30, 50, 75)
# dev の hit@m が flood_score の dev の hit@m のこの倍率以上になる最小の K を選ぶ
DEV_RATIO = 0.95
# 届かないときの K
FALLBACK_K = 75
# 成功条件（journal の事前登録）：S1 は multi_centroid より 0.10 以上高く区間が重ならない，
# S2 は flood_score との差が 0.05 以内で K が受信箱数の 20% 以下（150 受信箱で 30）
S1_MIN_GAIN = 0.10
S2_MAX_GAP = 0.05
S2_MAX_K = 30
# 退行の確認（EQ2 の test の hit@2 と一致）
REGRESSION_TOLERANCE = 5e-4
NEWCOMER_COUNT = 30


def cascade_scores(first: F32Array, second: F32Array, k: int) -> F32Array:
    """1 段目の上位 k 個を候補とし，候補の 2 段目の採点を返す（候補以外は -inf．同点は受信箱の番号の小さい方を先にする）．"""
    order = np.argsort(-first, axis=1, kind="stable")[:, :k]
    mask = np.zeros(first.shape, dtype=bool)
    np.put_along_axis(mask, order, True, axis=1)
    out: F32Array = np.where(mask, second, -np.inf).astype(np.float32)
    return out


def hit_stats(ranks: I64Array, m: int, seed: int) -> dict[str, Any]:
    """hit@1・hit@m と，それぞれの 95% ブートストラップ信頼区間．"""
    out: dict[str, Any] = {"n": int(len(ranks))}
    for k in (1, m):
        hit = (ranks <= k).astype(np.float64)
        out[f"hit@{k}"] = hit_at(ranks, k)
        out[f"hit@{k}_ci95"] = bootstrap_ci(hit, seed)
    return out


def choose_k(dev_hit_by_k: dict[int, float], dev_flood_hit: float) -> tuple[int, bool]:
    """dev の hit@m が flood_score の DEV_RATIO 倍以上になる最小の K と，届いたか（届かなければ FALLBACK_K）．"""
    for k in sorted(dev_hit_by_k):
        if dev_hit_by_k[k] >= DEV_RATIO * dev_flood_hit:
            return k, True
    return FALLBACK_K, False


def judge_success(
    chosen: dict[str, Any], multi: dict[str, Any], flood: dict[str, Any], k: int, m: int
) -> dict[str, Any]:
    """成功条件 S1・S2 と，その結論（採用・部分的に支持・棄却）．"""
    key = f"hit@{m}"
    gain = chosen[key] - multi[key]
    s1 = bool(gain >= S1_MIN_GAIN and chosen[f"{key}_ci95"][0] > multi[f"{key}_ci95"][1])
    gap = flood[key] - chosen[key]
    s2 = bool(gap <= S2_MAX_GAP and k <= S2_MAX_K)
    verdict = "採用" if s1 and s2 else "部分的に支持" if s1 else "棄却"
    return {
        "s1": s1,
        "s1_gain_over_multi": gain,
        "s2": s2,
        "s2_gap_to_flood": gap,
        "verdict": verdict,
    }


def regression_check(
    test_hit: dict[int, float], n_inboxes: int, m: int, reference: dict[str, Any]
) -> dict[str, Any]:
    """K = 受信箱数の hit@m が flood_score と，K = m の hit@m が multi_centroid と，EQ2 の値に一致するか．"""
    if reference["m"] != m:
        raise ValueError(f"reference m ({reference['m']}) differs from routing.top_m ({m})")
    expected = {
        n_inboxes: reference["test"]["flood_score"][f"hit@{m}"],
        m: reference["test"]["multi_centroid"][f"hit@{m}"],
    }
    diff = {k: test_hit[k] - v for k, v in expected.items()}
    return {
        "tolerance": REGRESSION_TOLERANCE,
        "expected": expected,
        "got": {k: test_hit[k] for k in expected},
        "diff": diff,
        "passed": all(abs(d) <= REGRESSION_TOLERANCE for d in diff.values()),
    }


def ranks_on_rows(scores: F32Array, gold: Sequence[int], rows: Sequence[int]) -> I64Array:
    """scores の rows 行だけの，正解の受信箱の順位．"""
    idx = np.asarray(rows, dtype=np.int64)
    return ranks_of_gold(scores[idx], [gold[i] for i in rows])


def evaluate(cfg: AtriumConfig, paths: DatasetPaths, reference: dict[str, Any]) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．reference は EQ2 の metrics.json（退行の確認の基準）．"""
    from atrium.data_enronqa import kmeans_centroids

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    n = len(inboxes)
    centers = [kmeans_centroids(ib.emb, e.n_centroids, seed) for ib in inboxes]
    ks = (*K_CANDIDATES, m, n)

    sets = {split: load_query_set(cfg, paths, split) for split in ("dev", "test")}
    first = {s: score_multi(q.emb, centers) for s, q in sets.items()}
    second = {s: score_flood_dense(q.emb, inboxes) for s, q in sets.items()}

    # 手順 2：全 K の順位（dev・test）．K = n は flood_score，K = m は multi_centroid と同じ集合になる
    ranks: dict[str, dict[int, I64Array]] = {
        s: {k: ranks_of_gold(cascade_scores(first[s], second[s], k), sets[s].gold) for k in ks}
        for s in sets
    }
    flood_ranks = {s: ranks_of_gold(second[s], sets[s].gold) for s in sets}
    multi_ranks = {s: ranks_of_gold(first[s], sets[s].gold) for s in sets}

    # 手順 3：K の選択（dev だけを見る）
    dev_flood = hit_at(flood_ranks["dev"], m)
    dev_hit = {k: hit_at(ranks["dev"][k], m) for k in K_CANDIDATES}
    chosen_k, reached = choose_k(dev_hit, dev_flood)

    # 手順 4：test
    test_hit_m = {k: hit_at(ranks["test"][k], m) for k in ks}
    regression = regression_check(test_hit_m, n, m, reference)
    curve = [
        {
            "k": k,
            **hit_stats(ranks["test"][k], m, seed),
            "inboxes_receiving_query": k,
            f"dev_hit@{m}": dev_hit[k],
        }
        for k in K_CANDIDATES
    ]
    flood_stats = hit_stats(flood_ranks["test"], m, seed)
    multi_stats = hit_stats(multi_ranks["test"], m, seed)
    chosen_stats = hit_stats(ranks["test"][chosen_k], m, seed)
    judgement = judge_success(chosen_stats, multi_stats, flood_stats, chosen_k, m)

    # 参考：T3 の新参者（newcomer_eval と同じ 30 受信箱の test の質問）．判定には使わない
    index = {b.name: i for i, b in enumerate(inboxes)}
    rows = [
        r
        for name in choose_newcomers([b.name for b in inboxes], NEWCOMER_COUNT)
        for r in rows_of(sets["test"].gold, index[name])
    ]
    newcomer: dict[str, Any] = {
        "flood_score": hit_stats(ranks_on_rows(second["test"], sets["test"].gold, rows), m, seed),
        "multi_centroid": hit_stats(ranks_on_rows(first["test"], sets["test"].gold, rows), m, seed),
        "centroid_probe": hit_stats(
            ranks_on_rows(
                cascade_scores(first["test"], second["test"], chosen_k), sets["test"].gold, rows
            ),
            m,
            seed,
        ),
    }
    return {
        "n_inboxes": n,
        "n_dev": len(sets["dev"].qids),
        "n_test": len(sets["test"].qids),
        "m": m,
        "n_centroids": e.n_centroids,
        "k_rule": f"smallest K in {list(K_CANDIDATES)} with dev hit@{m} >= {DEV_RATIO} x dev flood_score hit@{m}; {FALLBACK_K} if none",
        "dev_flood_score_hit": dev_flood,
        "dev_hit_by_k": {str(k): v for k, v in dev_hit.items()},
        "chosen_k": chosen_k,
        "chosen_k_reached_target": reached,
        "regression": regression,
        "test": {
            "flood_score": flood_stats,
            "multi_centroid": multi_stats,
            "centroid_probe_chosen": chosen_stats,
            "curve": curve,
        },
        "judgement": judgement,
        "newcomers_reference": {"n_newcomers": NEWCOMER_COUNT, **newcomer},
        "duration_s": time.perf_counter() - start,
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    m = metrics["m"]
    reg, j, t = metrics["regression"], metrics["judgement"], metrics["test"]

    def fmt(s: dict[str, Any]) -> str:
        c1, cm = s["hit@1_ci95"], s[f"hit@{m}_ci95"]
        return f"{s['hit@1']:.3f} [{c1[0]:.3f}, {c1[1]:.3f}] | {s[f'hit@{m}']:.3f} [{cm[0]:.3f}, {cm[1]:.3f}]"

    lines = [
        "# centroid_probe のオフラインの評価（Iteration 1）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個，dev {metrics['n_dev']} 問，test {metrics['n_test']} 問，m = {m}，中心の数 C = {metrics['n_centroids']}",
        f"- K の規則：{metrics['k_rule']}",
        f"- dev の flood_score の hit@{m}：{metrics['dev_flood_score_hit']:.4f}，dev の hit@{m}（K ごと）："
        + ", ".join(f"{k}: {v:.4f}" for k, v in metrics["dev_hit_by_k"].items()),
        f"- 選んだ K：{metrics['chosen_k']}（目標に{'届いた' if metrics['chosen_k_reached_target'] else '届かなかったので既定の K'}）",
        "",
        "## 退行の確認",
        "",
        f"- 許容 {reg['tolerance']}："
        + ("一致" if reg["passed"] else "**不一致（判定に進まない）**"),
        *(
            f"- K = {k}：{reg['got'][k]:.4f}（EQ2 {reg['expected'][k]:.4f}，差 {reg['diff'][k]:+.5f}）"
            for k in reg["expected"]
        ),
        "",
        f"## test の曲線（hit@1・hit@{m} と 95% 区間．クエリを受け取る受信箱 = K）",
        "",
        f"| 方式 | K | hit@1 [95%CI] | hit@{m} [95%CI] |",
        "|---|---|---|---|",
        f"| multi_centroid | {m} | {fmt(t['multi_centroid'])} |",
        *(f"| centroid_probe | {c['k']} | {fmt(c)} |" for c in t["curve"]),
        f"| flood_score | {metrics['n_inboxes']} | {fmt(t['flood_score'])} |",
        "",
        "## 判定（選んだ K）",
        "",
        f"- centroid_probe（K = {metrics['chosen_k']}）：{fmt(t['centroid_probe_chosen'])}",
        f"- S1（multi_centroid より 0.10 以上高く区間が重ならない）：{'成立' if j['s1'] else '不成立'}（差 {j['s1_gain_over_multi']:+.4f}）",
        f"- S2（flood_score との差 0.05 以内かつ K ≤ 30）：{'成立' if j['s2'] else '不成立'}（flood_score との差 {j['s2_gap_to_flood']:+.4f}）",
        f"- 結論：{j['verdict']}",
        "",
        "## 参考：T3 の新参者の質問（判定には使わない）",
        "",
        f"| 方式 | n | hit@1 [95%CI] | hit@{m} [95%CI] |",
        "|---|---|---|---|",
        *(
            f"| {name} | {metrics['newcomers_reference'][name]['n']} | {fmt(metrics['newcomers_reference'][name])} |"
            for name in ("flood_score", "multi_centroid", "centroid_probe")
        ),
    ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
