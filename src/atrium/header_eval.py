"""外部レビュー R1・R2（Iteration 2）：T2 の入力からヘッダーを除いた持ち主推定の測り直しと，質問文の人名による見つけやすさの集計．

`atrium header-eval` が制御点のコンテナで実行する（メールの本文と埋め込みは制御点にだけある）．実機のノードは使わない．
規則は journal の Iteration 2 の事前登録と同じで，コードに固定し，結果を見て変えない．

- R1 (b)：T2（`atrium owner-eval`）の短い入力 E_short（ヘッダー込みの先頭 28 トークン）を，区切り線より後の本文の
  先頭 28 トークン E_body_short（Subject は含めない）に変えて，同じ公開情報・同じ採点で持ち主の top-1 を比べる．
  参考として E_body（本文の全体）と E_subj_short（件名の値＋本文の先頭 28 トークン）も出す．本文が空のメールは，
  E_short との比較を含む全ての対の比較から除く．T2 との退行の確認は 1,460 通の全体で行う．
- R1 (a)：受信箱ごとの term_sketch の上位の語のうち，ヘッダーにしか多く現れない語（本文だけの文書頻度が
  ヘッダー込みの半分未満）の割合．
- R2：test の質問を，持ち主の姓を含む（A），アドレスの名前の語を含む（B），どちらでもない（C）に分け，群ごとの hit を出す．
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.advert_eval import (
    Inbox,
    build_methods,
    load_inboxes,
    load_query_set,
    owner_probes,
    ranks_of_gold,
    score_flood_dense,
)
from atrium.arrays import F32Array, I64Array
from atrium.cascade_eval import cascade_scores, hit_stats
from atrium.config import AtriumConfig
from atrium.data_enronqa import HEADER_SEPARATOR, MIN_TERM_LENGTH, term_sketch, tokens
from atrium.owner_eval import (
    METHODS,
    compare_inputs,
    median_token_count,
    owner_metrics,
    truncate_to_tokens,
)
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

# 退行の確認の許容．term_sketch の採点は文字列のハッシュの種で揺れる（backlog B8）ので，その方式だけ緩い
REGRESSION_TOLERANCE = 5e-4
TERM_SKETCH_TOLERANCE = 2e-3
# R1 (a)：上位の語の数と，ヘッダー依存の語とみなす本文の文書頻度の比，特徴語がヘッダーに占められるとみなす割合
TOP_TERMS_SHORT = 20
TOP_TERMS_LONG = 200
HEADER_DEPENDENT_RATIO = 0.5
HEADER_DOMINATED_SHARE = 0.25
# R2：群 B で一致した語の出力数
MATCHED_WORDS_SHOWN = 30
INPUT_KEYS = ("E_short", "E_body_short", "E_body", "E_subj_short")

_ADDRESS = re.compile(r"[\w.+-]+@[\w.-]+")
_CAPITALIZED_WORD = re.compile(r"\b[A-Z][A-Za-z0-9]*\b")


def split_header_body(text: str) -> tuple[str, str, bool]:
    """メールの文字列を（ヘッダー，本文，区切り線があったか）に分ける．区切り線が無ければ全体を本文とする．"""
    head, sep, body = text.partition(HEADER_SEPARATOR)
    if not sep:
        return "", text, False
    return head, body, True


def subject_value(header: str) -> str:
    """ヘッダーの `Subject:` の行の値（無ければ空）．"""
    for line in header.splitlines():
        if line.startswith("Subject:"):
            return line[len("Subject:") :].strip()
    return ""


def address_words(texts: Sequence[str]) -> tuple[set[str], set[str]]:
    """メールのヘッダーの `Sender:`・`Recipients:` のアドレスから，（ドメインの語，アドレスの名前の語）の集合を取る．

    ドメインは @ より後，名前は @ より前の語で，名前の語は MIN_TERM_LENGTH 文字以上・数字だけでない・英語の機能語でないものに限る．
    """
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    domains: set[str] = set()
    names: set[str] = set()
    for text in texts:
        header, _, _ = split_header_body(text)
        for line in header.splitlines():
            if not line.startswith(("Sender:", "Recipients:")):
                continue
            for address in _ADDRESS.findall(line):
                local, _, domain = address.partition("@")
                domains.update(tokens(domain))
                names.update(
                    t
                    for t in tokens(local)
                    if len(t) >= MIN_TERM_LENGTH and not t.isdigit() and t not in ENGLISH_STOP_WORDS
                )
    return domains, names


def owner_surname(inbox_name: str) -> str:
    """受信箱の名前（`lay-k`）の `-` より前（姓）を小文字で返す．"""
    return inbox_name.split("-", 1)[0].lower()


def classify_question(question: str, surname: str, name_words: set[str]) -> tuple[str, list[str]]:
    """質問を A（持ち主の姓を単語として含む）・B（A でなく，大文字で始まる語がアドレスの名前の語に一致）・C に分ける．

    B は一致した語（小文字）も返す．姓が MIN_TERM_LENGTH 文字未満なら A にならない．
    """
    if len(surname) >= MIN_TERM_LENGTH and re.search(
        rf"\b{re.escape(surname)}\b", question, re.IGNORECASE
    ):
        return "A", []
    matched = [w.lower() for w in _CAPITALIZED_WORD.findall(question) if w.lower() in name_words]
    return ("B", matched) if matched else ("C", [])


def group_hit_stats(
    ranks: I64Array, groups: Sequence[str], m: int, seed: int
) -> dict[str, dict[str, Any]]:
    """群（A・B・C・A+B）ごとの n・hit@1・hit@m と 95% 区間．空の群は n だけを返す．"""
    labels = np.asarray(groups)
    selections = {
        "A": labels == "A",
        "B": labels == "B",
        "C": labels == "C",
        "A+B": labels != "C",
    }
    return {
        name: hit_stats(ranks[mask], m, seed) if mask.any() else {"n": 0}
        for name, mask in selections.items()
    }


def weighted_total(stats: dict[str, dict[str, Any]], key: str) -> float:
    """A・B・C の値を問の数で重みづけして合わせた値（全 test の値と一致するはずである）．"""
    n_all = sum(stats[g]["n"] for g in "ABC")
    return float(sum(stats[g]["n"] * stats[g][key] for g in "ABC" if stats[g]["n"]) / n_all)


def named_easier(stats: dict[str, dict[str, Any]], m: int) -> bool:
    """H-R2：A+B の hit@m の区間の下端が C の区間の上端より上か（どちらかが空なら False）．"""
    if not stats["A+B"]["n"] or not stats["C"]["n"]:
        return False
    return bool(stats["A+B"][f"hit@{m}_ci95"][0] > stats["C"][f"hit@{m}_ci95"][1])


def judge_header_effect(
    short: dict[str, Any], body_short: dict[str, Any], question: dict[str, Any]
) -> dict[str, str]:
    """H-R1b：同じ部分集合の E_body_short の top-1 が E_short より区間として下か（支持・部分的に支持・棄却）．

    支持：E_body_short の区間が E_short の区間より下で，E_body_short と Q の H-O1 が「higher」でない．
    部分的に支持：同じく区間が下だが H-O1 は「higher」のまま．棄却：区間が重なる．
    E_body_short の区間が E_short より上の場合は事前登録の 3 区分のどれでもないので「above」とする．
    """
    h_o1 = compare_inputs(question, body_short)
    short_lo, short_hi = short["top1_ci95"]
    body_lo, body_hi = body_short["top1_ci95"]
    if body_hi < short_lo:
        verdict = "supported" if h_o1 != "higher" else "partially_supported"
    elif body_lo > short_hi:
        verdict = "above"
    else:
        verdict = "rejected"
    return {"h_r1b": verdict, "h_o1_body_short_vs_q": h_o1}


def regression_check_t2(
    results: dict[str, dict[str, Any]], t2: dict[str, Any], short_tokens: int
) -> dict[str, Any]:
    """1,460 通の全体の Q・E・E_short の top-1・AUC が，T2 の metrics.json と許容の範囲で一致するか．"""
    diffs: dict[str, dict[str, float]] = {}
    passed = short_tokens == t2["short_tokens"]
    for name in METHODS:
        tol = TERM_SKETCH_TOLERANCE if name == "term_sketch" else REGRESSION_TOLERANCE
        diffs[name] = {
            f"{inp}_{key}": results[name][inp][key] - t2["methods"][name][inp][key]
            for inp in ("Q", "E", "E_short")
            for key in ("top1", "auc")
        }
        passed = passed and all(abs(d) <= tol for d in diffs[name].values())
    return {
        "tolerance": REGRESSION_TOLERANCE,
        "tolerance_term_sketch": TERM_SKETCH_TOLERANCE,
        "short_tokens": short_tokens,
        "t2_short_tokens": t2["short_tokens"],
        "diff": diffs,
        "passed": bool(passed),
    }


def regression_check_r2(
    totals: dict[str, dict[str, float]], expected: dict[str, dict[str, float]]
) -> dict[str, Any]:
    """群の重みづけ和が，全 test の hit@1・hit@m と許容の範囲で一致するか．"""
    diff = {
        name: {key: totals[name][key] - value for key, value in per.items()}
        for name, per in expected.items()
    }
    passed = all(abs(d) <= REGRESSION_TOLERANCE for per in diff.values() for d in per.values())
    return {"tolerance": REGRESSION_TOLERANCE, "expected": expected, "diff": diff, "passed": passed}


def feature_term_stats(
    inboxes: Sequence[Inbox],
    sketch_size: int,
    domain_words: set[str],
    name_words: set[str],
) -> dict[str, Any]:
    """R1 (a)：受信箱ごとの term_sketch の上位語のうち，ヘッダー依存の語・ドメインの語・アドレスの名前の語の割合の平均．

    ヘッダー依存の語は，その受信箱で本文だけの文書頻度が，ヘッダー込みの文書頻度（スケッチの重み）の
    HEADER_DEPENDENT_RATIO 倍未満の語．語の重み（文書頻度の割合）で重みづけた割合も出す．
    """
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    shares: dict[int, dict[str, list[float]]] = {
        top: {"header_dependent": [], "domain": [], "address_name": [], "header_dependent_w": []}
        for top in (TOP_TERMS_SHORT, TOP_TERMS_LONG)
    }
    surname_in_top = 0
    for ib in inboxes:
        terms, weights = term_sketch(ib.texts, sketch_size)
        body_df: Counter[str] = Counter()
        for text in ib.texts:
            _, body, _ = split_header_body(text)
            body_df.update(
                {
                    t
                    for t in tokens(body)
                    if len(t) >= MIN_TERM_LENGTH and not t.isdigit() and t not in ENGLISH_STOP_WORDS
                }
            )
        n_docs = max(1, len(ib.texts))
        dependent = [
            body_df[t] / n_docs < HEADER_DEPENDENT_RATIO * w
            for t, w in zip(terms, weights, strict=True)
        ]
        if owner_surname(ib.name) in terms[:TOP_TERMS_SHORT]:
            surname_in_top += 1
        for top, per in shares.items():
            ts, ws, dep = terms[:top], weights[:top], dependent[:top]
            if not ts:
                continue
            per["header_dependent"].append(float(np.mean(dep)))
            per["domain"].append(float(np.mean([t in domain_words for t in ts])))
            per["address_name"].append(float(np.mean([t in name_words for t in ts])))
            per["header_dependent_w"].append(
                sum(w for w, d in zip(ws, dep, strict=True) if d) / sum(ws)
            )
    out: dict[str, Any] = {
        "dominated_threshold": HEADER_DOMINATED_SHARE,
        "header_dependent_ratio": HEADER_DEPENDENT_RATIO,
        "inboxes_with_surname_in_top20": surname_in_top,
        "n_inboxes": len(inboxes),
    }
    for top, per in shares.items():
        out[f"top{top}"] = {key: float(np.mean(v)) for key, v in per.items()}
    out["h_r1a_header_dominated"] = bool(
        out[f"top{TOP_TERMS_SHORT}"]["header_dependent"] >= HEADER_DOMINATED_SHARE
    )
    return out


def evaluate(cfg: AtriumConfig, paths: DatasetPaths, references: dict[str, Any]) -> dict[str, Any]:
    """手順は冒頭の説明のとおり．references は eq2・t2・cascade の各 metrics.json（退行の確認の基準）．"""
    from atrium.data_enronqa import read_gold
    from atrium.encoders import ArcticEncoder

    start = time.perf_counter()
    e = cfg.data.require_enronqa()
    seed = cfg.experiment.seed
    m = cfg.routing.top_m["enronqa"]
    inboxes = load_inboxes(cfg, paths)
    test = load_query_set(cfg, paths, "test")
    gold = read_gold(paths)
    test_gold_paths = {gold[q]["path"] for q in test.qids}
    probe_emb, probe_texts, probe_owner = owner_probes(cfg, inboxes, test_gold_paths)

    # R1 (b) の入力を作る（本文が空のメールは，対の比較の部分集合から除く）
    encoder = ArcticEncoder(e.encoder, e.embed_batch_size)
    tokenizer = encoder.tokenizer
    n_tokens = median_token_count(test.texts, tokenizer)
    splits = [split_header_body(t) for t in probe_texts]
    bodies = [b for _, b, _ in splits]
    body_lengths = [len(tokenizer.encode(b, add_special_tokens=False)) for b in bodies]
    keep = np.asarray([i for i, n in enumerate(body_lengths) if n > 0], dtype=np.int64)
    input_texts = {
        "E_short": truncate_to_tokens(probe_texts, tokenizer, n_tokens),
        "E_body_short": truncate_to_tokens(bodies, tokenizer, n_tokens),
        "E_body": bodies,
        "E_subj_short": truncate_to_tokens(
            [f"{subject_value(h)} {b}" for h, b, _ in splits], tokenizer, n_tokens
        ),
    }
    input_emb = {k: encoder.encode_texts(v) for k, v in input_texts.items()}
    logger.info(
        "short=%d tokens; empty bodies %d; no separator %d of %d",
        n_tokens,
        len(probe_texts) - len(keep),
        sum(not has for _, _, has in splits),
        len(probe_texts),
    )

    # 各方式の採点．Q と E は owner_eval と同じ 1 回の呼び出しに，追加の入力を続けて渡す（行ごとに独立に採点される）
    all_emb = np.concatenate([probe_emb, *(input_emb[k] for k in INPUT_KEYS)])
    all_texts = [*probe_texts, *(t for k in INPUT_KEYS for t in input_texts[k])]
    n_probe = len(probe_texts)
    params = {
        "card_sim": e.card_subjects,
        "term_sketch": e.term_sketch_size,
        "centroid_sim": 0,
        "multi_centroid": e.n_centroids,
    }
    methods = {x.name: x for x in build_methods(cfg, inboxes, paths) if x.name in METHODS}
    results: dict[str, dict[str, Any]] = {}
    question_scores: dict[str, F32Array] = {}
    for name in METHODS:
        q_scores, size, p_scores = methods[name].run(
            params[name], test.emb, test.texts, all_emb, all_texts
        )
        assert p_scores is not None
        question_scores[name] = q_scores
        rows = {"E": p_scores[:n_probe]}
        for i, key in enumerate(INPUT_KEYS, start=1):
            rows[key] = p_scores[i * n_probe : (i + 1) * n_probe]
        owner = np.asarray(probe_owner)
        entry: dict[str, Any] = {
            "param": params[name],
            "advert_bytes": size,
            "Q": owner_metrics(q_scores, test.gold, seed),
            # 全体（退行の確認用．T2 と同じ 1,460 通）
            "E": owner_metrics(rows["E"], probe_owner, seed),
            "E_short": owner_metrics(rows["E_short"], probe_owner, seed),
            # 本文が空でないメールだけの部分集合（対の比較）
            "subset": {
                key: owner_metrics(rows[key][keep], owner[keep].tolist(), seed)
                for key in INPUT_KEYS
            },
        }
        subset = entry["subset"]
        entry["judgement"] = judge_header_effect(
            subset["E_short"], subset["E_body_short"], entry["Q"]
        )
        entry["h_o1_e_short_vs_q"] = compare_inputs(entry["Q"], subset["E_short"])
        entry["delta_top1_body_short_minus_short"] = (
            subset["E_body_short"]["top1"] - subset["E_short"]["top1"]
        )
        results[name] = entry
        logger.info(
            "%s: Q %.3f E_short(all) %.3f | subset E_short %.3f E_body_short %.3f",
            name,
            entry["Q"]["top1"],
            entry["E_short"]["top1"],
            subset["E_short"]["top1"],
            subset["E_body_short"]["top1"],
        )
    regression_t2 = regression_check_t2(results, references["t2"], n_tokens)

    # R1 (a)：特徴語の集計
    domain_words, name_words = address_words([t for ib in inboxes for t in ib.texts])
    features = feature_term_stats(inboxes, e.term_sketch_size, domain_words, name_words)

    # R2：質問を 3 群に分けた hit（flood_score・multi_centroid・centroid_probe・参考に term_sketch）
    probe_k = cfg.routing.probe_k["enronqa"]
    flood = score_flood_dense(test.emb, inboxes)
    multi = question_scores["multi_centroid"]
    scores_by_method = {
        "flood_score": flood,
        "multi_centroid": multi,
        "centroid_probe": cascade_scores(multi, flood, probe_k),
        "term_sketch": question_scores["term_sketch"],
    }
    labels: list[str] = []
    matched: Counter[str] = Counter()
    for question, g in zip(test.texts, test.gold, strict=True):
        label, words = classify_question(question, owner_surname(inboxes[g].name), name_words)
        labels.append(label)
        matched.update(words)
    groups: dict[str, Any] = {}
    easier: dict[str, bool] = {}
    totals: dict[str, dict[str, float]] = {}
    for name, scores in scores_by_method.items():
        stats = group_hit_stats(ranks_of_gold(scores, test.gold), labels, m, seed)
        groups[name] = stats
        easier[name] = named_easier(stats, m)
        totals[name] = {f"hit@{k}": weighted_total(stats, f"hit@{k}") for k in (1, m)}
    expected = {
        "flood_score": {
            k: references["eq2"]["test"]["flood_score"][k] for k in ("hit@1", f"hit@{m}")
        },
        "multi_centroid": {
            k: references["eq2"]["test"]["multi_centroid"][k] for k in ("hit@1", f"hit@{m}")
        },
        "centroid_probe": {
            k: references["cascade"]["test"]["centroid_probe_chosen"][k]
            for k in ("hit@1", f"hit@{m}")
        },
    }
    regression_r2 = regression_check_r2(totals, expected)
    counts = Counter(labels)
    return {
        "n_inboxes": len(inboxes),
        "n_test": len(test.qids),
        "n_owner_probes": n_probe,
        "short_tokens": n_tokens,
        "m": m,
        "probe_k": probe_k,
        "n_empty_body": n_probe - len(keep),
        "n_subset": int(len(keep)),
        "n_no_separator": sum(not has for _, _, has in splits),
        "regression": {"t2": regression_t2, "r2": regression_r2},
        "methods": results,
        "feature_terms": features,
        "r2": {
            "group_sizes": {g: counts[g] for g in "ABC"},
            "matched_words_in_b": matched.most_common(MATCHED_WORDS_SHOWN),
            "groups": groups,
            "named_easier": easier,
            "weighted_totals": totals,
        },
        "duration_s": time.perf_counter() - start,
    }


def render_report(metrics: dict[str, Any]) -> str:
    """analysis_report.md の本文（本文は含めず，集計値と語だけを書く）．"""
    reg, m = metrics["regression"], metrics["m"]
    ft = metrics["feature_terms"]

    def cell(x: dict[str, Any]) -> str:
        return f"{x['top1']:.3f} [{x['top1_ci95'][0]:.3f}, {x['top1_ci95'][1]:.3f}]"

    lines = [
        "# Iteration 2：T2 の入力からヘッダーを除いた持ち主推定（R1）と，質問文の人名による見つけやすさ（R2）",
        "",
        f"- 受信箱 {metrics['n_inboxes']} 個，質問 {metrics['n_test']} 問，所有者推定用のメール {metrics['n_owner_probes']} 通．"
        f"短い入力は {metrics['short_tokens']} トークン．",
        f"- 本文が空のメール {metrics['n_empty_body']} 通を除いた部分集合 {metrics['n_subset']} 通で対の比較をする．区切り線の無いメール {metrics['n_no_separator']} 通（全体を本文とした）．",
        f"- 退行の確認（T2 の Q・E・E_short，1,460 通の全体）：{'一致' if reg['t2']['passed'] else '**不一致（判定に進まない）**'}",
        f"- 退行の確認（R2 の群の重みづけ和 = 全 test の hit）：{'一致' if reg['r2']['passed'] else '**不一致（判定に進まない）**'}",
        "",
        "## R1 (b)：持ち主の top-1 [95%CI]（部分集合．Q は全 test）",
        "",
        "| 方式 | Q | E_short | E_body_short | E_body | E_subj_short | 差（E_body_short − E_short） | H-R1b | H-O1（E_body_short と Q） |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in METHODS:
        r = metrics["methods"][name]
        s = r["subset"]
        lines.append(
            f"| {name} | {cell(r['Q'])} | {cell(s['E_short'])} | {cell(s['E_body_short'])} | {cell(s['E_body'])} | "
            f"{cell(s['E_subj_short'])} | {r['delta_top1_body_short_minus_short']:+.3f} | "
            f"{r['judgement']['h_r1b']} | {r['judgement']['h_o1_body_short_vs_q']} |"
        )
    t20, t200 = ft[f"top{TOP_TERMS_SHORT}"], ft[f"top{TOP_TERMS_LONG}"]
    lines += [
        "",
        "## R1 (a)：term_sketch の特徴語（受信箱ごとの上位語．150 受信箱の平均）",
        "",
        "| 上位 | ヘッダー依存の語 | 重みづけ | ドメインの語 | アドレスの名前の語 |",
        "|---|---|---|---|---|",
        f"| 20 | {t20['header_dependent']:.3f} | {t20['header_dependent_w']:.3f} | {t20['domain']:.3f} | {t20['address_name']:.3f} |",
        f"| 200 | {t200['header_dependent']:.3f} | {t200['header_dependent_w']:.3f} | {t200['domain']:.3f} | {t200['address_name']:.3f} |",
        "",
        f"- 持ち主の姓が上位 20 語に入る受信箱：{ft['inboxes_with_surname_in_top20']} / {ft['n_inboxes']}",
        f"- 上位 20 語のヘッダー依存の語の割合が {ft['dominated_threshold']} 以上：{ft['h_r1a_header_dominated']}",
        "",
        "## R2：質問の群ごとの hit（A：持ち主の姓，B：アドレスの名前の語，C：どちらでもない）",
        "",
        f"- 群の大きさ：{metrics['r2']['group_sizes']}",
        "",
        f"| 方式 | 群 | n | hit@1 [95%CI] | hit@{m} [95%CI] |",
        "|---|---|---|---|---|",
    ]
    for name, stats in metrics["r2"]["groups"].items():
        for g in ("A", "B", "C", "A+B"):
            s = stats[g]
            if not s["n"]:
                lines.append(f"| {name} | {g} | 0 | - | - |")
                continue
            c1, cm = s["hit@1_ci95"], s[f"hit@{m}_ci95"]
            lines.append(
                f"| {name} | {g} | {s['n']} | {s['hit@1']:.3f} [{c1[0]:.3f}, {c1[1]:.3f}] | "
                f"{s[f'hit@{m}']:.3f} [{cm[0]:.3f}, {cm[1]:.3f}] |"
            )
        lines.append(
            f"| {name} | 名前を含む問の方が見つけやすい | {metrics['r2']['named_easier'][name]} | | |"
        )
    lines += [
        "",
        "群 B で一致した語（上位）："
        + ", ".join(f"{w}({c})" for w, c in metrics["r2"]["matched_words_in_b"]),
    ]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, metrics: dict[str, Any]) -> None:
    """metrics.json と analysis_report.md を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    (out_dir / "analysis_report.md").write_text(render_report(metrics), encoding="utf-8")
