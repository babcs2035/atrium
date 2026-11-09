"""d0009 B2（Iteration 39）：cross-encoder の最大長を 8,192 から 1,024 にしたときの速さと選ぶ候補の一致の集計と，ゲート（G-F16・G-AT・G-TR・有効な実行）の判定．

b2l_chain.sh が呼ぶ．使い方（リポジトリの直下．GPU・実機は使わない．実行ごとの記録は results/d0009_b2_ml1024_<name>/，
集計は results/d0009_b2_scoring_cost_maxlen1024/）:
    uv run python scripts/d0009/b2l_verdict.py gf16 [--run results/d0009_b2_ml1024_l8kpass]
    uv run python scripts/d0009/b2l_verdict.py at                  # G-AT：l8kpass・l1kpass の healthz.json
    uv run python scripts/d0009/b2l_verdict.py tr                  # G-TR：切ることが効いているか（token_lengths.json が要る）
    uv run python scripts/d0009/b2l_verdict.py agree               # H-L1 の数値（l1kpass を l8kpass・表と比べる）
    uv run python scripts/d0009/b2l_verdict.py check --run <dir> --host wafl502 [--smoke]
    uv run python scripts/d0009/b2l_verdict.py orders              # ラウンドごとの最大長の順（種 39）
    uv run python scripts/d0009/b2l_verdict.py summary             # maxlen_summary.json・verdict.json・analysis_report.md
- gf16（G-F16）：新しいコードの対照の経路（引数なし＝最大長 8,192）の 1 周が，Iteration 38 の fp16 の 1 周と全 4,320 組で |差| ≤ 1e-3，
  R3′ と V2 の選択が全問で一致（同点は b2c_verdict.selection_changes と同じ扱いで別に数え，不一致に入れない）．
- at（G-AT）：両側の /healthz で dtype が float16，attention の実装が同じで，実際に効いている最大長が 8,192 と 1,024．
- tr（G-TR）：1,024 以下の組の l8kpass との |差| の中央値 ≤ 1e-4，かつ 1,024 を超える組で |差| > 1e-3 の組が 1 以上．
- agree（H-L1）：b2c_verdict の選択・判定の関数を再利用し，対照を l8kpass のダンプに替える．表（fp32）との比較は記述．
- check：実行の有効性（b2_verdict.check_run と同じ）．summary：H-L2 の数値と H-L1・H-L2 の機械的な判定（採否の評価は rc-evaluator が行う）．
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import statistics
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
from scipy.stats import spearmanr

from atrium.answer_select_eval import load_questions


def load_script(name: str) -> ModuleType:
    """scripts/ はパッケージではないので，同じディレクトリの兄弟のスクリプトをパスから読み込む．"""
    path = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


B2C = load_script("b2c_verdict.py")
B2 = B2C.B2

BASE = "d0009_b2_ml1024"
RESULTS = Path("results")
RESULT_DIR = RESULTS / "d0009_b2_scoring_cost_maxlen1024"
TOKEN_LENGTHS = RESULT_DIR / "token_lengths.json"
# Iteration 38 の fp16・最大長 8,192 の 1 周（G-F16 の基準）
FP16_PASS_ITER38 = RESULTS / "d0009_b2_fp16_f16pass"
# 対照を先に置く（種 39 の並べ替えの入力の順．b2c_verdict.DTYPES と同じ流儀）
MAX_LENGTHS = ("max8192", "max1024")
SHORT = {"max8192": "l8k", "max1024": "l1k"}
EXPECTED_MAX_LENGTH = {"max8192": 8192, "max1024": 1024}
ROUNDS = [1, 2, 3]
ORDER_SEED = 39
LENGTH_LIMIT = 1024
# G-F16・G-TR（事前登録）
GF16_TOLERANCE = 1e-3
TR_SHORT_MEDIAN_MAX = 1e-4
TR_LONG_MOVED_MIN_DIFF = 1e-3
# H-L2（事前登録）
FASTER_MIN_RATIO = 1.08
NOT_FASTER_MAX_RATIO = 1.03
FITTED_RATIO_EXPECTED = 1.147

ScoreKey = tuple[str, int, str]


def run_dir(name: str) -> Path:
    """実行の名前（l8kpass・l1kpass・smoke・r<round>_<l8k|l1k>）から結果のディレクトリを返す．"""
    return RESULTS / f"{BASE}_{name}"


def read_lengths(path: Path = TOKEN_LENGTHS) -> dict[ScoreKey, int]:
    """token_lengths.json → （qid，候補の番号，文書 ID）→ 組の長さ（トークン数）．"""
    rows = json.loads(path.read_text(encoding="utf-8"))["pairs"]
    return {(r["qid"], r["candidate"], r["doc_id"]): int(r["length"]) for r in rows}


def selection_report(
    new_dump: dict[str, dict[int, list[tuple[str, float]]]],
    base_dump: dict[str, dict[int, list[tuple[str, float]]]],
    questions: list[Any],
) -> dict[str, Any]:
    """new_dump の R3′・V2 の選択を base_dump と比べる（b2c_verdict の選択・食い違いの関数を再利用）．"""
    ordered_qids = [q.qid for q in questions]
    correct = {q.qid: q.correct for q in questions}

    def r3(
        dump: dict[str, dict[int, list[tuple[str, float]]]],
    ) -> tuple[dict[str, int], dict[str, list[float]]]:
        cand_max = {
            qid: [max(s for _, s in dump[qid][c]) for c in sorted(dump[qid])]
            for qid in ordered_qids
        }
        return {qid: B2C.pick_by_max(v) for qid, v in cand_max.items()}, cand_max

    base_r3, base_r3_scores = r3(base_dump)
    new_r3, _ = r3(new_dump)
    base_v2, base_v2_scores = B2C.v2_choices(
        questions, B2C.ce_features_of(base_dump), B2C.V2_SELECTOR
    )
    new_v2, _ = B2C.v2_choices(questions, B2C.ce_features_of(new_dump), B2C.V2_SELECTOR)
    return {
        "R3p": B2C.selection_changes(
            ordered_qids, base_r3, new_r3, base_r3_scores, B2C.R3_TIE_GAP, correct
        ),
        "V2": B2C.selection_changes(
            ordered_qids, base_v2, new_v2, base_v2_scores, B2C.V2_TIE_GAP, correct
        ),
    }


def load_labeled_questions(qids: set[str]) -> list[Any]:
    """H の質問（正誤の印つき）のうち，ダンプにあるもの（b2c_verdict.agreement と同じ読み込み）．"""
    return [
        q
        for q in load_questions(
            B2C.P1_RUN,
            need_labels=True,
            need_answer_features=False,
            verify_features=B2C.VERIFY_FEATURES,
        )
        if q.qid in qids
    ]


# ── G-F16 ────────────────────────────────────────────────────────────────────


def gate_f16(run: Path, reference: Path = FP16_PASS_ITER38) -> dict[str, Any]:
    """G-F16：引数なしの server（最大長 8,192）の 1 周が Iteration 38 の fp16 の 1 周と一致するか．gf16.json を書く．"""
    new_dump, ref_dump = (
        B2C.read_dump(run / "scores.jsonl"),
        B2C.read_dump(reference / "scores.jsonl"),
    )
    new, ref = B2C.flat_scores(new_dump), B2C.flat_scores(ref_dump)
    missing = [k for k in ref if k not in new]
    diffs = [abs(v - ref[k]) for k, v in new.items() if k in ref]
    questions = load_labeled_questions(set(new_dump))
    selection = selection_report(new_dump, ref_dump, questions)
    result = {
        "n_pairs": len(new),
        "n_pairs_missing_vs_reference": len(missing),
        "abs_diff_vs_iter38_fp16": B2C.describe_diffs(diffs),
        "selection_R3p": {k: v for k, v in selection["R3p"].items() if k != "tie_qids"},
        "selection_V2": {k: v for k, v in selection["V2"].items() if k != "tie_qids"},
        "tolerance": GF16_TOLERANCE,
    }
    result["pass"] = bool(
        len(new) == B2C.EXPECTED_PAIRS
        and not missing
        and diffs
        and max(diffs) <= GF16_TOLERANCE
        and selection["R3p"]["n_mismatch"] == 0
        and selection["V2"]["n_mismatch"] == 0
    )
    (run / "gf16.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


# ── G-AT ─────────────────────────────────────────────────────────────────────


def gate_at(h8k_path: Path, h1k_path: Path) -> dict[str, Any]:
    """G-AT：両側で dtype（float16）と attention の実装が同じで，実際に効いている最大長が 8,192 と 1,024．"""
    h8k, h1k = B2C.read_json(h8k_path), B2C.read_json(h1k_path)
    keys = ("dtype", "attn_implementation", "max_length")
    result: dict[str, Any] = {
        "max8192": {k: h8k.get(k) for k in keys},
        "max1024": {k: h1k.get(k) for k in keys},
    }
    result["pass"] = bool(
        h8k.get("dtype") == "float16"
        and h1k.get("dtype") == "float16"
        and h8k.get("attn_implementation") is not None
        and h8k.get("attn_implementation") == h1k.get("attn_implementation")
        and h8k.get("max_length") == EXPECTED_MAX_LENGTH["max8192"]
        and h1k.get("max_length") == EXPECTED_MAX_LENGTH["max1024"]
    )
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "gat.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


# ── G-TR ─────────────────────────────────────────────────────────────────────


def split_diffs_by_length(
    new: dict[ScoreKey, float], base: dict[ScoreKey, float], lengths: dict[ScoreKey, int]
) -> tuple[list[float], list[float]]:
    """組ごとの |差| を，長さが 1,024 以下の組と超える組に分ける．"""
    short = [abs(v - base[k]) for k, v in new.items() if k in base and lengths[k] <= LENGTH_LIMIT]
    long = [abs(v - base[k]) for k, v in new.items() if k in base and lengths[k] > LENGTH_LIMIT]
    return short, long


def decide_truncation_effect(short_diffs: list[float], long_diffs: list[float]) -> bool:
    """G-TR（事前登録）：1,024 以下の組の |差| の中央値 ≤ 1e-4，かつ超える組で |差| > 1e-3 が 1 組以上．"""
    return bool(
        short_diffs
        and statistics.median(short_diffs) <= TR_SHORT_MEDIAN_MAX
        and any(d > TR_LONG_MOVED_MIN_DIFF for d in long_diffs)
    )


def gate_tr(run_1k: Path, run_8k: Path, lengths: dict[ScoreKey, int]) -> dict[str, Any]:
    """G-TR：最大長 1,024 の 1 周の得点が，1,024 以下の組では動かず，超える組では動いている．gtr.json を書く．"""
    new, base = (
        B2C.flat_scores(B2C.read_dump(run_1k / "scores.jsonl")),
        B2C.flat_scores(B2C.read_dump(run_8k / "scores.jsonl")),
    )
    short, long = split_diffs_by_length(new, base, lengths)
    result = {
        "n_short_pairs": len(short),
        "n_long_pairs": len(long),
        "short_abs_diff": B2C.describe_diffs(short),
        "long_abs_diff": B2C.describe_diffs(long),
        "n_long_pairs_moved_over_1e-3": sum(d > TR_LONG_MOVED_MIN_DIFF for d in long),
        "pass": decide_truncation_effect(short, long),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "gtr.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


# ── H-L1 ─────────────────────────────────────────────────────────────────────


def agreement(
    new_run: Path,
    base_run: Path,
    lengths: dict[ScoreKey, int],
    table_path: Path = B2C.TABLE,
) -> dict[str, Any]:
    """H-L1 の数値：最大長 1,024 の 1 周を対照（同じ日の 8,192）と比べ，記述として fp32 の表とも比べる．fp16_agreement の流儀を再利用する．"""
    new_dump, base_dump = (
        B2C.read_dump(new_run / "scores.jsonl"),
        B2C.read_dump(base_run / "scores.jsonl"),
    )
    table = B2C.read_table(table_path)
    new, base = B2C.flat_scores(new_dump), B2C.flat_scores(base_dump)
    nonfinite = sum(not math.isfinite(v) for v in new.values())
    questions = load_labeled_questions(set(new_dump))
    vs_base = selection_report(new_dump, base_dump, questions)
    # 記述：fp32 の表を基準にした同じ数（fp16 と合わせた累積の効果）．表の得点を同じ並びのダンプにして同じ関数に通す
    table_dump = {
        qid: {c: [(d, table[(qid, d)]) for d, _ in pairs] for c, pairs in cands.items()}
        for qid, cands in new_dump.items()
    }
    vs_table = selection_report(new_dump, table_dump, questions)
    short, long = split_diffs_by_length(new, base, lengths)
    short_t, long_t = split_diffs_by_length(
        new, {k: table[(k[0], k[2])] for k in new if (k[0], k[2]) in table}, lengths
    )
    rhos: list[float] = []
    top1_same = top1_total = 0
    for qid in new_dump:
        new_vals = [s for c in sorted(new_dump[qid]) for _, s in new_dump[qid][c]]
        ref_vals = [table[(qid, d)] for c in sorted(new_dump[qid]) for d, _ in new_dump[qid][c]]
        rho = spearmanr(new_vals, ref_vals).statistic
        if math.isfinite(rho):
            rhos.append(float(rho))
        for c in new_dump[qid]:
            new_c = [s for _, s in new_dump[qid][c]]
            ref_c = [table[(qid, d)] for d, _ in new_dump[qid][c]]
            top1_same += int(np.argmax(new_c) == np.argmax(ref_c))
            top1_total += 1
    changed = sorted(set(vs_base["R3p"]["mismatch_qids"] + vs_base["V2"]["mismatch_qids"]))
    result: dict[str, Any] = {
        "n_pairs": len(new),
        "n_questions": len(new_dump),
        "n_questions_with_labels": len(questions),
        "n_nonfinite": nonfinite,
        "abs_diff_vs_l8k_same_day": {
            "all": B2C.describe_diffs(short + long),
            "len_le_1024": B2C.describe_diffs(short),
            "len_gt_1024": B2C.describe_diffs(long),
        },
        "abs_diff_vs_table": {
            "len_le_1024": B2C.describe_diffs(short_t),
            "len_gt_1024": B2C.describe_diffs(long_t),
        },
        "R3p_vs_l8k_same_day": vs_base["R3p"],
        "V2_vs_l8k_same_day": vs_base["V2"],
        "R3p_vs_table": vs_table["R3p"],
        "V2_vs_table": vs_table["V2"],
        "spearman_per_question_vs_table": {
            "n": len(rhos),
            "median": statistics.median(rhos) if rhos else None,
            "min": min(rhos) if rhos else None,
        },
        "top1_agreement_vs_table": top1_same / top1_total if top1_total else None,
        # 選択が変わった問の，候補の中の最長の組の長さ（記述）
        "changed_questions_max_pair_length": {
            q: max(lengths[(q, c, d)] for c in new_dump[q] for d, _ in new_dump[q][c])
            for q in changed
        },
    }
    result["H_L1"] = B2C.decide_agreement(
        nonfinite,
        vs_base["R3p"]["n_mismatch"],
        vs_base["V2"]["n_mismatch"],
        vs_base["R3p"]["delta_correct"],
        vs_base["V2"]["delta_correct"],
    )
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "maxlen_agreement.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return result


# ── 速さ（H-L2）─────────────────────────────────────────────────────────────


def decide_speed(ratios: list[float]) -> str:
    """H-L2（事前登録）：有効なラウンドの全てで S ≥ 1.08 なら faster，全てで S < 1.03 なら not_faster，それ以外（ラウンドが無いときも）は undetermined．"""
    if ratios and all(r >= FASTER_MIN_RATIO for r in ratios):
        return "faster"
    if ratios and all(r < NOT_FASTER_MAX_RATIO for r in ratios):
        return "not_faster"
    return "undetermined"


def round_orders(seed: int = ORDER_SEED) -> list[list[str]]:
    """ラウンドごとの最大長の順（種 39 の無作為な並べ替え．1 つの乱数列から続けて並べ替える）．"""
    rng = random.Random(seed)
    orders = []
    for _ in ROUNDS:
        order = list(MAX_LENGTHS)
        rng.shuffle(order)
        orders.append(order)
    return orders


def one_pass_intervals(run: Path, lengths: dict[ScoreKey, int]) -> dict[str, float | int]:
    """記述：1 周の要求の終わった時刻の間隔（GPU の上の要求ごとの時間）の和を，1,024 を超える組を含む要求とそれ以外に分けて出す．"""
    rows = [json.loads(line) for line in (run / "requests.jsonl").open(encoding="utf-8")]
    rows = sorted((r for r in rows if r["error"] is None), key=lambda r: r["done_at"])
    long_requests = {(qid, c) for (qid, c, _), n in lengths.items() if n > LENGTH_LIMIT}
    total = long_total = other_total = 0.0
    n_long = 0
    # 最初の要求は前の終わりが無いので，2 番目から間隔を数える
    for prev, cur in zip(rows, rows[1:], strict=False):
        gap = cur["done_at"] - prev["done_at"]
        total += gap
        if (cur["qid"], cur["candidate"]) in long_requests:
            long_total += gap
            n_long += 1
        else:
            other_total += gap
    return {
        "n_intervals": max(len(rows) - 1, 0),
        "sum_s": total,
        "sum_s_requests_with_over_1024_pair": long_total,
        "n_requests_with_over_1024_pair": n_long,
        "sum_s_other_requests": other_total,
    }


def prod_prefix() -> str:
    """本番の実行名の前置き（機を wafl503 に替えた流し直しは `w503_`．chain が書く．無ければ空）．"""
    path = RESULT_DIR / "prod_prefix.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def load_rounds(host: str) -> tuple[dict[int, dict[str, dict[str, Any]]], dict[str, list[str]]]:
    """ラウンド → 最大長 → 有効な実行の集計（再実行があれば最初の有効なもの）と，無効だった実行の理由．"""
    runs: dict[int, dict[str, dict[str, Any]]] = {}
    invalid: dict[str, list[str]] = {}
    prefix = prod_prefix()
    for rnd in ROUNDS:
        for label in MAX_LENGTHS:
            for suffix in ("", "_retry"):
                path = run_dir(f"{prefix}r{rnd}_{SHORT[label]}{suffix}")
                if not (path / "valid.json").exists():
                    continue
                valid = B2C.read_json(path / "valid.json")
                if not valid["valid"]:
                    invalid[path.name] = valid["reasons"]
                    continue
                if label not in runs.setdefault(rnd, {}):
                    runs[rnd][label] = B2C.describe_run(path, host)
    return runs, invalid


def summarize() -> dict[str, Any]:
    """H-L2 の数値と，H-L1・H-L2 の機械的な判定．verdict.json・maxlen_summary.json・analysis_report.md を書く．"""
    host = (RESULT_DIR / "host.txt").read_text(encoding="utf-8").split()[0]
    runs, invalid = load_rounds(host)
    valid_rounds = [r for r in ROUNDS if set(runs.get(r, {})) == set(MAX_LENGTHS)]
    ratios = {
        str(r): runs[r]["max1024"]["x_req_qps"] / runs[r]["max8192"]["x_req_qps"]
        for r in valid_rounds
    }
    h_l2 = decide_speed(list(ratios.values()))
    agree = B2C.read_json(RESULT_DIR / "maxlen_agreement.json")
    gates = {
        "G_F16": B2C.read_json(run_dir("l8kpass") / "gf16.json")["pass"],
        "G_AT": B2C.read_json(RESULT_DIR / "gat.json")["pass"],
        "G_TR": B2C.read_json(RESULT_DIR / "gtr.json")["pass"],
        "smoke": B2C.read_json(run_dir("smoke") / "valid.json")["valid"],
    }
    gates_passed = all(gates.values())
    enough_rounds = len(valid_rounds) >= 2
    lengths = read_lengths()
    intervals = {
        label: one_pass_intervals(run_dir(f"{SHORT[label]}pass"), lengths) for label in MAX_LENGTHS
    }
    summary = {
        "host": host,
        "runs": {str(r): v for r, v in sorted(runs.items())},
        "valid_rounds": valid_rounds,
        "S_by_round": ratios,
        "S_median": statistics.median(ratios.values()) if ratios else None,
        "S_fitted_expectation": FITTED_RATIO_EXPECTED,
        "S_median_over_fitted_expectation": statistics.median(ratios.values())
        / FITTED_RATIO_EXPECTED
        if ratios
        else None,
        "invalid_runs": invalid,
        "gates": gates,
        "success_condition_1_gates_passed": gates_passed,
        "success_condition_2_enough_valid_rounds": enough_rounds,
        "H_L1": agree["H_L1"],
        "H_L2": h_l2,
        "H_L2_thresholds": {"faster": FASTER_MIN_RATIO, "not_faster": NOT_FASTER_MAX_RATIO},
        "one_pass_intervals": intervals,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "maxlen_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    mechanical = "undetermined_not_closed"
    if gates_passed and enough_rounds:
        if h_l2 == "faster" and agree["H_L1"] == "agree":
            mechanical = "adopt"
        elif h_l2 == "not_faster" or agree["H_L1"] == "disagree":
            mechanical = "reject"
        else:
            mechanical = "undetermined_closed_as_description"
    verdict = {
        "H_L1": agree["H_L1"],
        "H_L2": h_l2,
        "success_condition_1_gates_passed": gates_passed,
        "success_condition_2_enough_valid_rounds": enough_rounds,
        "mechanical_outcome": mechanical,
        "note": "mechanical application of the pre-registered rules; adoption is decided by rc-evaluator",
    }
    (RESULT_DIR / "verdict.json").write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    (RESULT_DIR / "analysis_report.md").write_text(render_report(summary, agree), encoding="utf-8")
    return summary


def render_report(summary: dict[str, Any], agree: dict[str, Any]) -> str:
    """X_req の表と一致の数値（Markdown．機械集計．採否は評価の担当）．"""
    lines = [
        "# d0009 B2 採点の最大長 1,024（機械集計．採否は評価の担当）",
        "",
        f"機: {summary['host']}　有効なラウンド: {summary['valid_rounds']}　ゲート: {json.dumps(summary['gates'])}",
        "",
        "| ラウンド | X_req 8,192 | X_req 1,024 | S | 温度の最大 | GPU メモリの最大 MiB |",
        "|---|---|---|---|---|---|",
    ]
    for r in summary["valid_rounds"]:
        a, b = summary["runs"][str(r)]["max8192"], summary["runs"][str(r)]["max1024"]
        lines.append(
            f"| {r} | {a['x_req_qps']:.3f} | {b['x_req_qps']:.3f} | {summary['S_by_round'][str(r)]:.3f} | "
            f"{a['gpu_temp_max_c']}/{b['gpu_temp_max_c']} | {a['gpu_mem_max_mib']}/{b['gpu_mem_max_mib']} |"
        )
    r3, v2 = agree["R3p_vs_l8k_same_day"], agree["V2_vs_l8k_same_day"]
    lines += [
        "",
        f"H-L2: {summary['H_L2']}　H-L1: {summary['H_L1']}　S の中央値/当てはめの見込み 1.147: {summary['S_median_over_fitted_expectation']}",
        f"非有限: {agree['n_nonfinite']}　|差|（l8k）: {json.dumps(agree['abs_diff_vs_l8k_same_day'])}",
        f"R3′: 食い違い {r3['n_mismatch']}（同点 {r3['n_mismatch_tie']}）　Δ正解 {r3['delta_correct']}",
        f"V2: 食い違い {v2['n_mismatch']}（同点 {v2['n_mismatch_tie']}）　Δ正解 {v2['delta_correct']}",
        f"無効になった実行: {json.dumps(summary['invalid_runs'], ensure_ascii=False)}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    """サブコマンドを実行し，結果を標準出力へ JSON で出す．gf16・at・tr・check は不合格のとき終了コード 1．"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("gf16")
    p.add_argument("--run", default=str(run_dir("l8kpass")))
    for name in ("at", "tr", "agree", "orders", "summary"):
        sub.add_parser(name)
    p = sub.add_parser("check")
    p.add_argument("--run", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.cmd == "gf16":
        result = gate_f16(Path(args.run))
    elif args.cmd == "at":
        result = gate_at(run_dir("l8kpass") / "healthz.json", run_dir("l1kpass") / "healthz.json")
    elif args.cmd == "tr":
        result = gate_tr(run_dir("l1kpass"), run_dir("l8kpass"), read_lengths())
    elif args.cmd == "agree":
        result = agreement(run_dir("l1kpass"), run_dir("l8kpass"), read_lengths())
        print(json.dumps(result, indent=1)[:6000])
        return 0
    elif args.cmd == "check":
        result = B2.check_run(Path(args.run), [args.host], args.smoke)
        print(json.dumps({"valid": result["valid"], "reasons": result["reasons"]}))
        return 0 if result["valid"] else 1
    elif args.cmd == "orders":
        for order in round_orders():
            print(" ".join(order))
        return 0
    else:
        print(json.dumps(summarize(), indent=1)[:4000])
        return 0
    print(json.dumps(result, indent=1))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
