"""RQ-A の持ち主推定の定義の統一（p0005 T2）：同じ公開情報・同じ採点で，入力だけを変えて持ち主を当てる．

`atrium owner-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．

EQ2（`atrium advert-eval`）の「所有者 top-1」はメール本文（長い文）から持ち主を当て，hit@1 は質問文（短い文）から正解の受信箱を当てる．
入力の情報量が違うので，公開情報の方式ごとに次の 3 つの入力で top-1 と AUC を出し，違いが入力の長さで説明できるかを見る．

- Q：test の質問文（EQ2 と同じ 2,940 問）．top-1 は EQ2 の hit@1 と一致するはずである．
- E：EQ2 と同じ所有者推定用のメール本文（受信箱あたり最大 10 通）．top-1・AUC は EQ2 の所有者 top-1・AUC と一致するはずである．
- E_short：E の各メールを，test の質問のトークン数の中央値（arctic-embed-m-v1.5 のトークナイザで数える）に先頭から切ったもの．

公開情報の大きさは EQ2 で選んだ値（config.yaml の data.enronqa）を使い，選び直さない．
E_short の埋め込みは E と同じく文書側（接頭辞なし）で計算する．Q はクエリ側の接頭辞を付けた埋め込みである．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from atrium.advert_eval import (
    bootstrap_ci,
    build_methods,
    hit_at,
    load_inboxes,
    load_query_set,
    owner_auc,
    owner_probes,
    ranks_of_gold,
)
from atrium.arrays import F32Array
from atrium.config import AtriumConfig
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

# 受信箱ごとの公開情報を持つ，評価の対象の方式
METHODS = ("card_sim", "term_sketch", "centroid_sim", "multi_centroid")
INPUTS = ("Q", "E", "E_short")
# 退行の確認の許容（小数 3 桁で一致）
REGRESSION_TOLERANCE = 5e-4


class Tokenizer(Protocol):
    """HuggingFace のトークナイザのうち使う部分（テストでは単語で区切る偽物を渡す）．"""

    def encode(self, text: str, add_special_tokens: bool = ...) -> list[int]:
        """文章をトークン ID の列にする．"""
        ...

    def decode(self, ids: list[int], skip_special_tokens: bool = ...) -> str:
        """トークン ID の列を文章に戻す．"""
        ...


def median_token_count(texts: Sequence[str], tokenizer: Tokenizer) -> int:
    """文章のトークン数（特殊トークンを除く）の中央値（切り上げ）．"""
    counts = [len(tokenizer.encode(t, add_special_tokens=False)) for t in texts]
    return int(np.ceil(np.median(counts)))


def truncate_to_tokens(texts: Sequence[str], tokenizer: Tokenizer, n_tokens: int) -> list[str]:
    """各文章を先頭の n_tokens トークンに切る（短い文章はそのまま）．"""
    out = []
    for t in texts:
        ids = tokenizer.encode(t, add_special_tokens=False)
        out.append(
            t
            if len(ids) <= n_tokens
            else tokenizer.decode(ids[:n_tokens], skip_special_tokens=True)
        )
    return out


def owner_metrics(scores: F32Array, owner: Sequence[int], seed: int) -> dict[str, Any]:
    """入力 1 組の（行：入力，列：受信箱）の採点から，持ち主の top-1・信頼区間・AUC を出す．"""
    ranks = ranks_of_gold(scores, owner)
    return {
        "n": len(owner),
        "top1": hit_at(ranks, 1),
        "top1_ci95": bootstrap_ci((ranks <= 1).astype(np.float64), seed),
        "auc": owner_auc(scores, owner),
    }


def compare_inputs(question: dict[str, Any], short: dict[str, Any]) -> str:
    """H-O1：同じ長さのメール本文（E_short）の top-1 が，質問文（Q）の top-1 より高いか（95% 区間が重ならないとき）．

    higher：E_short の区間が Q の区間より上（同じ長さでも，メール本文の方が持ち主を当てやすい）．
    overlap：区間が重なる（EQ2 の差は入力の長さで説明できる）．lower：E_short の区間が Q の区間より下．
    """
    q_lo, q_hi = question["top1_ci95"]
    s_lo, s_hi = short["top1_ci95"]
    if s_lo > q_hi:
        return "higher"
    if s_hi < q_lo:
        return "lower"
    return "overlap"


def regression_check(
    results: dict[str, dict[str, Any]], reference: dict[str, Any]
) -> dict[str, Any]:
    """Q の top-1 が EQ2 の hit@1，E の top-1・AUC が EQ2 の所有者 top-1・AUC と小数 3 桁で一致するか．"""
    diffs: dict[str, dict[str, float]] = {}
    for name in METHODS:
        ref = reference["test"][name]
        diffs[name] = {
            "Q_top1_vs_hit@1": results[name]["Q"]["top1"] - ref["hit@1"],
            "E_top1_vs_owner_top1": results[name]["E"]["top1"] - ref["owner_top1"],
            "E_auc_vs_owner_auc": results[name]["E"]["auc"] - ref["owner_auc"],
        }
    passed = all(abs(d) <= REGRESSION_TOLERANCE for per in diffs.values() for d in per.values())
    return {"tolerance": REGRESSION_TOLERANCE, "diff": diffs, "passed": passed}


def evaluate(cfg: AtriumConfig, paths: DatasetPaths, reference: dict[str, Any]) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．reference は EQ2 の metrics.json（退行の確認に使う）．"""
    from atrium.data_enronqa import read_gold
    from atrium.encoders import ArcticEncoder

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    inboxes = load_inboxes(cfg, paths)
    test = load_query_set(cfg, paths, "test")
    gold = read_gold(paths)
    test_gold_paths = {gold[q]["path"] for q in test.qids}
    probe_emb, probe_texts, probe_owner = owner_probes(cfg, inboxes, test_gold_paths)

    # E_short：test の質問のトークン数の中央値に，メール本文を先頭から切る
    encoder = ArcticEncoder(e.encoder, e.embed_batch_size)
    tokenizer = encoder.tokenizer
    n_tokens = median_token_count(test.texts, tokenizer)
    short_texts = truncate_to_tokens(probe_texts, tokenizer, n_tokens)
    short_emb = encoder.encode_texts(short_texts)
    logger.info("E_short: %d tokens (median of the %d test questions)", n_tokens, len(test.texts))

    params = {
        "card_sim": e.card_subjects,
        "term_sketch": e.term_sketch_size,
        "centroid_sim": 0,
        "multi_centroid": e.n_centroids,
    }
    methods = {m.name: m for m in build_methods(cfg, inboxes, paths) if m.name in METHODS}
    results: dict[str, dict[str, Any]] = {}
    for name in METHODS:
        method = methods[name]
        # Q と E は 1 回の呼び出しで採点する（質問の採点と抜き出したメールの採点）．E_short は質問を 1 件だけ渡して採点する
        q_scores, size, e_scores = method.run(
            params[name], test.emb, test.texts, probe_emb, probe_texts
        )
        _, _, s_scores = method.run(
            params[name], test.emb[:1], test.texts[:1], short_emb, short_texts
        )
        assert e_scores is not None and s_scores is not None
        entry: dict[str, Any] = {
            "param": params[name],
            "advert_bytes": size,
            "Q": owner_metrics(q_scores, test.gold, seed),
            "E": owner_metrics(e_scores, probe_owner, seed),
            "E_short": owner_metrics(s_scores, probe_owner, seed),
        }
        entry["h_o1"] = compare_inputs(entry["Q"], entry["E_short"])
        results[name] = entry
        logger.info(
            "%s: Q %.3f E %.3f E_short %.3f",
            name,
            entry["Q"]["top1"],
            entry["E"]["top1"],
            entry["E_short"]["top1"],
        )
    return {
        "n_inboxes": len(inboxes),
        "n_test": len(test.qids),
        "n_owner_probes": len(probe_owner),
        "short_tokens": n_tokens,
        "regression": regression_check(results, reference),
        "methods": results,
        "duration_s": time.perf_counter() - start,
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文．"""
    reg = metrics["regression"]
    lines = [
        "# T2：持ち主推定の定義の統一（同じ公開情報・同じ採点で，入力だけを変える）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個，質問（Q）{metrics['n_test']} 問，所有者推定用のメール（E・E_short）{metrics['n_owner_probes']} 通．"
        f"E_short は質問のトークン数の中央値 {metrics['short_tokens']} トークンに先頭から切った．",
        f"- 退行の確認（Q の top-1 = EQ2 の hit@1，E の top-1・AUC = EQ2 の所有者 top-1・AUC．小数 3 桁）：{'通過' if reg['passed'] else '不一致'}",
        "",
        "| 方式 | 公開情報の大きさ（バイト） | Q top-1 [95%CI] | E top-1 [95%CI] | E_short top-1 [95%CI] | Q AUC | E AUC | E_short AUC | H-O1（E_short と Q） |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    verdict = {
        "higher": "同じ長さでも，メール本文の方が持ち主を当てやすい",
        "overlap": "区間が重なる（EQ2 の差は入力の長さで説明できる）",
        "lower": "E_short の方が低い",
    }
    for name in METHODS:
        m = metrics["methods"][name]

        def cell(key: str, m: dict[str, Any] = m) -> str:
            x = m[key]
            return f"{x['top1']:.3f} [{x['top1_ci95'][0]:.3f}, {x['top1_ci95'][1]:.3f}]"

        lines.append(
            f"| {name} | {m['advert_bytes']:,} | {cell('Q')} | {cell('E')} | {cell('E_short')} | "
            f"{m['Q']['auc']:.3f} | {m['E']['auc']:.3f} | {m['E_short']['auc']:.3f} | {verdict[m['h_o1']]} |"
        )
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
