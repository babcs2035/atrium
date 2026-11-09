"""d0009 B2（Iteration 38）：cross-encoder の採点を fp16 にしたときの速さと選ぶ候補の一致の集計と，ゲート（G-F32・G-AT・有効な実行）の判定．

b2c_chain.sh が呼ぶ．使い方（リポジトリの直下．GPU・実機は使わない．実行ごとの記録は results/d0009_b2_fp16_<name>/，
集計は results/d0009_b2_scoring_cost_fp16/）:
    uv run python scripts/d0009/b2c_verdict.py gf32 [--run results/d0009_b2_fp16_f32pass]
    uv run python scripts/d0009/b2c_verdict.py at                  # G-AT：healthz_float32.json・healthz_float16.json（fp16 の 1 周の記録）
    uv run python scripts/d0009/b2c_verdict.py agree               # H-F1 の数値（fp16 の 1 周と表・fp32 の 1 周の比較）
    uv run python scripts/d0009/b2c_verdict.py check --run <dir> --host wafl502 [--smoke]
    uv run python scripts/d0009/b2c_verdict.py orders              # ラウンドごとの dtype の順（種 38）
    uv run python scripts/d0009/b2c_verdict.py summary             # fp16_summary.json・verdict.json・analysis_report.md
- gf32（G-F32）：fp32 の 1 周が，全 4,320 組で表 `ce_table/holdout_owner.jsonl` と |差| ≤ 1e-3，R3′ の選択が同点を除いて全問で一致
  （b2_verdict.gate_gs と同じ判定），かつダンプから作り直した ce_max・ce_top1 が `results/d0009_a3_p1/ce_features.jsonl` と |差| ≤ 1e-3．
- at（G-AT）：両 dtype の server の /healthz で attention の実装と tokenizer の最大長が同じ．
- agree（H-F1）：fp16 の得点と表の差，非有限の数，R3′・V2 の選択の食い違い（同点を除く）と正誤の純の変化．
- check：実行の有効性（b2_verdict.check_run と同じ）．summary：H-F2 の数値と，H-F1・H-F2 の機械的な判定（採否の評価は rc-evaluator が行う）．
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
from scipy.stats import spearmanr

from atrium.answer_select_eval import (
    FEATURE_NAMES,
    TIE_TOLERANCE,
    QuestionCandidates,
    choose_index,
    load_questions,
    load_selector,
)

BASE = "d0009_b2_fp16"
RESULTS = Path("results")
RESULT_DIR = RESULTS / "d0009_b2_scoring_cost_fp16"
DTYPES = ("float32", "float16")
ROUNDS = [1, 2, 3]
ORDER_SEED = 38
REQUESTS_PER_QUESTION = 3
TABLE = Path("artifacts/enronqa/ce_table/holdout_owner.jsonl")
CE_FEATURES = Path("results/d0009_a3_p1/ce_features.jsonl")
P1_RUN = Path("results/d0009_a3_p1")
VERIFY_FEATURES = Path("results/d0009_b3v_h/verify_features.jsonl")
V2_SELECTOR = Path("results/d0009_b3_verify/answer_verifier_selector.json")
EFFICIENCY_SOURCE = RESULTS / "d0009_b2_split_scoring" / "b2_summary.json"
FEATURE_TOLERANCE = 1e-3
# H-F1（事前登録）
R3_TIE_GAP = 2e-3
V2_TIE_GAP = TIE_TOLERANCE
AGREE_MAX_MISMATCH = 2
AGREE_MIN_DELTA_CORRECT = -1
DISAGREE_MIN_MISMATCH = 6
DISAGREE_MAX_DELTA_CORRECT = -3
# H-F2（事前登録）
FASTER_MIN_RATIO = 1.5
NOT_FASTER_MAX_RATIO = 1.15
P2P_THROUGHPUT_QPS = 1.32
GPUS_TOTAL = 10
EXPECTED_PAIRS = 4320
PAIRS_PER_CANDIDATE = 5


def load_script(name: str) -> ModuleType:
    """scripts/ はパッケージではないので，同じディレクトリの兄弟のスクリプトをパスから読み込む．"""
    path = Path(__file__).resolve().parent / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


B2 = load_script("b2_verdict.py")
WB = load_script("b2_window_bias.py")

ScoreKey = tuple[str, int, str]


def run_dir(name: str) -> Path:
    """実行の名前（f32pass・f16pass・smoke・r<round>_<dtype>）から結果のディレクトリを返す．"""
    return RESULTS / f"{BASE}_{name}"


def short(dtype: str) -> str:
    """float32 → f32，float16 → f16（実行名に使う）．"""
    return "f32" if dtype == "float32" else "f16"


def read_json(path: Path) -> Any:
    """JSON を読む．"""
    return json.loads(path.read_text(encoding="utf-8"))


def read_dump(path: Path) -> dict[str, dict[int, list[tuple[str, float]]]]:
    """scores.jsonl → 質問 ID → 候補の番号 → （文書 ID，得点）の並び（文脈の順．書き出した順）．"""
    out: dict[str, dict[int, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(list))
    with path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            out[row["qid"]][row["candidate"]].append((row["doc_id"], float(row["score"])))
    return out


def read_table(path: Path) -> dict[tuple[str, str], float]:
    """表（qid・doc_id → 得点）．"""
    with path.open(encoding="utf-8") as f:
        return {(r["qid"], r["doc_id"]): float(r["score"]) for r in map(json.loads, f)}


def flat_scores(dump: dict[str, dict[int, list[tuple[str, float]]]]) -> dict[ScoreKey, float]:
    """ダンプを（qid，候補の番号，文書 ID）→ 得点にする（同じ文書 ID は候補の中に 1 つ）．"""
    return {
        (qid, ci, doc_id): score
        for qid, cands in dump.items()
        for ci, pairs in cands.items()
        for doc_id, score in pairs
    }


def ce_features_of(
    dump: dict[str, dict[int, list[tuple[str, float]]]],
) -> dict[tuple[str, int], tuple[float, float]]:
    """ダンプから作り直した（qid，候補の番号）→（ce_max，ce_top1）．ce_top1 は文脈の先頭（内積が最高）の 1 通の得点．"""
    return {
        (qid, ci): (max(s for _, s in pairs), pairs[0][1])
        for qid, cands in dump.items()
        for ci, pairs in cands.items()
    }


def describe_diffs(diffs: list[float]) -> dict[str, float | int | None]:
    """|差| の中央値・p99・最大（非有限は除いた値と，除いた数）．"""
    arr = np.asarray(diffs, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    if not finite.size:
        return {
            "n": int(arr.size),
            "median": None,
            "p99": None,
            "max": None,
            "n_nonfinite": int(arr.size),
        }
    return {
        "n": int(arr.size),
        "median": float(np.median(finite)),
        "p99": float(np.percentile(finite, 99)),
        "max": float(finite.max()),
        "n_nonfinite": int(arr.size - finite.size),
    }


# ── G-F32 ────────────────────────────────────────────────────────────────────


def gate_f32(run: Path, table_path: Path = TABLE) -> dict[str, Any]:
    """G-F32：新しいコードの fp32 の経路の退行の確かめ．gs.json（表との一致・R3′ の選択）と，ce_max・ce_top1 の作り直しの一致．"""
    gs = B2.gate_gs(run, table_path)
    stored: dict[tuple[str, int], tuple[float, float]] = {}
    with CE_FEATURES.open(encoding="utf-8") as f:
        for row in map(json.loads, f):
            if row["ce_max"] is not None:
                stored[(row["qid"], int(row["index"]))] = (
                    float(row["ce_max"]),
                    float(row["ce_top1"]),
                )
    rebuilt = ce_features_of(read_dump(run / "scores.jsonl"))
    missing = [k for k in rebuilt if k not in stored]
    diffs_max = [abs(v[0] - stored[k][0]) for k, v in rebuilt.items() if k in stored]
    diffs_top1 = [abs(v[1] - stored[k][1]) for k, v in rebuilt.items() if k in stored]
    result = {
        "gs": {k: v for k, v in gs.items() if k != "selection_mismatches"},
        "n_candidates_rebuilt": len(rebuilt),
        "n_candidates_missing_in_ce_features": len(missing),
        "ce_max_abs_diff": describe_diffs(diffs_max),
        "ce_top1_abs_diff": describe_diffs(diffs_top1),
        "feature_tolerance": FEATURE_TOLERANCE,
    }
    result["pass"] = bool(
        gs["pass"]
        and not missing
        and diffs_max
        and max(diffs_max) <= FEATURE_TOLERANCE
        and max(diffs_top1) <= FEATURE_TOLERANCE
    )
    (run / "gf32.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


# ── G-AT ─────────────────────────────────────────────────────────────────────


def gate_at(f32_path: Path, f16_path: Path) -> dict[str, Any]:
    """G-AT：両 dtype で attention の実装と tokenizer の最大長が同じ（かつ dtype が期待どおり）．"""
    h32, h16 = read_json(f32_path), read_json(f16_path)
    keys = ("attn_implementation", "tokenizer_max_length")
    result: dict[str, Any] = {
        "float32": {k: h32.get(k) for k in ("dtype", *keys)},
        "float16": {k: h16.get(k) for k in ("dtype", *keys)},
    }
    result["pass"] = bool(
        h32.get("dtype") == "float32"
        and h16.get("dtype") == "float16"
        and all(h32.get(k) is not None and h32.get(k) == h16.get(k) for k in keys)
    )
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "gat.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


# ── H-F1 ─────────────────────────────────────────────────────────────────────


def pick_by_max(candidate_max: list[float]) -> int:
    """候補ごとの最大の最高（同点は番号の先．choose_cross_encoder と同じ）．"""
    return max(range(len(candidate_max)), key=lambda i: (candidate_max[i], -i))


def top2_gap(values: list[float]) -> float:
    """上位 2 つの差（候補が 1 つなら無限大）．"""
    ordered = sorted(values, reverse=True)
    return ordered[0] - ordered[1] if len(ordered) > 1 else float("inf")


def selection_changes(
    qids: list[str],
    base_choice: dict[str, int],
    new_choice: dict[str, int],
    base_scores: dict[str, list[float]],
    tie_gap: float,
    correct: dict[str, tuple[bool, ...]],
) -> dict[str, Any]:
    """基準と新しい選択の食い違い．基準の得点の上位 2 候補の差が tie_gap 未満の問は同点として別に数える．

    delta_correct は選択が変わった全ての問（同点を含む）の，正解（候補の印）の数の純の変化．
    """
    changed = [q for q in qids if base_choice[q] != new_choice[q]]
    ties = [q for q in changed if top2_gap(base_scores[q]) < tie_gap]
    real = [q for q in changed if q not in ties]

    def delta(group: list[str]) -> int:
        return sum(int(correct[q][new_choice[q]]) - int(correct[q][base_choice[q]]) for q in group)

    return {
        "n_mismatch": len(real),
        "n_mismatch_tie": len(ties),
        "delta_correct": delta(changed),
        "delta_correct_excluding_ties": delta(real),
        "mismatch_qids": real,
        "tie_qids": ties,
    }


def decide_agreement(n_nonfinite: int, n_r3: int, n_v2: int, delta_r3: int, delta_v2: int) -> str:
    """H-F1（事前登録）：「一致する」「一致しない」「決まらない」．"""
    if (
        n_nonfinite >= 1
        or max(n_r3, n_v2) >= DISAGREE_MIN_MISMATCH
        or min(delta_r3, delta_v2) <= DISAGREE_MAX_DELTA_CORRECT
    ):
        return "disagree"
    if (
        n_nonfinite == 0
        and max(n_r3, n_v2) <= AGREE_MAX_MISMATCH
        and min(delta_r3, delta_v2) >= AGREE_MIN_DELTA_CORRECT
    ):
        return "agree"
    return "undetermined"


def v2_choices(
    questions: list[QuestionCandidates],
    features: dict[tuple[str, int], tuple[float, float]] | None,
    selector_path: Path,
) -> tuple[dict[str, int], dict[str, list[float]]]:
    """V2 の選択と得点．features があれば ce_max・ce_top1 の列だけをそれに差し替える（他の特徴量・重みは固定）．"""
    _, selector = load_selector(selector_path)
    assert selector is not None
    col_max, col_top1 = FEATURE_NAMES.index("ce_max"), FEATURE_NAMES.index("ce_top1")
    choice: dict[str, int] = {}
    scores: dict[str, list[float]] = {}
    for q in questions:
        if features is not None:
            raw = q.raw.copy()
            for i in range(raw.shape[0]):
                raw[i, col_max], raw[i, col_top1] = features[(q.qid, i)]
            q = dataclasses.replace(q, raw=raw)
        p = selector.probabilities(q)
        scores[q.qid] = p
        choice[q.qid] = choose_index(p)
    return choice, scores


def agreement(
    f16_run: Path,
    f32_run: Path,
    table_path: Path = TABLE,
    p1_run: Path = P1_RUN,
    verify_path: Path = VERIFY_FEATURES,
    selector_path: Path = V2_SELECTOR,
) -> dict[str, Any]:
    """H-F1 の数値：fp16 の 1 周の得点を表・同じ日の fp32 の 1 周と比べ，R3′・V2 の選択の食い違いと正誤の変化を出す．"""
    d16, d32 = read_dump(f16_run / "scores.jsonl"), read_dump(f32_run / "scores.jsonl")
    table = read_table(table_path)
    s16, s32 = flat_scores(d16), flat_scores(d32)
    nonfinite = sum(not math.isfinite(v) for v in s16.values())
    diff_table = [abs(v - table[(k[0], k[2])]) for k, v in s16.items() if (k[0], k[2]) in table]
    diff_f32 = [abs(v - s32[k]) for k, v in s16.items() if k in s32]
    qids = sorted(d16)
    questions = [
        q
        for q in load_questions(
            p1_run, need_labels=True, need_answer_features=False, verify_features=verify_path
        )
        if q.qid in d16
    ]
    by_qid = {q.qid: q for q in questions}
    ordered_qids = [q.qid for q in questions]
    correct = {q.qid: q.correct for q in questions}

    # R3′：基準は表（G-S と同じ）．同じ日の fp32 の 1 周を基準にした数も記述として出す
    def r3(
        dump: dict[str, dict[int, list[tuple[str, float]]]] | None,
    ) -> tuple[dict[str, int], dict[str, list[float]]]:
        choice: dict[str, int] = {}
        cand_max: dict[str, list[float]] = {}
        for qid in ordered_qids:
            ncand = len(d16[qid])
            if dump is None:
                values = [max(table[(qid, d)] for d, _ in d16[qid][c]) for c in range(ncand)]
            else:
                values = [max(s for _, s in dump[qid][c]) for c in range(ncand)]
            cand_max[qid] = values
            choice[qid] = pick_by_max(values)
        return choice, cand_max

    base_r3, base_r3_scores = r3(None)
    new_r3, _ = r3(d16)
    f32_r3, f32_r3_scores = r3(d32)
    r3_vs_table = selection_changes(
        ordered_qids, base_r3, new_r3, base_r3_scores, R3_TIE_GAP, correct
    )
    r3_vs_f32 = selection_changes(ordered_qids, f32_r3, new_r3, f32_r3_scores, R3_TIE_GAP, correct)

    # V2：基準は保存済みの特徴量（表と同じ値）．ce_max・ce_top1 だけを差し替える
    base_v2, base_v2_scores = v2_choices(questions, None, selector_path)
    new_v2, _ = v2_choices(questions, ce_features_of(d16), selector_path)
    f32_v2, f32_v2_scores = v2_choices(questions, ce_features_of(d32), selector_path)
    v2_vs_stored = selection_changes(
        ordered_qids, base_v2, new_v2, base_v2_scores, V2_TIE_GAP, correct
    )
    v2_vs_f32 = selection_changes(ordered_qids, f32_v2, new_v2, f32_v2_scores, V2_TIE_GAP, correct)

    # 記述：問ごとの 15 組の順位の一致（Spearman）と，候補ごとの上位 1 通の一致の割合（表と比べる）
    rhos: list[float] = []
    top1_same = top1_total = 0
    for qid in ordered_qids:
        new_vals = [s for c in sorted(d16[qid]) for _, s in d16[qid][c]]
        ref_vals = [table[(qid, d)] for c in sorted(d16[qid]) for d, _ in d16[qid][c]]
        rho = spearmanr(new_vals, ref_vals).statistic
        if math.isfinite(rho):
            rhos.append(float(rho))
        for c in d16[qid]:
            new_c = [s for _, s in d16[qid][c]]
            ref_c = [table[(qid, d)] for d, _ in d16[qid][c]]
            top1_same += int(np.argmax(new_c) == np.argmax(ref_c))
            top1_total += 1
    result = {
        "n_pairs": len(s16),
        "n_questions": len(qids),
        "n_questions_with_labels": len(ordered_qids),
        "n_nonfinite": nonfinite,
        "abs_diff_vs_table": describe_diffs(diff_table),
        "abs_diff_vs_f32_same_day": describe_diffs(diff_f32),
        "R3p_vs_table": r3_vs_table,
        "R3p_vs_f32_same_day": r3_vs_f32,
        "V2_vs_stored_features": v2_vs_stored,
        "V2_vs_f32_same_day": v2_vs_f32,
        "spearman_per_question_vs_table": {
            "n": len(rhos),
            "median": statistics.median(rhos) if rhos else None,
            "min": min(rhos) if rhos else None,
        },
        "top1_agreement_vs_table": top1_same / top1_total if top1_total else None,
        "n_candidates_top1": top1_total,
        "thresholds": {
            "tie_gap_r3": R3_TIE_GAP,
            "tie_gap_v2": V2_TIE_GAP,
            "agree_max_mismatch": AGREE_MAX_MISMATCH,
            "disagree_min_mismatch": DISAGREE_MIN_MISMATCH,
        },
    }
    result["H_F1"] = decide_agreement(
        nonfinite,
        r3_vs_table["n_mismatch"],
        v2_vs_stored["n_mismatch"],
        r3_vs_table["delta_correct"],
        v2_vs_stored["delta_correct"],
    )
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "fp16_agreement.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    assert set(by_qid) == set(ordered_qids)
    return result


# ── 速さ（H-F2）─────────────────────────────────────────────────────────────


def decide_speed(ratios: list[float]) -> str:
    """H-F2（事前登録）：有効なラウンドの全てで S ≥ 1.5 なら faster，全てで S < 1.15 なら not_faster，それ以外（ラウンドが無いときも）は undetermined．"""
    if ratios and all(r >= FASTER_MIN_RATIO for r in ratios):
        return "faster"
    if ratios and all(r < NOT_FASTER_MAX_RATIO for r in ratios):
        return "not_faster"
    return "undetermined"


def round_orders(seed: int = ORDER_SEED) -> list[list[str]]:
    """ラウンドごとの dtype の順（種 38 の無作為な並べ替え．1 つの乱数列から続けて並べ替える）．"""
    rng = random.Random(seed)
    orders = []
    for _ in ROUNDS:
        order = list(DTYPES)
        rng.shuffle(order)
        orders.append(order)
    return orders


def prod_prefix() -> str:
    """本番の実行名の前置き（機を wafl503 に替えた流し直しは `w503_`．chain が書く．無ければ空）．"""
    path = RESULT_DIR / "prod_prefix.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def host_name() -> str:
    """測定に使った機（chain が書く）．"""
    return (RESULT_DIR / "host.txt").read_text(encoding="utf-8").split()[0]


def server_duration_percentiles(
    run: Path, window_start: float, window_end: float
) -> dict[str, float | None]:
    """区間に終わった要求の，server の返した採点の時間（lock の待ちを含む）の p50・p95．"""
    values = []
    with (run / "requests.jsonl").open(encoding="utf-8") as f:
        for row in map(json.loads, f):
            in_window = row["error"] is None and window_start <= row["done_at"] < window_end
            if in_window and row.get("server_duration_s") is not None:
                values.append(row["server_duration_s"])
    if not values:
        return {"p50": None, "p95": None}
    return {"p50": float(np.percentile(values, 50)), "p95": float(np.percentile(values, 95))}


def describe_run(path: Path, host: str) -> dict[str, Any]:
    """有効な 1 実行の集計（要求の数で数えた X_req・X・X_period・待ち時間・GPU の記述）．"""
    bench = read_json(path / "score_bench.json")
    valid = read_json(path / "valid.json")
    wb = WB.summarize_run(path)
    gpu = B2.host_descriptives(path, [host], bench)[host]
    cool = path / "cool_wait_s.txt"
    return {
        "run": path.name,
        "x_req_qps": wb["x_req_qps"],
        "x_count_qps": wb["x_count_qps"],
        "x_period_qps": wb["x_period_qps"],
        "n_ok_questions": bench["n_ok"],
        "latency_p50_s": bench["latency_s"]["p50"],
        "latency_p95_s": bench["latency_s"]["p95"],
        "little_ratio": bench["little_ratio"],
        "server_duration_s": server_duration_percentiles(
            path, bench["window_start"], bench["window_end"]
        ),
        "gpu_util_mean_pct": gpu["util_mean_pct"],
        "gpu_mem_max_mib": gpu["mem_max_mib"],
        "gpu_temp_max_c": gpu["temp_max_c"],
        "sw_thermal_counter_delta_us": valid["hosts"][host].get("counter_delta_us"),
        "control_cpu_busy": bench["control_cpu_busy"],
        "generator_process_cpu_util": bench["generator_process_cpu_util"],
        "cool_wait_s": int(cool.read_text(encoding="utf-8")) if cool.exists() else None,
    }


def sharing_upper_bounds(x_req_f16: float) -> dict[str, float] | None:
    """Iteration 37 の E(G) の中央値を使った分け合いの上限の見積もり min(1.32 × (10 − G)/10, G·E·X_req(fp16))（記述）．"""
    if not EFFICIENCY_SOURCE.exists():
        return None
    by_g = read_json(EFFICIENCY_SOURCE)["by_g"]
    out = {}
    for g in (2, 4, 8):
        eff = list(by_g[str(g)]["efficiency"].values())
        if eff:
            out[str(g)] = min(
                P2P_THROUGHPUT_QPS * (GPUS_TOTAL - g) / GPUS_TOTAL,
                g * statistics.median(eff) * x_req_f16,
            )
    return out


def load_rounds(host: str) -> tuple[dict[int, dict[str, dict[str, Any]]], dict[str, list[str]]]:
    """ラウンド → dtype → 有効な実行の集計（再実行があれば最初の有効なもの）と，無効だった実行の理由．"""
    runs: dict[int, dict[str, dict[str, Any]]] = {}
    invalid: dict[str, list[str]] = {}
    for rnd in ROUNDS:
        for dtype in DTYPES:
            for suffix in ("", "_retry"):
                path = run_dir(f"{prod_prefix()}r{rnd}_{short(dtype)}{suffix}")
                if not (path / "valid.json").exists():
                    continue
                valid = read_json(path / "valid.json")
                if not valid["valid"]:
                    invalid[path.name] = valid["reasons"]
                    continue
                if dtype not in runs.setdefault(rnd, {}):
                    runs[rnd][dtype] = describe_run(path, host)
    return runs, invalid


def summarize() -> dict[str, Any]:
    """H-F2 の数値と，H-F1・H-F2 の機械的な判定．verdict.json・fp16_summary.json・analysis_report.md を書く．"""
    host = host_name()
    runs, invalid = load_rounds(host)
    valid_rounds = [r for r in ROUNDS if set(runs.get(r, {})) == set(DTYPES)]
    ratios = {
        str(r): runs[r]["float16"]["x_req_qps"] / runs[r]["float32"]["x_req_qps"]
        for r in valid_rounds
    }
    h_f2 = decide_speed(list(ratios.values()))
    agree = read_json(RESULT_DIR / "fp16_agreement.json")
    gf32 = read_json(run_dir("f32pass") / "gf32.json")
    gat = read_json(RESULT_DIR / "gat.json")
    smoke_valid = read_json(run_dir("smoke") / "valid.json")["valid"]
    gates_passed = bool(gf32["pass"] and gat["pass"] and smoke_valid)
    enough_rounds = len(valid_rounds) >= 2
    f16_x = [runs[r]["float16"]["x_req_qps"] for r in valid_rounds]
    summary = {
        "host": host,
        "runs": {str(r): v for r, v in sorted(runs.items())},
        "valid_rounds": valid_rounds,
        "S_by_round": ratios,
        "S_median": statistics.median(ratios.values()) if ratios else None,
        "invalid_runs": invalid,
        "gates": {"G_F32": gf32["pass"], "G_AT": gat["pass"], "smoke": smoke_valid},
        "success_condition_1_gates_passed": gates_passed,
        "success_condition_2_enough_valid_rounds": enough_rounds,
        "H_F1": agree["H_F1"],
        "H_F2": h_f2,
        "H_F2_thresholds": {"faster": FASTER_MIN_RATIO, "not_faster": NOT_FASTER_MAX_RATIO},
        "sharing_upper_bound_qps_by_g": sharing_upper_bounds(statistics.median(f16_x))
        if f16_x
        else None,
        "agreement_summary": {
            k: agree[k]
            for k in (
                "n_nonfinite",
                "abs_diff_vs_table",
                "abs_diff_vs_f32_same_day",
                "top1_agreement_vs_table",
            )
        },
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "fp16_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    verdict = {
        "H_F1": agree["H_F1"],
        "H_F2": h_f2,
        "success_condition_1_gates_passed": gates_passed,
        "success_condition_2_enough_valid_rounds": enough_rounds,
        "note": "mechanical application of the pre-registered rules; adoption is decided by rc-evaluator",
    }
    (RESULT_DIR / "verdict.json").write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    (RESULT_DIR / "analysis_report.md").write_text(render_report(summary, agree), encoding="utf-8")
    return summary


def render_report(summary: dict[str, Any], agree: dict[str, Any]) -> str:
    """X_req の表と一致の数値（Markdown．機械集計．採否は評価の担当）．"""
    lines = [
        "# d0009 B2 採点の fp16（機械集計．採否は評価の担当）",
        "",
        f"機: {summary['host']}　有効なラウンド: {summary['valid_rounds']}　G-F32: {summary['gates']['G_F32']}　"
        f"G-AT: {summary['gates']['G_AT']}　smoke: {summary['gates']['smoke']}",
        "",
        "| ラウンド | X_req fp32 | X_req fp16 | S | 温度の最大 fp32/fp16 | GPU メモリの最大 MiB fp32/fp16 |",
        "|---|---|---|---|---|---|",
    ]
    for r in summary["valid_rounds"]:
        a, b = summary["runs"][str(r)]["float32"], summary["runs"][str(r)]["float16"]
        lines.append(
            f"| {r} | {a['x_req_qps']:.3f} | {b['x_req_qps']:.3f} | {summary['S_by_round'][str(r)]:.3f} | "
            f"{a['gpu_temp_max_c']}/{b['gpu_temp_max_c']} | {a['gpu_mem_max_mib']}/{b['gpu_mem_max_mib']} |"
        )
    r3, v2 = agree["R3p_vs_table"], agree["V2_vs_stored_features"]
    lines += [
        "",
        f"H-F2: {summary['H_F2']}　H-F1: {summary['H_F1']}",
        f"非有限: {agree['n_nonfinite']}　|差|（表）: {json.dumps(agree['abs_diff_vs_table'])}",
        f"R3′: 食い違い {r3['n_mismatch']}（同点 {r3['n_mismatch_tie']}）　Δ正解 {r3['delta_correct']}",
        f"V2: 食い違い {v2['n_mismatch']}（同点 {v2['n_mismatch_tie']}）　Δ正解 {v2['delta_correct']}",
        f"無効になった実行: {json.dumps(summary['invalid_runs'], ensure_ascii=False)}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    """サブコマンドを実行し，結果を標準出力へ JSON で出す．gf32・at・check は不合格のとき終了コード 1．"""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("gf32")
    p.add_argument("--run", default=str(run_dir("f32pass")))
    sub.add_parser("at")
    sub.add_parser("agree")
    p = sub.add_parser("check")
    p.add_argument("--run", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--smoke", action="store_true")
    sub.add_parser("orders")
    sub.add_parser("summary")
    args = parser.parse_args()
    if args.cmd == "gf32":
        result = gate_f32(Path(args.run))
        print(json.dumps(result, indent=1))
        return 0 if result["pass"] else 1
    if args.cmd == "at":
        result = gate_at(
            run_dir("f16pass") / "healthz_float32.json", run_dir("f16pass") / "healthz_float16.json"
        )
        print(json.dumps(result, indent=1))
        return 0 if result["pass"] else 1
    if args.cmd == "agree":
        result = agreement(run_dir("f16pass"), run_dir("f32pass"))
        print(json.dumps({k: v for k, v in result.items() if k != "thresholds"}, indent=1)[:6000])
        return 0
    if args.cmd == "check":
        result = B2.check_run(Path(args.run), [args.host], args.smoke)
        print(json.dumps({"valid": result["valid"], "reasons": result["reasons"]}))
        return 0 if result["valid"] else 1
    if args.cmd == "orders":
        for order in round_orders():
            print(" ".join(order))
        return 0
    print(json.dumps(summarize(), indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
