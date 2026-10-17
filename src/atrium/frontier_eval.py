"""汎用のルーティング方式のフロンティア評価（p0006 P2．EnronQA）．

`atrium frontier-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．
方式は `atrium.frontier_methods.BUILDERS` に足すだけで加わる（CLI は増やさない）．

- 入力：既存の 150 受信箱・30 台の配置（n30）と，1 受信箱 = 1 デバイスの模擬（n150）．要約は本文（ヘッダーを除く）から作る（p0006 §3.2）．
  本文の埋め込みは全メールを文書側で計算し，fp16 に丸める（atrium.body_advert_eval と同じ）．work_dir があれば保存して再利用する．
- 出力：frontier.json（方式×設定×split×subset ごとに 1 行．p0006 §6.2），metrics.json（再現の確認 R4・dev で選んだ設定・仮説の集計），
  analysis_report.md．
- 設定の選び方（事前登録）：方式の族ごとに，dev の hit@m が目標（既定 0.78）以上になる設定のうち，dev の平均の要求数が最小のもの
  （届かなければ dev の hit@m が最大のもの）を，test の主の点とする．test の値で選ばない．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from atrium.advert_eval import Inbox, load_inboxes, load_query_set
from atrium.arrays import F32Array, I64Array
from atrium.body_advert_eval import embed_bodies
from atrium.config import AtriumConfig
from atrium.data_enronqa import read_gold
from atrium.device_probe_eval import device_index_of_inbox
from atrium.frontier_core import (
    SPLITS,
    BoolMat,
    Config,
    FrontierData,
    ProbeDocs,
    evaluate_selection,
    make_row,
)
from atrium.frontier_data import SCORE_NORMS, TARGET_RATIO, Feb4ragBase, MedragBase
from atrium.frontier_methods import build_all, probe_docs
from atrium.header_eval import classify_question, owner_surname, split_header_body
from atrium.manifest import Placement
from atrium.paths import DatasetPaths
from atrium.routing.advert import tokenize

logger = logging.getLogger(__name__)

# p0006 H-P2a：hit@m の目標と，要求数の上限（flood_score の半分）
TARGET_HIT = 0.78
MAX_REQUESTS = 16.0
# H-P2b：打ち切りが固定の D より平均の要求数を減らす割合の下限と，hit の許容の低下
CUTOFF_MIN_REDUCTION = 0.25
CUTOFF_HIT_TOLERANCE = 0.01
# R4：既存の点（p0006 §6.3．hit@2 は小数 4 桁の記録なので丸めの許容を含める）
R4_TOLERANCE = 6e-4
R4_EXPECTED: dict[str, tuple[str, dict[str, Any], float, float | None]] = {
    "flood_score": ("flood_score", {}, 0.8041, 32.0),
    "device_probe_d24": ("device_probe", {"D": 24}, 0.7837, 26.0),
    "device_probe_d12": ("device_probe", {"D": 12}, 0.6830, 14.0),
    "centroid_probe_k75": ("centroid_probe", {"K": 75}, 0.7612, None),
    "multi_centroid": ("multi_centroid", {"C": 32}, 0.4401, 2.0),
}
BODY_CACHE_NAME = "body_emb.npz"
# 新しい方式のうち，p0006 H-P2a の判定の対象にする族（ヘッダー込みの既存の基準は含めない）
ELIGIBLE_FAMILIES = (
    "body_baseline",
    "sample_summary",
    "term_stats",
    "hybrid_summary",
    "adaptive_cutoff",
    "hierarchy",
    "overlay_walk",
)


def config_key(method: str, params: dict[str, Any]) -> str:
    """方式と設定を表す文字列（dev の行と test の行を対応づける）．"""
    return json.dumps([method, params], sort_keys=True)


# ── EnronQA の入力 ─────────────────────────────────────────────────────────


class EnronqaBase:
    """配置に依らない EnronQA の評価の入力（本文の埋め込み・語・質問・2 段目の得点・ヘッダー込みの重心）．"""

    def __init__(self, cfg: AtriumConfig, paths: DatasetPaths, work_dir: Path | None) -> None:
        """全メールを読み，本文の要約の元と質問の入力を作る．"""
        from atrium.advert_eval import score_flood_dense
        from atrium.data_enronqa import kmeans_centroids
        from atrium.encoders import ArcticEncoder

        e = cfg.data.require_enronqa()
        self.cfg = cfg
        self.inboxes: list[Inbox] = load_inboxes(cfg, paths)
        self.names = [ib.name for ib in self.inboxes]
        self.sets = {s: load_query_set(cfg, paths, s) for s in SPLITS}
        self.second = {s: score_flood_dense(q.emb, self.inboxes) for s, q in self.sets.items()}
        self.full_centers = [
            kmeans_centroids(ib.emb, e.n_centroids, cfg.experiment.seed) for ib in self.inboxes
        ]

        cache = None if work_dir is None else work_dir / BODY_CACHE_NAME
        if cache is not None and cache.exists():
            stored = np.load(cache, allow_pickle=False)
            parts = np.split(stored["emb"].astype(np.float32), np.cumsum(stored["lens"])[:-1])
            keep = json.loads(cache.with_suffix(".keep.json").read_text(encoding="utf-8"))
        else:
            encoder = ArcticEncoder(e.encoder, e.embed_batch_size)
            parts, keep, counts = embed_bodies(self.inboxes, encoder)
            logger.info("embedded bodies: %s", counts)
            if cache is not None:
                work_dir_path = cache.parent
                work_dir_path.mkdir(parents=True, exist_ok=True)
                np.savez(
                    cache,
                    emb=np.concatenate(parts).astype(np.float16),
                    lens=np.asarray([len(p) for p in parts]),
                )
                cache.with_suffix(".keep.json").write_text(json.dumps(keep), encoding="utf-8")
        self.summary_emb: list[F32Array] = list(parts)
        self.summary_tokens: list[list[list[str]]] = [
            [tokenize(split_header_body(ib.texts[i])[1]) for i in rows]
            for ib, rows in zip(self.inboxes, keep, strict=True)
        ]
        gold = read_gold(paths)
        test_paths = {gold[q]["path"] for q in self.sets["test"].qids}
        self.exclude_docs: list[set[int]] = [
            {j for j, i in enumerate(rows) if ib.paths[i] in test_paths}
            for ib, rows in zip(self.inboxes, keep, strict=True)
        ]

    def data(self, device_of_source: I64Array, placement: str) -> FrontierData:
        """配置を与えて，評価の入力にする．"""
        n = len(self.names)
        relevant: dict[str, BoolMat] = {}
        subsets: dict[str, dict[str, BoolMat]] = {}
        for s, q in self.sets.items():
            rel = np.zeros((len(q.qids), n), dtype=bool)
            rel[np.arange(len(q.qids)), q.gold] = True
            relevant[s] = rel
            # A 群（質問が持ち主の姓を含む）を除いた部分集合（p0006 §3.2 の質問の偏りの点検）
            named = np.array(
                [
                    classify_question(text, owner_surname(self.names[g]), set())[0] == "A"
                    for text, g in zip(q.texts, q.gold, strict=True)
                ]
            )
            subsets[s] = {"no_owner_name": ~named}
        return FrontierData(
            dataset="enronqa",
            placement=placement,
            names=self.names,
            device_of_source=device_of_source,
            m=self.cfg.routing.top_m["enronqa"],
            target_hit=TARGET_HIT,
            second=self.second,
            relevant=relevant,
            q_emb={s: q.emb for s, q in self.sets.items()},
            q_tokens={s: [tokenize(t) for t in q.texts] for s, q in self.sets.items()},
            subsets=subsets,
            seed=self.cfg.experiment.seed,
            summary_emb=self.summary_emb,
            summary_tokens=self.summary_tokens,
            full_centers=self.full_centers,
            exclude_docs=self.exclude_docs,
        )


# ── 評価 ──────────────────────────────────────────────────────────────────


def run_configs(data: FrontierData, configs: list[Config]) -> list[dict[str, Any]]:
    """設定ごとに dev・test を評価し，frontier の行を返す（test は区間付き）．"""
    probes: ProbeDocs | None = probe_docs(data) if any(c.attribute for c in configs) else None
    rows: list[dict[str, Any]] = []
    for i, cfg in enumerate(configs):
        for split in SPLITS:
            per_query = evaluate_selection(data, cfg.select(split), split)
            rows += make_row(data, cfg, split, per_query, probes, with_ci=split == "test")
        if (i + 1) % 50 == 0:
            logger.info("%s: %d / %d configs", data.placement, i + 1, len(configs))
    return rows


def pick_by_dev(
    rows: list[dict[str, Any]],
    family: str,
    m: int,
    target: float,
    where: Callable[[dict[str, Any]], bool] = lambda _r: True,
) -> dict[str, Any] | None:
    """族の設定を dev だけで選ぶ：dev の hit@m が目標以上で平均の要求数が最小のもの（無ければ hit@m が最大のもの）．

    where は設定の絞り込み（行ごとの条件）．
    """
    dev = [
        r
        for r in rows
        if r["family"] == family and r["split"] == "dev" and r["subset"] == "all" and where(r)
    ]
    if not dev:
        return None
    reached = [r for r in dev if r[f"hit@{m}"] >= target]
    best = (
        min(reached, key=lambda r: (r["requests_per_query"]["mean"], -r[f"hit@{m}"]))
        if reached
        else max(dev, key=lambda r: (r[f"hit@{m}"], -r["requests_per_query"]["mean"]))
    )
    key = config_key(best["method"], best["params"])
    test = {
        r["subset"]: r
        for r in rows
        if r["split"] == "test"
        and config_key(r["method"], r["params"]) == key
        and r["family"] == family
    }
    return {
        "family": family,
        "method": best["method"],
        "params": best["params"],
        "dev_target_reached": bool(reached),
        "dev_hit": best[f"hit@{m}"],
        "dev_requests": best["requests_per_query"]["mean"],
        "test": test,
    }


def pareto_front(rows: list[dict[str, Any]], m: int, subset: str = "all") -> list[dict[str, Any]]:
    """test の (要求数の平均, hit@m) のフロンティア（要求数が少なく hit が高いものが支配する）．"""
    pts = sorted(
        (r for r in rows if r["split"] == "test" and r["subset"] == subset),
        key=lambda r: (r["requests_per_query"]["mean"], -r[f"hit@{m}"]),
    )
    front: list[dict[str, Any]] = []
    best = -1.0
    for r in pts:
        if r[f"hit@{m}"] > best + 1e-12:
            front.append(
                {
                    "method": r["method"],
                    "params": r["params"],
                    "requests": r["requests_per_query"]["mean"],
                    f"hit@{m}": r[f"hit@{m}"],
                }
            )
            best = r[f"hit@{m}"]
    return front


def check_reference(rows: list[dict[str, Any]], m: int) -> dict[str, Any]:
    """R4：既存の点（p0006 §6.3）を再現するか（test・全問の hit@m と要求数）．"""
    out: dict[str, Any] = {}
    for name, (method, params, hit, requests) in R4_EXPECTED.items():
        found = [
            r
            for r in rows
            if r["split"] == "test"
            and r["subset"] == "all"
            and r["family"] == "reference"
            and r["method"] == method
            and all(r["params"].get(k) == v for k, v in params.items())
        ]
        if not found:
            out[name] = {"found": False, "passed": False}
            continue
        r = found[0]
        got_requests = r["requests_per_query"]["mean"]
        out[name] = {
            "found": True,
            "expected_hit": hit,
            "got_hit": r[f"hit@{m}"],
            "expected_requests": requests,
            "got_requests": got_requests,
            "passed": abs(r[f"hit@{m}"] - hit) <= R4_TOLERANCE
            and (requests is None or abs(got_requests - requests) < 1e-9),
        }
    out["passed"] = all(v["passed"] for v in out.values())
    return out


def judge_hypotheses(by_placement: dict[str, list[dict[str, Any]]], m: int) -> dict[str, Any]:
    """事前登録の仮説 H-P2a・H-P2b・H-P2c の集計（判定は journal の規則．ここでは値を並べて機械的に判定する）．"""
    chosen: dict[str, dict[str, Any]] = {}
    for placement, rows in by_placement.items():
        chosen[placement] = {
            fam: picked
            for fam in ELIGIBLE_FAMILIES
            if (picked := pick_by_dev(rows, fam, m, TARGET_HIT)) is not None
        }
    n30 = chosen.get("n30", {})
    h_a = {
        fam: {
            "test_hit": c["test"]["all"][f"hit@{m}"],
            "test_requests": c["test"]["all"]["requests_per_query"]["mean"],
            "dev_target_reached": c["dev_target_reached"],
            "passed": c["test"]["all"][f"hit@{m}"] >= TARGET_HIT
            and c["test"]["all"]["requests_per_query"]["mean"] <= MAX_REQUESTS,
        }
        for fam, c in n30.items()
    }
    return {
        "chosen": chosen,
        "h_p2a": {"by_family": h_a, "passed": any(v["passed"] for v in h_a.values())},
        "h_p2b": judge_cutoff(by_placement.get("n30", []), m),
        "h_p2c": judge_hierarchy_growth(by_placement, m),
    }


def same_base(base: str) -> Callable[[dict[str, Any]], bool]:
    """打ち切りの設定のうち，1 段目が base のものだけを残す条件．"""
    return lambda r: bool(r["params"].get("base") == base)


def judge_cutoff(rows: list[dict[str, Any]], m: int) -> dict[str, Any]:
    """H-P2b：同じ 1 段目の上で，問ごとの打ち切りが固定の D より平均の要求数を 25% 以上減らし，hit を 0.01 より下げないか．

    固定の D と打ち切りは，どちらも dev で（目標 hit@m に届く最小の要求数で）選び，test の値を比べる．
    """
    bases = {
        "multi_centroid_body": (
            "body_baseline",
            lambda r: r["method"] == "device_probe_body",
        ),
        "sample_64_max": (
            "sample_summary",
            lambda r: r["params"].get("S") == 64 and r["params"].get("scorer") == "max",
        ),
    }
    out: dict[str, Any] = {}
    for base, (family, where) in bases.items():
        fixed = pick_by_dev(rows, family, m, TARGET_HIT, where)
        adaptive = pick_by_dev(rows, "adaptive_cutoff", m, TARGET_HIT, same_base(base))
        if fixed is None or adaptive is None:
            out[base] = {"available": False, "passed": False}
            continue
        f, a = fixed["test"]["all"], adaptive["test"]["all"]
        reduction = 1 - a["requests_per_query"]["mean"] / f["requests_per_query"]["mean"]
        out[base] = {
            "available": True,
            "fixed": {
                "params": fixed["params"],
                "hit": f[f"hit@{m}"],
                "requests": f["requests_per_query"]["mean"],
                "dev_target_reached": fixed["dev_target_reached"],
            },
            "adaptive": {
                "params": adaptive["params"],
                "hit": a[f"hit@{m}"],
                "requests": a["requests_per_query"]["mean"],
                "dev_target_reached": adaptive["dev_target_reached"],
            },
            "reduction": reduction,
            "passed": bool(
                reduction >= CUTOFF_MIN_REDUCTION
                and a[f"hit@{m}"] >= f[f"hit@{m}"] - CUTOFF_HIT_TOLERANCE
            ),
        }
    out["passed"] = any(v["passed"] for v in out.values())
    return out


def judge_hierarchy_growth(by_placement: dict[str, list[dict[str, Any]]], m: int) -> dict[str, Any]:
    """H-P2c：デバイスが n30 から n150 に増えたとき，dev で選んだ F3 の要求数の伸びが，全員に聞く方式（flood_score）より緩やかか．"""
    if "n150" not in by_placement:
        return {"available": False, "passed": False}
    requests: dict[str, dict[str, float]] = {}
    for placement, rows in by_placement.items():
        picked = pick_by_dev(rows, "hierarchy", m, TARGET_HIT)
        flood = [
            r
            for r in rows
            if r["method"] == "flood_score" and r["split"] == "test" and r["subset"] == "all"
        ]
        if picked is None or not flood:
            return {"available": False, "passed": False}
        requests[placement] = {
            "hierarchy": picked["test"]["all"]["requests_per_query"]["mean"],
            "hierarchy_hit": picked["test"]["all"][f"hit@{m}"],
            "flood": flood[0]["requests_per_query"]["mean"],
        }
    growth_h = requests["n150"]["hierarchy"] / requests["n30"]["hierarchy"]
    growth_f = requests["n150"]["flood"] / requests["n30"]["flood"]
    return {
        "available": True,
        "requests": requests,
        "growth_hierarchy": growth_h,
        "growth_flood": growth_f,
        "passed": bool(growth_h < growth_f),
    }


# ── 他のデータ（P3．§7.3 の汎用の判定）──────────────────────────────────────


def infer_m(rows: list[dict[str, Any]]) -> int:
    """行の列名（source_recall@m）から m を読む．"""
    key = next(k for k in rows[0] if k.startswith("source_recall@") and not k.endswith("_ci95"))
    return int(key.split("@")[1])


def flood_reference(rows: list[dict[str, Any]], m: int) -> tuple[int, float]:
    """全員に聞く方式（flood_score）の test・全問の再現率と，デバイスの数（要求数 − m）．"""
    flood = next(
        r
        for r in rows
        if r["method"] == "flood_score" and r["split"] == "test" and r["subset"] == "all"
    )
    return int(round(flood["requests_per_query"]["mean"])) - m, float(flood[f"source_recall@{m}"])


def family_curves(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """方式の族ごとの test の曲線（再現率と要求数）．目標（全員に聞く方式の再現率の 0.95 倍）に届く最小の要求数と，
    フロンティアの下の面積（要求数 [m, n+m] を 1 に正規化．x 以下の点の最良の再現率の階段）を出す．"""
    m = infer_m(rows)
    n_dev, flood = flood_reference(rows, m)
    key = f"source_recall@{m}"
    # §7.3：EnronQA は hit@m 0.78，他のデータは全員に聞く方式の再現率の 0.95 倍
    target = TARGET_HIT if rows[0]["dataset"] == "enronqa" else TARGET_RATIO * flood
    lo, hi = float(m), float(n_dev + m)
    out: dict[str, dict[str, Any]] = {}
    for fam in sorted({r["family"] for r in rows}):
        pts = sorted(
            (r["requests_per_query"]["mean"], r[key])
            for r in rows
            if r["family"] == fam and r["split"] == "test" and r["subset"] == "all"
        )
        reached = [x for x, y in pts if y >= target]
        best = np.maximum.accumulate([y for _, y in pts])
        xs = np.clip([x for x, _ in pts], lo, hi)
        area = float(np.sum(np.diff(np.r_[xs, hi]) * best) / (hi - lo))
        out[fam] = {
            "target": target,
            "min_requests_to_target": min(reached) if reached else None,
            "area": area,
            "best_recall": float(best[-1]),
        }
    return out


def best_at_budget(
    rows: list[dict[str, Any]], family: str, m: int, budget: float
) -> dict[str, Any] | None:
    """test・全問で，平均の要求数が budget 以下の設定のうち再現率が最大の行．"""
    key = f"source_recall@{m}"
    cands = [
        r
        for r in rows
        if r["family"] == family
        and r["split"] == "test"
        and r["subset"] == "all"
        and r["requests_per_query"]["mean"] <= budget
    ]
    return max(cands, key=lambda r: r[key]) if cands else None


def better_family(curves: dict[str, dict[str, Any]], x: str, y: str) -> str:
    """§7.3：目標に届く最小の要求数で順位づけし（届かない場合は面積），x が y より優れるか（x・y・tie）．"""
    cx, cy = curves[x], curves[y]
    rx, ry = cx["min_requests_to_target"], cy["min_requests_to_target"]
    if rx is not None and ry is not None:
        return "tie" if abs(rx - ry) < 1e-9 else (x if rx < ry else y)
    if rx is not None:
        return x
    if ry is not None:
        return y
    return "tie" if abs(cx["area"] - cy["area"]) < 1e-9 else (x if cx["area"] > cy["area"] else y)


def recall_ci(row: dict[str, Any], m: int) -> list[float]:
    """再現率の 95% 区間（単一ラベルのデータの古い行は hit@m の区間が同じ値なので代用する）．"""
    ci: list[float] = row.get(f"source_recall@{m}_ci95") or row[f"hit@{m}_ci95"]
    return ci


def generality_verdict(
    per_dataset: dict[str, list[dict[str, Any]]], x: str, y: str
) -> dict[str, Any]:
    """§7.3：x が y より優れることが 3 つのデータのうち 2 つ以上で成り立ち，残りで逆転の差が区間の外にない場合だけ「汎用に優れる」．

    逆転の差が区間の外にある：y が優れるデータで，要求数の予算が全員に聞く方式の半分のとき，
    y の再現率の 95% 区間の下端が x の上端より大きい．
    """
    detail: dict[str, Any] = {}
    wins = 0
    reversal = False
    for name, rows in per_dataset.items():
        curves = family_curves(rows)
        if x not in curves or y not in curves:
            detail[name] = {"available": False}
            continue
        m = infer_m(rows)
        n_dev, _ = flood_reference(rows, m)
        who = better_family(curves, x, y)
        budget = (n_dev + m) / 2
        bx, by = best_at_budget(rows, x, m, budget), best_at_budget(rows, y, m, budget)
        key = f"source_recall@{m}"
        # 予算内に点が無い方式は，再現率 0 とみなす（区間も 0）
        x_upper = recall_ci(bx, m)[1] if bx else 0.0
        outside = bool(who == y and by and recall_ci(by, m)[0] > x_upper)
        wins += who == x
        reversal = reversal or outside
        detail[name] = {
            "available": True,
            "better": who,
            "budget": budget,
            "x": None if bx is None else {"recall": bx[key], "ci95": recall_ci(bx, m)},
            "y": None if by is None else {"recall": by[key], "ci95": recall_ci(by, m)},
            "min_requests_x": curves[x]["min_requests_to_target"],
            "min_requests_y": curves[y]["min_requests_to_target"],
            "reversal_outside_interval": outside,
        }
    return {
        "x": x,
        "y": y,
        "wins": wins,
        "n_datasets": len(per_dataset),
        "reversal_outside_interval": reversal,
        "generally_better": bool(wins >= 2 and not reversal),
        "detail": detail,
    }


def evaluate_other(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    dataset: str,
    method_names: list[str],
    work_dir: Path | None,
) -> dict[str, Any]:
    """FeB4RAG・MedRAG で方式を評価する（P3）．FeB4RAG は得点の正規化（none・zscore・rank）ごとに流し，dev の全員に聞く方式の再現率が最大のものを使う．"""
    start = time.perf_counter()
    datas: dict[str, FrontierData] = {}
    extra: dict[str, Any] = {}
    if dataset == "feb4rag":
        base = Feb4ragBase(cfg, paths, work_dir)
        datas = {f"feb4rag_{norm}": base.data(norm) for norm in SCORE_NORMS}
    else:
        medrag = MedragBase(cfg, paths, work_dir)
        datas = {"medrag": medrag.data()}
        extra["label_agreement"] = medrag.label_agreement
    by_placement = {name: run_configs(d, build_all(d, method_names)) for name, d in datas.items()}
    flood_dev = {name: float(d.cache["flood_recall"]["dev"]) for name, d in datas.items()}
    chosen = max(flood_dev, key=lambda k: flood_dev[k])
    curves = {name: family_curves(rows) for name, rows in by_placement.items()}
    return {
        "rows": [r for rows in by_placement.values() for r in rows],
        "metrics": {
            "dataset": dataset,
            "methods": method_names,
            "m": next(iter(datas.values())).m,
            "n_devices": next(iter(datas.values())).n_devices,
            "flood_recall": {n: d.cache["flood_recall"] for n, d in datas.items()},
            "chosen_placement": chosen,
            "curves": curves,
            "target_dev": {n: d.target_hit for n, d in datas.items()},
            **extra,
            "duration_s": time.perf_counter() - start,
        },
    }


# ── 全体 ──────────────────────────────────────────────────────────────────


def evaluate(
    cfg: AtriumConfig,
    paths: DatasetPaths,
    placement: Placement,
    method_names: list[str],
    work_dir: Path | None,
    with_n150: bool,
) -> dict[str, Any]:
    """n30（既存の配置）と n150（1 受信箱 = 1 デバイス）で方式を評価する．"""
    start = time.perf_counter()
    base = EnronqaBase(cfg, paths, work_dir)
    n = len(base.names)
    placements = {"n30": device_index_of_inbox(base.names, placement)}
    if with_n150:
        placements["n150"] = np.arange(n, dtype=np.int64)
    by_placement: dict[str, list[dict[str, Any]]] = {}
    for name, device_of_source in placements.items():
        data = base.data(device_of_source, name)
        configs = build_all(data, method_names)
        logger.info("%s: %d configs, %d devices", name, len(configs), data.n_devices)
        by_placement[name] = run_configs(data, configs)
    m = cfg.routing.top_m["enronqa"]
    rows = [r for placement_rows in by_placement.values() for r in placement_rows]
    return {
        "rows": rows,
        "metrics": {
            "m": m,
            "n_inboxes": n,
            "checks": {"r4": check_reference(by_placement["n30"], m)},
            "hypotheses": judge_hypotheses(by_placement, m),
            "pareto": {
                p: {sub: pareto_front(r, m, sub) for sub in ("all", "no_owner_name")}
                for p, r in by_placement.items()
            },
            "methods": method_names,
            "duration_s": time.perf_counter() - start,
        },
    }


def render_report(result: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    metrics = result["metrics"]
    m = metrics["m"]
    r4 = metrics["checks"]["r4"]
    lines = [
        "# frontier-eval（p0006 P2．EnronQA）",
        "",
        f"- 方式：{', '.join(metrics['methods'])}．m = {m}，受信箱 {metrics['n_inboxes']}",
        f"- R4（既存の点の再現）：{'通過' if r4['passed'] else '**不通過**'}",
        "",
        "| 既存の点 | 期待の hit | 得た hit | 期待の要求数 | 得た要求数 | 通過 |",
        "|---|---|---|---|---|---|",
    ]
    for name, v in r4.items():
        if name == "passed" or not v["found"]:
            continue
        exp_req = "-" if v["expected_requests"] is None else f"{v['expected_requests']:.1f}"
        lines.append(
            f"| {name} | {v['expected_hit']:.4f} | {v['got_hit']:.4f} | {exp_req} | {v['got_requests']:.2f} | {v['passed']} |"
        )
    hyp = metrics["hypotheses"]
    lines += ["", "## dev で選んだ設定の test の値（n30 と n150）", ""]
    for placement, fams in hyp["chosen"].items():
        lines += [
            f"### {placement}",
            "",
            f"| 族 | 設定 | dev が目標に届く | test の要求数（平均・p95） | 往復（平均） | test の hit@{m} | A 群を除く hit@{m} | 公開情報の大きさ（bytes） | 出どころ top-1 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for fam, c in fams.items():
            t = c["test"]["all"]
            sub = c["test"].get("no_owner_name")
            attr = t.get("attribution_top1")
            lines.append(
                f"| {fam} | {c['method']} {json.dumps(c['params'], ensure_ascii=False)} | {c['dev_target_reached']} "
                f"| {t['requests_per_query']['mean']:.2f}・{t['requests_per_query']['p95']:.0f} | {t['round_trips']['mean']:.2f} "
                f"| {t[f'hit@{m}']:.4f} | {'-' if sub is None else format(sub[f'hit@{m}'], '.4f')} "
                f"| {t['advert_bytes']:,} | {'-' if attr is None else format(attr, '.3f')} |"
            )
        lines.append("")
    h_a = hyp["h_p2a"]
    lines += [
        "## H-P2a（hit@m ≥ 0.78 を要求数 ≤ 16 で）",
        "",
        f"結果：{'成立' if h_a['passed'] else '不成立'}",
        "",
        "| 族 | test の hit | test の要求数 | 通過 |",
        "|---|---|---|---|",
        *(
            f"| {fam} | {v['test_hit']:.4f} | {v['test_requests']:.2f} | {v['passed']} |"
            for fam, v in h_a["by_family"].items()
        ),
        "",
    ]
    return "\n".join(lines) + "\n"


def render_other_report(result: dict[str, Any]) -> str:
    """FeB4RAG・MedRAG の analysis_report.md の本文．"""
    metrics = result["metrics"]
    m = metrics["m"]
    lines = [
        f"# frontier-eval（p0006 P3．{metrics['dataset']}）",
        "",
        f"- 方式：{', '.join(metrics['methods'])}．m = {m}，デバイス {metrics['n_devices']}",
        f"- dev の全員に聞く方式の再現率が最大の配置：{metrics['chosen_placement']}",
    ]
    if "label_agreement" in metrics:
        lines.append(
            f"- シャードのラベルから求めたデータ源の集合が labels.json と一致する割合：{metrics['label_agreement']:.4f}"
        )
    for name, fams in metrics["curves"].items():
        flood = metrics["flood_recall"][name]
        lines += [
            "",
            f"## {name}（全員に聞く方式の再現率 dev {flood['dev']:.4f}・test {flood['test']:.4f}．目標 = 0.95 倍）",
            "",
            "| 族 | 目標に届く最小の要求数（test） | 面積 | 最良の再現率 |",
            "|---|---|---|---|",
            *(
                f"| {fam} | {'-' if c['min_requests_to_target'] is None else format(c['min_requests_to_target'], '.2f')} "
                f"| {c['area']:.4f} | {c['best_recall']:.4f} |"
                for fam, c in fams.items()
            ),
        ]
    return "\n".join(lines) + "\n"


def render(result: dict[str, Any]) -> str:
    """データに応じた analysis_report.md の本文（EnronQA は dev で選んだ設定の表，他は族ごとの曲線の表）．"""
    return render_other_report(result) if "dataset" in result["metrics"] else render_report(result)


def write_outputs(out_dir: Path, result: dict[str, Any]) -> None:
    """frontier.json・metrics.json・analysis_report.md を書く（メールの本文は含まない）．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "frontier.json").write_text(
        json.dumps(result["rows"], ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "metrics.json").write_text(
        json.dumps(result["metrics"], indent=1, ensure_ascii=False), encoding="utf-8"
    )
    report = (
        render_other_report(result) if "dataset" in result["metrics"] else render_report(result)
    )
    (out_dir / "analysis_report.md").write_text(report, encoding="utf-8")
