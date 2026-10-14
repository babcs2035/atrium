"""公開情報をヘッダーを除いた本文から作り直した RQ-A の測り直し（Iteration 3．backlog B14・B15）．

`atrium body-advert-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．
規則は journal の Iteration 3 の事前登録と同じで，コードに固定し，結果を見て変えない．

- 4 方式（card_sim・term_sketch・centroid_sim・multi_centroid）の公開情報を 2 通りで作る．`full` は既存の chunk と埋め込み（ヘッダー込み），
  `body_only` は区切り線より後の本文（Subject・Sender・Recipients を含まない）と，その本文を埋め込み直したものである．
  card_sim は件名だけの公開情報なので変わらず，公開情報を変えていないことの対照になる．
- 本文の埋め込みは全メールを文書側（接頭辞なし）で計算し，既存の保存と同じく fp16 に丸めてから float32 に戻す．データディレクトリには書かない．
- 同じ質問・同じ所有者推定のメールを同じ採点で通し，`body_only` − `full` の差を対にしたブートストラップの 95% 区間で示す．
- flood_score（既存のヘッダー込みの埋め込み）と，参考の centroid_probe（本文だけの multi_centroid を 1 段目にした二段）も出す．
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from atrium.advert_eval import (
    BOOTSTRAP_SAMPLES,
    CENTROID_CANDIDATES,
    TERM_CANDIDATES,
    Inbox,
    build_methods,
    hit_at,
    load_inboxes,
    load_query_set,
    owner_probes,
    ranks_of_gold,
    score_flood_dense,
    spearman,
)
from atrium.arrays import F32Array, F64Array, I64Array
from atrium.cascade_eval import cascade_scores, hit_stats
from atrium.config import AtriumConfig
from atrium.data_enronqa import card_text, term_sketch, tokens
from atrium.header_eval import classify_question, owner_surname, split_header_body
from atrium.owner_eval import (
    METHODS,
    Tokenizer,
    median_token_count,
    owner_metrics,
    truncate_to_tokens,
)
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

VARIANTS = ("full", "body_only")
# 所有者推定の入力：E はヘッダー込みのメール全体（EQ2 と同じ），残りは区切り線より後の本文（Iteration 2 と同じ）
OWNER_INPUTS = ("E", "E_body_short", "E_body")
# 退行の確認の許容（journal の事前登録）．term_sketch の採点は文字列のハッシュの種で揺れる（backlog B8）ので緩い
REGRESSION_TOLERANCE = 5e-4
TERM_SKETCH_TOLERANCE = 2e-3
# card_sim の body_only は full と同じ公開情報なので，ほぼ完全に一致するはずである（AUC は浮動小数点の丸めだけ許す）
CARD_IDENTICAL_TOLERANCE = 1e-6
# 判定の規則：非劣性の幅（hit@m の差の区間の下端がこれより上なら，ほぼ失わない）
NON_INFERIORITY_MARGIN = -0.03
# 公開情報に持ち主の姓が入るかを数える上位の語の数
TOP_TERMS_SHORT = 20
# centroid_probe の 1 段目と 2 段目の組．K は config の routing.probe_k.enronqa
GROUPS = ("all", "A", "non_A")


def paired_bootstrap_diff(
    treated: F64Array, control: F64Array, seed: int, n_samples: int = BOOTSTRAP_SAMPLES
) -> dict[str, Any]:
    """同じ問（またはメール）の組で，treated − control の平均差と，復元抽出（n_samples 回）の 95% 区間と区分を返す．

    区分：区間の上端 < 0 を lower，下端 > 0 を higher，それ以外を unclear とする（事前登録の規則）．
    """
    if treated.shape != control.shape or treated.ndim != 1 or treated.size == 0:
        raise ValueError("treated and control must be non-empty 1-D arrays of the same length")
    delta = treated - control
    rng = np.random.default_rng(seed)
    resampled = delta[rng.integers(0, delta.size, size=(n_samples, delta.size))].mean(axis=1)
    lo, hi = float(np.percentile(resampled, 2.5)), float(np.percentile(resampled, 97.5))
    verdict = "lower" if hi < 0 else "higher" if lo > 0 else "unclear"
    return {"diff": float(delta.mean()), "ci95": (lo, hi), "verdict": verdict}


def make_body_inboxes(
    inboxes: Sequence[Inbox], body_embeddings: Sequence[F32Array], keep: Sequence[Sequence[int]]
) -> list[Inbox]:
    """受信箱ごとに，本文が空でないメールだけの（本文，本文の埋め込み）を持つ Inbox を作る．件名・パスは元のまま（card_sim は変えない）．"""
    out = []
    for ib, emb, rows in zip(inboxes, body_embeddings, keep, strict=True):
        if len(rows) != emb.shape[0]:
            raise ValueError(f"{ib.name}: {len(rows)} kept bodies but {emb.shape[0]} embeddings")
        if not rows:
            raise ValueError(f"{ib.name}: every body is empty")
        bodies = [split_header_body(ib.texts[i])[1] for i in rows]
        out.append(replace(ib, texts=bodies, emb=emb))
    return out


def embed_bodies(
    inboxes: Sequence[Inbox], encoder: Any
) -> tuple[list[F32Array], list[list[int]], dict[str, int]]:
    """全メールの本文（区切り線より後）を埋め込み，受信箱ごとに（埋め込み，本文が空でないメールの行）と通数を返す．

    本文が空（空白だけ）のメールは埋め込まず，本文だけの公開情報の計算から除く．区切り線が無ければ全体を本文とする．
    埋め込みは既存の保存と同じく fp16 に丸めてから float32 に戻す．
    """
    texts: list[str] = []
    keep: list[list[int]] = []
    counts = {"n_emails": 0, "n_no_separator": 0, "n_empty_body": 0}
    for ib in inboxes:
        rows = []
        for i, text in enumerate(ib.texts):
            _, body, has_separator = split_header_body(text)
            counts["n_emails"] += 1
            counts["n_no_separator"] += int(not has_separator)
            if body.strip():
                rows.append(i)
                texts.append(body)
            else:
                counts["n_empty_body"] += 1
        keep.append(rows)
    flat = encoder.encode_texts(texts).astype(np.float16).astype(np.float32)
    parts = np.split(flat, np.cumsum([len(r) for r in keep])[:-1])
    return list(parts), keep, counts


def surname_in_public_info(
    inboxes: Sequence[Inbox], sketch_size: int, card_subjects: int
) -> dict[str, int]:
    """持ち主の姓（受信箱の名前の `-` より前）が，term_sketch の上位 20 語・全 T 語と card_sim の説明文に入る受信箱の数．"""
    counts = {"term_sketch_top20": 0, "term_sketch_all": 0, "card_sim": 0}
    for ib in inboxes:
        surname = owner_surname(ib.name)
        terms, _ = term_sketch(ib.texts, sketch_size)
        counts["term_sketch_top20"] += int(surname in terms[:TOP_TERMS_SHORT])
        counts["term_sketch_all"] += int(surname in terms)
        counts["card_sim"] += int(surname in tokens(card_text(ib.titles, card_subjects)))
    return counts


def hit_indicators(ranks: I64Array, k: int) -> F64Array:
    """問ごとの「正解が上位 k 個に入ったか」の 0/1．"""
    return (ranks <= k).astype(np.float64)


def judge_body_only(
    hit_diff: dict[str, Any], owner_diff_body_short: dict[str, Any]
) -> dict[str, Any]:
    """このイテレーションの結論（multi_centroid）：採用・部分的に支持・棄却．

    採用：hit@m の差の区間の下端が NON_INFERIORITY_MARGIN より上，かつ E_body_short の所有者 top-1 の差の区間の上端が 0 より下．
    部分的に支持：どちらか一方だけ．棄却：どちらでもない．
    """
    keeps_hit = bool(hit_diff["ci95"][0] > NON_INFERIORITY_MARGIN)
    hides_owner = bool(owner_diff_body_short["ci95"][1] < 0)
    verdict = (
        "adopt"
        if keeps_hit and hides_owner
        else "partial"
        if keeps_hit or hides_owner
        else "reject"
    )
    return {"keeps_hit": keeps_hit, "hides_owner": hides_owner, "verdict": verdict}


def judge_h_b1_term_sketch(diff_all: dict[str, Any], diff_non_a: dict[str, Any]) -> bool:
    """H-B1 の term_sketch の部分：全体は下がり（lower），かつ A 以外は差がはっきりしない（unclear）なら支持．"""
    return bool(diff_all["verdict"] == "lower" and diff_non_a["verdict"] == "unclear")


def judge_h_b3(curves: dict[str, Any]) -> dict[str, Any]:
    """H-A2 の再確認：term_sketch・multi_centroid（body_only）の大きさとの順位相関が hit@1・所有者 top-1 の両方で正か．

    card_sim は full と同じ値なので，EQ2 の曲線（呼び出し側が渡す `card_sim`）をそのまま使う．3 方式で成立すれば supported，
    どれでもなければ refuted，それ以外は partial．
    """
    per_method = {
        name: bool(c["spearman_size_hit"] > 0 and c["spearman_size_owner"] > 0)
        for name, c in curves.items()
    }
    verdict = (
        "supported"
        if all(per_method.values())
        else "refuted"
        if not any(per_method.values())
        else "partial"
    )
    return {"monotone": per_method, "verdict": verdict}


def regression_check(
    runs: dict[str, dict[str, Any]],
    probe_runs: dict[str, dict[str, Any]],
    references: dict[str, Any],
    probe_k: int,
    m: int,
    row_count_ok: bool,
    n_empty_body_in_probes: int,
) -> dict[str, Any]:
    """事前登録の退行の確認．各項目の（差，許容，通過）と全体の通過を返す．

    runs・probe_runs は `full` の結果．references は eq2・header（Iteration 2）・cascade の metrics.json．
    """
    items: list[dict[str, Any]] = []

    def add(label: str, got: float, expected: float, tol: float) -> None:
        items.append(
            {
                "label": label,
                "got": got,
                "expected": expected,
                "diff": got - expected,
                "tolerance": tol,
                "passed": abs(got - expected) <= tol,
            }
        )

    for name in METHODS:
        tol = TERM_SKETCH_TOLERANCE if name == "term_sketch" else REGRESSION_TOLERANCE
        eq2, hdr = references["eq2"]["test"][name], references["header"]["methods"][name]["subset"]
        run = runs[name]
        add(f"{name} full hit@1 vs EQ2", run["hit"]["all"]["hit@1"], eq2["hit@1"], tol)
        add(f"{name} full hit@{m} vs EQ2", run["hit"]["all"][f"hit@{m}"], eq2[f"hit@{m}"], tol)
        add(
            f"{name} full E top-1 vs EQ2", probe_runs[name]["E_all"]["top1"], eq2["owner_top1"], tol
        )
        add(f"{name} full E AUC vs EQ2", probe_runs[name]["E_all"]["auc"], eq2["owner_auc"], tol)
        add(
            f"{name} full E_body_short top-1 vs Iteration 2",
            probe_runs[name]["E_body_short"]["top1"],
            hdr["E_body_short"]["top1"],
            tol,
        )
    casc = references["cascade"]["test"]["centroid_probe_chosen"]
    probe = runs["centroid_probe"]["hit"]["all"]
    add(
        f"centroid_probe(K={probe_k}) full hit@1 vs Iteration 1",
        probe["hit@1"],
        casc["hit@1"],
        REGRESSION_TOLERANCE,
    )
    add(
        f"centroid_probe(K={probe_k}) full hit@{m} vs Iteration 1",
        probe[f"hit@{m}"],
        casc[f"hit@{m}"],
        REGRESSION_TOLERANCE,
    )
    return {
        "tolerance": REGRESSION_TOLERANCE,
        "tolerance_term_sketch": TERM_SKETCH_TOLERANCE,
        "items": items,
        "row_counts_match": row_count_ok,
        "n_empty_body_in_probes": n_empty_body_in_probes,
        "chosen_k_matches_probe_k": bool(references["cascade"]["chosen_k"] == probe_k),
        "passed": bool(
            row_count_ok
            and references["cascade"]["chosen_k"] == probe_k
            and all(i["passed"] for i in items)
        ),
    }


def card_identity_check(
    runs: dict[str, dict[str, dict[str, Any]]], probe_runs: dict[str, dict[str, dict[str, Any]]]
) -> dict[str, Any]:
    """card_sim の body_only の全ての値が full と一致するか（公開情報を変えていないことの確認）．"""
    diffs: dict[str, float] = {}
    for group in GROUPS:
        for key, value in runs["body_only"]["card_sim"]["hit"][group].items():
            if isinstance(value, float):
                diffs[f"hit {group} {key}"] = value - runs["full"]["card_sim"]["hit"][group][key]
    for inp in (*OWNER_INPUTS, "E_all"):
        for key in ("top1", "auc"):
            body = probe_runs["body_only"]["card_sim"][inp][key]
            diffs[f"owner {inp} {key}"] = body - probe_runs["full"]["card_sim"][inp][key]
    return {
        "tolerance": CARD_IDENTICAL_TOLERANCE,
        "max_abs_diff": max(abs(d) for d in diffs.values()),
        "passed": all(abs(d) <= CARD_IDENTICAL_TOLERANCE for d in diffs.values()),
    }


def evaluate(cfg: AtriumConfig, paths: DatasetPaths, references: dict[str, Any]) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．references は eq2・header・cascade の各 metrics.json（退行の確認の基準）．"""
    from atrium.data_enronqa import read_gold
    from atrium.encoders import ArcticEncoder

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    probe_k = cfg.routing.probe_k["enronqa"]
    full_inboxes = load_inboxes(cfg, paths)
    test = load_query_set(cfg, paths, "test")
    gold = read_gold(paths)
    test_gold_paths = {gold[q]["path"] for q in test.qids}
    probe_emb, probe_texts, probe_owner = owner_probes(cfg, full_inboxes, test_gold_paths)

    # 本文の埋め込み（全メール．保存しない）と，本文だけの受信箱
    encoder = ArcticEncoder(e.encoder, e.embed_batch_size)
    body_emb, keep_rows, counts = embed_bodies(full_inboxes, encoder)
    row_counts_ok = all(
        len(r) + sum(not split_header_body(t)[1].strip() for t in ib.texts) == ib.emb.shape[0]
        for ib, r in zip(full_inboxes, keep_rows, strict=True)
    )
    body_inboxes = make_body_inboxes(full_inboxes, body_emb, keep_rows)
    logger.info("bodies embedded: %s", counts)
    inbox_sets = {"full": full_inboxes, "body_only": body_inboxes}

    # 所有者推定の入力：E（ヘッダー込み），E_body_short・E_body（区切り線より後．Subject なし）
    tokenizer: Tokenizer = encoder.tokenizer
    n_tokens = median_token_count(test.texts, tokenizer)
    bodies = [split_header_body(t)[1] for t in probe_texts]
    owner_keep = np.asarray([i for i, b in enumerate(bodies) if b.strip()], dtype=np.int64)
    short_texts = truncate_to_tokens(bodies, tokenizer, n_tokens)
    input_texts = {"E": probe_texts, "E_body_short": short_texts, "E_body": bodies}
    input_emb = {
        "E": probe_emb,
        "E_body_short": encoder.encode_texts(short_texts),
        "E_body": encoder.encode_texts(bodies),
    }
    all_emb = np.concatenate([input_emb[k] for k in OWNER_INPUTS])
    all_texts = [t for k in OWNER_INPUTS for t in input_texts[k]]
    n_probe = len(probe_texts)
    owner = np.asarray(probe_owner)

    # 質問の群（A：持ち主の姓を含む．それ以外）
    is_a = np.asarray(
        [
            classify_question(q, owner_surname(full_inboxes[g].name), set())[0] == "A"
            for q, g in zip(test.texts, test.gold, strict=True)
        ]
    )
    group_masks = {"all": np.ones(len(is_a), dtype=bool), "A": is_a, "non_A": ~is_a}

    params = {
        "card_sim": e.card_subjects,
        "term_sketch": e.term_sketch_size,
        "centroid_sim": 0,
        "multi_centroid": e.n_centroids,
    }
    flood = score_flood_dense(
        test.emb, full_inboxes
    )  # probe（2 段目）はどちらもヘッダー込みの既存の埋め込み
    runs: dict[str, dict[str, dict[str, Any]]] = {}
    probe_runs: dict[str, dict[str, dict[str, Any]]] = {}
    rank_of: dict[str, dict[str, I64Array]] = {}
    owner_rank_of: dict[str, dict[str, dict[str, I64Array]]] = {}
    for variant, inboxes in inbox_sets.items():
        methods = {x.name: x for x in build_methods(cfg, inboxes, paths) if x.name in METHODS}
        runs[variant], probe_runs[variant] = {}, {}
        rank_of[variant], owner_rank_of[variant] = {}, {}
        first_stage: F32Array | None = None
        for name in METHODS:
            q_scores, size, p_scores = methods[name].run(
                params[name], test.emb, test.texts, all_emb, all_texts
            )
            assert p_scores is not None
            if name == "multi_centroid":
                first_stage = q_scores
            ranks = ranks_of_gold(q_scores, test.gold)
            rank_of[variant][name] = ranks
            runs[variant][name] = {
                "param": params[name],
                "advert_bytes": size,
                "hit": {g: hit_stats(ranks[mask], m, seed) for g, mask in group_masks.items()},
            }
            rows = {
                k: p_scores[i * n_probe : (i + 1) * n_probe] for i, k in enumerate(OWNER_INPUTS)
            }
            owner_rank_of[variant][name] = {
                k: ranks_of_gold(rows[k][owner_keep], owner[owner_keep].tolist())
                for k in OWNER_INPUTS
            }
            probe_runs[variant][name] = {
                "E_all": owner_metrics(rows["E"], probe_owner, seed),
                **{
                    k: owner_metrics(rows[k][owner_keep], owner[owner_keep].tolist(), seed)
                    for k in OWNER_INPUTS
                },
            }
            logger.info(
                "%s %s: hit@1 %.4f hit@%d %.4f E %.4f E_body_short %.4f",
                variant,
                name,
                runs[variant][name]["hit"]["all"]["hit@1"],
                m,
                runs[variant][name]["hit"]["all"][f"hit@{m}"],
                probe_runs[variant][name]["E"]["top1"],
                probe_runs[variant][name]["E_body_short"]["top1"],
            )
        # 参考：centroid_probe（1 段目：この方式の multi_centroid，2 段目：既存の flood_score）
        assert first_stage is not None
        probe_ranks = ranks_of_gold(cascade_scores(first_stage, flood, probe_k), test.gold)
        rank_of[variant]["centroid_probe"] = probe_ranks
        runs[variant]["centroid_probe"] = {
            "param": probe_k,
            "hit": {g: hit_stats(probe_ranks[mask], m, seed) for g, mask in group_masks.items()},
        }

    # body_only − full の対にしたブートストラップの差
    diffs: dict[str, dict[str, Any]] = {}
    for name in (*METHODS, "centroid_probe"):
        entry: dict[str, Any] = {"hit": {}, "owner": {}}
        for g, mask in group_masks.items():
            entry["hit"][g] = {
                f"hit@{k}": paired_bootstrap_diff(
                    hit_indicators(rank_of["body_only"][name][mask], k),
                    hit_indicators(rank_of["full"][name][mask], k),
                    seed,
                )
                for k in (1, m)
            }
        if name in METHODS:
            for inp in OWNER_INPUTS:
                entry["owner"][inp] = paired_bootstrap_diff(
                    hit_indicators(owner_rank_of["body_only"][name][inp], 1),
                    hit_indicators(owner_rank_of["full"][name][inp], 1),
                    seed,
                )
        diffs[name] = entry

    # H-A2 の曲線（body_only．hit@1・所有者 top-1（E）・公開情報の大きさ）
    body_methods = {x.name: x for x in build_methods(cfg, body_inboxes, paths) if x.name in METHODS}
    curves: dict[str, Any] = {}
    for name, candidates in (
        ("term_sketch", TERM_CANDIDATES),
        ("multi_centroid", CENTROID_CANDIDATES),
    ):
        points = []
        for c in candidates:
            q_scores, size, p_scores = body_methods[name].run(
                c, test.emb, test.texts, probe_emb, probe_texts
            )
            assert p_scores is not None
            points.append(
                {
                    "param": c,
                    "advert_bytes": size,
                    "hit@1": hit_at(ranks_of_gold(q_scores, test.gold), 1),
                    "owner_top1": hit_at(ranks_of_gold(p_scores, probe_owner), 1),
                }
            )
        sizes = [float(p["advert_bytes"]) for p in points]
        curves[name] = {
            "curve": points,
            "spearman_size_hit": spearman(sizes, [p["hit@1"] for p in points]),
            "spearman_size_owner": spearman(sizes, [p["owner_top1"] for p in points]),
        }

    # 判定の規則（事前登録）と退行の確認
    judgement = {
        "h_b1_term_sketch_supported": judge_h_b1_term_sketch(
            diffs["term_sketch"]["hit"]["all"][f"hit@{m}"],
            diffs["term_sketch"]["hit"]["non_A"][f"hit@{m}"],
        ),
        "h_b3": judge_h_b3(curves),
        "conclusion_multi_centroid": judge_body_only(
            diffs["multi_centroid"]["hit"]["all"][f"hit@{m}"],
            diffs["multi_centroid"]["owner"]["E_body_short"],
        ),
    }
    regression = regression_check(
        runs["full"],
        probe_runs["full"],
        references,
        probe_k,
        m,
        row_counts_ok,
        n_probe - len(owner_keep),
    )
    regression["card_sim_identical"] = card_identity_check(runs, probe_runs)
    regression["passed"] = bool(regression["passed"] and regression["card_sim_identical"]["passed"])

    surnames = {
        variant: surname_in_public_info(inboxes, e.term_sketch_size, e.card_subjects)
        for variant, inboxes in inbox_sets.items()
    }
    return {
        "n_inboxes": len(full_inboxes),
        "n_test": len(test.qids),
        "n_group_A": int(is_a.sum()),
        "n_owner_probes": n_probe,
        "n_owner_probes_nonempty": int(len(owner_keep)),
        "short_tokens": n_tokens,
        "m": m,
        "probe_k": probe_k,
        **counts,
        "n_empty_body_share": counts["n_empty_body"] / counts["n_emails"],
        "regression": regression,
        "variants": {v: {"methods": runs[v], "owner": probe_runs[v]} for v in VARIANTS},
        "diff_body_only_minus_full": diffs,
        "curves_body_only": curves,
        "surname_in_public_info": surnames,
        "judgement": judgement,
        "duration_s": time.perf_counter() - start,
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文（本文は含めず，集計値だけを書く）．"""
    m, reg = metrics["m"], metrics["regression"]
    v = metrics["variants"]
    d = metrics["diff_body_only_minus_full"]

    def ci(x: dict[str, Any], key: str) -> str:
        lo, hi = x[f"{key}_ci95"]
        return f"{x[key]:.3f} [{lo:.3f}, {hi:.3f}]"

    def diff_cell(x: dict[str, Any]) -> str:
        return f"{x['diff']:+.3f} [{x['ci95'][0]:+.3f}, {x['ci95'][1]:+.3f}] {x['verdict']}"

    lines = [
        "# Iteration 3：公開情報をヘッダーを除いた本文から作り直した RQ-A の測り直し",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個，質問 {metrics['n_test']} 問（A 群 {metrics['n_group_A']} 問），所有者推定のメール {metrics['n_owner_probes']} 通"
        f"（本文が空でない {metrics['n_owner_probes_nonempty']} 通で採点），m = {m}，centroid_probe の K = {metrics['probe_k']}．",
        f"- 全 {metrics['n_emails']} 通のうち，区切り線の無いメール {metrics['n_no_separator']} 通，本文が空のメール {metrics['n_empty_body']} 通"
        f"（{metrics['n_empty_body_share']:.4f}．本文だけの公開情報の計算から除いた）．",
        f"- 退行の確認：{'一致' if reg['passed'] else '**不一致（判定に進まない）**'}"
        f"（card_sim の body_only = full：{'一致' if reg['card_sim_identical']['passed'] else '不一致'}，"
        f"埋め込みの行数：{'一致' if reg['row_counts_match'] else '不一致'}）",
        *(
            f"  - 不一致：{i['label']} 差 {i['diff']:+.5f}（許容 {i['tolerance']}）"
            for i in reg["items"]
            if not i["passed"]
        ),
        "",
        f"## test の hit@1・hit@{m}（95%CI）と，body_only − full の差（対にしたブートストラップの 95% 区間）",
        "",
        f"| 方式 | 群 | full hit@1 | body_only hit@1 | 差 hit@1 | full hit@{m} | body_only hit@{m} | 差 hit@{m} |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in (*METHODS, "centroid_probe"):
        for g in ("all", "A", "non_A"):
            f, b, dd = (
                v["full"]["methods"][name]["hit"][g],
                v["body_only"]["methods"][name]["hit"][g],
                d[name]["hit"][g],
            )
            lines.append(
                f"| {name} | {g} | {ci(f, 'hit@1')} | {ci(b, 'hit@1')} | {diff_cell(dd['hit@1'])} | "
                f"{ci(f, f'hit@{m}')} | {ci(b, f'hit@{m}')} | {diff_cell(dd[f'hit@{m}'])} |"
            )
    lines += [
        "",
        "## 所有者の特定（top-1 [95%CI]，AUC．本文が空でないメール）と，body_only − full の差",
        "",
        "| 方式 | 入力 | full top-1 | body_only top-1 | 差 | full AUC | body_only AUC |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in METHODS:
        for inp in OWNER_INPUTS:
            f, b = v["full"]["owner"][name][inp], v["body_only"]["owner"][name][inp]
            lines.append(
                f"| {name} | {inp} | {ci(f, 'top1')} | {ci(b, 'top1')} | {diff_cell(d[name]['owner'][inp])} | "
                f"{f['auc']:.3f} | {b['auc']:.3f} |"
            )
    lines += [
        "",
        "## H-A2 の曲線（body_only．大きさ：公開情報のバイト数，hit@1，所有者 top-1（E））",
        "",
        "| 方式 | 候補ごとの（KB，hit@1，所有者 top-1） | 大きさと hit@1 の順位相関 | 大きさと所有者 top-1 の順位相関 |",
        "|---|---|---|---|",
    ]
    for name, c in metrics["curves_body_only"].items():
        points = "; ".join(
            f"{p['param']}: ({p['advert_bytes'] / 1024:.1f}, {p['hit@1']:.3f}, {p['owner_top1']:.3f})"
            for p in c["curve"]
        )
        lines.append(
            f"| {name} | {points} | {c['spearman_size_hit']:.2f} | {c['spearman_size_owner']:.2f} |"
        )
    sn = metrics["surname_in_public_info"]
    lines += [
        "",
        "## 公開情報に持ち主の姓が入る受信箱の数（150 受信箱中）",
        "",
        "| 公開情報 | term_sketch 上位 20 語 | term_sketch 全 T 語 | card_sim の説明文 |",
        "|---|---|---|---|",
        *(
            f"| {variant} | {sn[variant]['term_sketch_top20']} | {sn[variant]['term_sketch_all']} | {sn[variant]['card_sim']} |"
            for variant in VARIANTS
        ),
        "",
        "## 公開情報の大きさ（バイト）",
        "",
        "| 方式 | full | body_only |",
        "|---|---|---|",
        *(
            f"| {name} | {v['full']['methods'][name]['advert_bytes']:,} | {v['body_only']['methods'][name]['advert_bytes']:,} |"
            for name in METHODS
        ),
        "",
        "## 事前登録の規則による判定",
        "",
        f"- H-B1（term_sketch．全体は lower かつ A 以外は unclear）：{metrics['judgement']['h_b1_term_sketch_supported']}",
        f"- H-B3：{metrics['judgement']['h_b3']}",
        f"- 結論（multi_centroid）：{metrics['judgement']['conclusion_multi_centroid']}",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く（本文・本文の埋め込みは書かない）．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
