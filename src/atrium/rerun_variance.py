"""同じ設定を 3 回ずつ流し直した実行の揺れを含めた，P2P（R3′）− 中央（C1-RRF-CE）の正答率の差の区間と判定（d0009 A3）．

`atrium rerun-variance` が，採点済みの 6 つの実行（P・C 各 3 回．各実行に results.jsonl と judgements.jsonl）から次を出す．
区間の作り方は journal の Iteration 22 の事前登録で，以降の d0009 の判定（A1・B3 など）も同じものを使う．

記号：設定 c ∈ {P, C}，実行 r = 1..3，問 i = 1..n（全 6 回で成功し判定の付いた問），Y[c, r, i] ∈ {0, 1}，
acc[c, r] = 平均_i Y．主の量は Δ = 平均_r acc[P, r] − 平均_r acc[C, r]．

1. 2 段のブートストラップ（主）：(1) P の 3 回と C の 3 回をそれぞれ独立に復元抽出し，(2) 問を n 個復元抽出して
   同じ問の添字を選んだ全ての実行に当て（対応あり），(3) Δ* を求める．10,000 回・種 12 の百分位 95% 区間．
2. 分散の分解による解析的な区間：s_i = 平均_r Y[P, r, i] − 平均_r Y[C, r, i]，SE_q = sd(s_i) / √n．
   各 c で実行の水準の揺れ σ²_u = max(0, Var_r(acc[c, r]) − 平均_i v[c, i] / n)（v は問 i の 3 回の標本分散．独立な回答の変化で
   説明できる分を引く）．SE_tot = √(SE_q² + σ²_u,P / 3 + σ²_u,C / 3)．区間は Δ ± t · SE_tot（σ² の項が両方 0 なら t = 1.96．
   正なら自由度を Welch–Satterthwaite で求めた t の 0.975 分位．SE_q² の自由度は無限，σ² の項は各 2）．
3. 併記（判定に使わない）：(a) 1 回ずつの組（P_r 対 C_r'．9 組）の従来の質問のブートストラップの区間，(b) 受信箱で束ねた版（2 段目を
   受信箱の復元抽出にする），(c) 同じ設定の 2 回の組（各 c で 3 組）の差 d と McNemar の不一致の数 b + c・z = d / (√(b + c) / n)．

判定：H-V1（同じ設定の 6 組の McNemar の正確検定が全て p ≥ 0.05），H-V2（区間 1・2 の両方の下端 > −δ で非劣性．片方だけなら
「区間の作り方に依る」，両方 ≤ −δ なら「非劣性を満たさない」．優越は両方の下端 > 0），H-V3（区間 1 の幅 ÷ 9 組の区間の幅の中央値．
≤ 1.2 なら小さい），δ の見直し（δ_rev = 3 × 同じ設定の 6 組の差の絶対値の最大を 0.005 単位に切り上げ．δ_rev ≠ 0.02 なら verdict_revised）．

統計量は既存の実装を使う：McNemar の正確検定（`atrium.analysis.mcnemar_exact`），対応ありのブートストラップ区間
（`atrium.verdict.paired_bootstrap_ci`），verdict.json の書式（`atrium.verdict.write_verdict`）．Student の t 分布の分位点だけは
依存に scipy が無いので，不完全ベータ関数の連分数（Press et al., Numerical Recipes 3rd ed. §6.4．modified Lentz 法）で
t の累積分布（A&S 26.7.1）を求め，二分法で逆算する（`tests/test_rerun_variance.py` で標準の表の値と照合する）．
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import mcnemar_exact, merge_judgements, read_results
from atrium.arrays import F64Array
from atrium.verdict import (
    ALPHA,
    BOOTSTRAP_CHUNK,
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
    NON_INFERIORITY_MARGIN,
    paired_bootstrap_ci,
    write_verdict,
)

logger = logging.getLogger(__name__)

N_RUNS = 3
Z_975 = 1.959963984540054
# 流し直しの揺れの幅の何倍を δ とするか（元の δ = 0.02 は，当時の流し直しの幅の最大 0.006 の約 3 倍）
DELTA_FACTOR = 3.0
DELTA_STEP = 0.005
# H-V3：1 の区間の幅が 9 組の区間の幅の中央値のこの倍以下なら「広がりは小さい」
WIDTH_RATIO_LIMIT = 1.2
# 判定不能の割合の上限（成功条件）
UNPARSED_LIMIT = 0.01
VERDICT_NON_INFERIOR = "non_inferior"
VERDICT_DEPENDS = "depends_on_interval_method"
VERDICT_NOT_NON_INFERIOR = "not_non_inferior"
_BETACF_MAX_ITER = 500
_BETACF_EPS = 3e-16
_TINY = 1e-300


# ── Student の t 分布の分位点 ──────────────────────────────────────────────────


def _betacf(a: float, b: float, x: float) -> float:
    """不完全ベータ関数の連分数（modified Lentz 法．Numerical Recipes §6.4）．"""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = _TINY if abs(d) < _TINY else d
    d = 1.0 / d
    h = d
    for m in range(1, _BETACF_MAX_ITER + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = _TINY if abs(d) < _TINY else d
        c = 1.0 + aa / c
        c = _TINY if abs(c) < _TINY else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = _TINY if abs(d) < _TINY else d
        c = 1.0 + aa / c
        c = _TINY if abs(c) < _TINY else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _BETACF_EPS:
            return h
    raise RuntimeError("incomplete beta continued fraction did not converge")


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """正則化不完全ベータ関数 I_x(a, b)．"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def student_t_cdf(t: float, df: float) -> float:
    """Student の t 分布の累積分布関数（自由度 df > 0．Abramowitz & Stegun 26.7.1）．"""
    tail = 0.5 * regularized_incomplete_beta(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def student_t_quantile(p: float, df: float) -> float:
    """Student の t 分布の p 分位点（0.5 < p < 1．二分法）．"""
    if not 0.5 < p < 1.0:
        raise ValueError("p must be in (0.5, 1)")
    low, high = 0.0, 1.0
    while student_t_cdf(high, df) < p:
        high *= 2.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if student_t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


# ── 実行の読み込み ───────────────────────────────────────────────────────────


def read_outcomes(run_dir: Path) -> tuple[dict[str, bool], dict[str, int]]:
    """実行の（質問 ID → 正誤（成功し判定の付いた問だけ），件数: 成功・判定不能・失敗）を返す．"""
    rows = read_results(run_dir / "results.jsonl")
    judgements = run_dir / "judgements.jsonl"
    if judgements.exists():
        rows = merge_judgements(rows, judgements)
    outcomes: dict[str, bool] = {}
    counts = {"rows": len(rows), "failed": 0, "unparsed": 0, "unjudged": 0}
    for r in rows:
        if r.get("error") is not None:
            counts["failed"] += 1
        elif "correct" not in r:
            counts["unjudged"] += 1
        elif r.get("judge_unparsed"):
            counts["unparsed"] += 1
        else:
            outcomes[r["qid"]] = bool(r["correct"])
    return outcomes, counts


def build_matrices(
    runs: Mapping[str, Sequence[Path]],
) -> tuple[list[str], dict[str, F64Array], dict[str, Any]]:
    """設定ごとの Y（実行 × 問．全ての実行で成功し判定の付いた問だけ）と，除いた問の数の記録を返す．"""
    outcomes = {c: [read_outcomes(d) for d in dirs] for c, dirs in runs.items()}
    common: set[str] | None = None
    for per_run in outcomes.values():
        for o, _ in per_run:
            common = set(o) if common is None else common & set(o)
    qids = sorted(common or set())
    y = {
        c: np.array([[float(o[q]) for q in qids] for o, _ in per_run], dtype=np.float64)
        for c, per_run in outcomes.items()
    }
    info = {
        "n_questions": len(qids),
        "runs": {
            c: [{"run": d.name, **counts} for d, (_, counts) in zip(runs[c], per_run, strict=True)]
            for c, per_run in outcomes.items()
        },
    }
    return qids, y, info


# ── 区間 ──────────────────────────────────────────────────────────────────────


def _run_weights(rng: np.random.Generator, size: int, n_runs: int) -> F64Array:
    """実行の復元抽出を，実行ごとの重み（抽出の回数 / 実行の数）の行列（size × n_runs）にする．"""
    draws = rng.integers(0, n_runs, size=(size, n_runs))
    weights: F64Array = np.eye(n_runs)[draws].sum(axis=1) / n_runs
    return weights


def two_stage_bootstrap(
    y_p: F64Array,
    y_c: F64Array,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
    clusters: Sequence[str] | None = None,
) -> tuple[float, float]:
    """実行（P・C それぞれ独立）→ 問の 2 段のブートストラップの Δ の百分位 95% 区間．

    問の添字は，選んだ全ての実行に同じものを当てる（対応あり）．clusters（問ごとの受信箱）を渡すと，2 段目を受信箱の
    復元抽出にする（Δ* は抽出した受信箱の全ての問の平均）．
    """
    n_runs, n = y_p.shape
    rng = np.random.default_rng(seed)
    if clusters is None:
        unit_p, unit_c = y_p, y_c
        sizes: F64Array = np.ones(n, dtype=np.float64)
    else:
        names = sorted(set(clusters))
        member = np.array([names.index(c) for c in clusters])
        unit_p = np.stack([np.bincount(member, weights=row, minlength=len(names)) for row in y_p])
        unit_c = np.stack([np.bincount(member, weights=row, minlength=len(names)) for row in y_c])
        sizes = np.bincount(member, minlength=len(names)).astype(np.float64)
    n_units = unit_p.shape[1]
    means: list[F64Array] = []
    for start in range(0, samples, BOOTSTRAP_CHUNK):
        size = min(BOOTSTRAP_CHUNK, samples - start)
        w_p = _run_weights(rng, size, n_runs)
        w_c = _run_weights(rng, size, y_c.shape[0])
        idx = rng.integers(0, n_units, size=(size, n_units))
        diff = w_p @ unit_p - w_c @ unit_c
        picked = np.take_along_axis(diff, idx, axis=1).sum(axis=1)
        means.append(picked / sizes[idx].sum(axis=1))
    all_means = np.concatenate(means)
    return (
        float(np.percentile(all_means, 100 * ALPHA / 2)),
        float(np.percentile(all_means, 100 * (1 - ALPHA / 2))),
    )


def run_level_variance(y: F64Array) -> float:
    """実行の水準の揺れ σ²_u = max(0, Var_r(acc_r) − 平均_i v_i / n)．v_i は問 i の実行間の標本分散（独立な回答の変化の分）．"""
    n = y.shape[1]
    acc = y.mean(axis=1)
    per_question = y.var(axis=0, ddof=1)
    return max(0.0, float(acc.var(ddof=1) - per_question.mean() / n))


def analytic_interval(y_p: F64Array, y_c: F64Array) -> dict[str, Any]:
    """分散の分解による解析的な 95% 区間（区間の作り方 2）．"""
    n_runs_p, n = y_p.shape
    n_runs_c = y_c.shape[0]
    s = y_p.mean(axis=0) - y_c.mean(axis=0)
    delta = float(s.mean())
    se_q = float(s.std(ddof=1) / math.sqrt(n))
    sigma_p = run_level_variance(y_p)
    sigma_c = run_level_variance(y_c)
    term_p, term_c = sigma_p / n_runs_p, sigma_c / n_runs_c
    se_tot = math.sqrt(se_q**2 + term_p + term_c)
    if term_p + term_c == 0.0:
        quantile, df = Z_975, None
    else:
        # Welch–Satterthwaite．SE_q² の自由度は無限（寄与 0），σ² の項は各 2
        df = se_tot**4 / ((term_p**2 + term_c**2) / 2.0)
        quantile = student_t_quantile(1.0 - ALPHA / 2, df)
    return {
        "delta": delta,
        "se_q": se_q,
        "sigma2_u_p": sigma_p,
        "sigma2_u_c": sigma_c,
        "se_total": se_tot,
        "df": df,
        "t": quantile,
        "ci95": [delta - quantile * se_tot, delta + quantile * se_tot],
    }


def pair_intervals(y_p: F64Array, y_c: F64Array) -> list[dict[str, Any]]:
    """1 回ずつの組（P_r 対 C_r'．9 組）の，従来の質問のブートストラップの区間（併記 a．判定に使わない）．"""
    out = []
    for r in range(y_p.shape[0]):
        for r2 in range(y_c.shape[0]):
            diff = y_p[r] - y_c[r2]
            low, high = paired_bootstrap_ci(diff)
            out.append(
                {
                    "p_run": r + 1,
                    "c_run": r2 + 1,
                    "delta": float(diff.mean()),
                    "ci95": [low, high],
                    "width": high - low,
                }
            )
    return out


def same_config_pairs(y: F64Array) -> list[dict[str, Any]]:
    """同じ設定の 2 回の組（3 組）の差 d・McNemar の不一致の数・z = d / (√(b + c) / n)・正確検定の p（併記 c と H-V1）．"""
    n = y.shape[1]
    out = []
    for a in range(y.shape[0]):
        for b in range(a + 1, y.shape[0]):
            only_a = int(((y[a] == 1) & (y[b] == 0)).sum())
            only_b = int(((y[a] == 0) & (y[b] == 1)).sum())
            d = float(y[a].mean() - y[b].mean())
            discordant = only_a + only_b
            out.append(
                {
                    "runs": [a + 1, b + 1],
                    "d": d,
                    "only_first_correct": only_a,
                    "only_second_correct": only_b,
                    "z": d / (math.sqrt(discordant) / n) if discordant else 0.0,
                    "mcnemar_p": mcnemar_exact(only_a, only_b),
                }
            )
    return out


def round_up_delta(value: float) -> float:
    """δ を DELTA_STEP 単位に切り上げる（浮動小数点の誤差で 1 刻み上がらないよう小さな許容を引く）．"""
    return round(math.ceil(value / DELTA_STEP - 1e-9) * DELTA_STEP, 6)


def decide_non_inferiority(boot_low: float, analytic_low: float, margin: float) -> dict[str, Any]:
    """H-V2：2 つの区間の下端がどちらも −margin を上回れば非劣性，片方だけなら区間の作り方に依る，どちらも下回れば非劣性ではない．"""
    holds = [boot_low > -margin, analytic_low > -margin]
    if all(holds):
        verdict = VERDICT_NON_INFERIOR
    elif any(holds):
        verdict = VERDICT_DEPENDS
    else:
        verdict = VERDICT_NOT_NON_INFERIOR
    return {
        "margin": margin,
        "bootstrap_low_above": holds[0],
        "analytic_low_above": holds[1],
        "verdict": verdict,
    }


# ── 全体 ──────────────────────────────────────────────────────────────────────


def evaluate(
    runs: Mapping[str, Sequence[Path]],
    inbox_of_qid: Mapping[str, str],
    margin: float = NON_INFERIORITY_MARGIN,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """P・C 各 3 回の実行から，区間 1・2・併記・H-V1〜H-V3・δ の見直しを求める．

    runs は {"P": [3 つの実行のディレクトリ], "C": [同]}．inbox_of_qid は問の受信箱（併記 b の束ね）．
    """
    qids, y, info = build_matrices(runs)
    y_p, y_c = y["P"], y["C"]
    if y_p.shape[0] != N_RUNS or y_c.shape[0] != N_RUNS:
        raise ValueError(f"each configuration needs exactly {N_RUNS} runs")
    n = len(qids)
    acc = {c: [float(v) for v in y[c].mean(axis=1)] for c in y}
    delta = float(y_p.mean(axis=0).mean() - y_c.mean(axis=0).mean())
    boot = two_stage_bootstrap(y_p, y_c, samples, seed)
    analytic = analytic_interval(y_p, y_c)
    clustered = two_stage_bootstrap(y_p, y_c, samples, seed, [inbox_of_qid[q] for q in qids])
    pairs = pair_intervals(y_p, y_c)
    widths = [p["width"] for p in pairs]
    same = {c: same_config_pairs(y[c]) for c in y}
    all_same = [p for c in same for p in same[c]]
    max_d = max(abs(p["d"]) for p in all_same)
    v1_pass = all(p["mcnemar_p"] >= ALPHA for p in all_same)
    ratio = (boot[1] - boot[0]) / float(np.median(widths))
    delta_rev = round_up_delta(DELTA_FACTOR * max_d)
    unparsed_total = sum(r["unparsed"] for c in info["runs"] for r in info["runs"][c])
    rows_total = sum(r["rows"] for c in info["runs"] for r in info["runs"][c])
    h_v2 = decide_non_inferiority(boot[0], analytic["ci95"][0], margin)
    superior = boot[0] > 0 and analytic["ci95"][0] > 0
    return {
        "n_questions": n,
        "inputs": info,
        "accuracy_by_run": acc,
        "delta": delta,
        "bootstrap_two_stage": {
            "ci95": list(boot),
            "width": boot[1] - boot[0],
            "samples": samples,
            "seed": seed,
        },
        "analytic": analytic,
        "also_inbox_clustered_bootstrap": {
            "ci95": list(clustered),
            "width": clustered[1] - clustered[0],
        },
        "also_single_run_pairs": {"pairs": pairs, "median_width": float(np.median(widths))},
        "same_config_pairs": same,
        "unparsed_rate": unparsed_total / rows_total if rows_total else None,
        "H_V1": {
            "holds": v1_pass,
            "n_pairs": len(all_same),
            "min_p": min(p["mcnemar_p"] for p in all_same),
            "rule": "同じ設定の 6 組の McNemar の正確検定が全て p >= 0.05",
        },
        "H_V2": {**h_v2, "superior": bool(superior)},
        "H_V3": {
            "width_ratio": ratio,
            "limit": WIDTH_RATIO_LIMIT,
            "reading": "small" if ratio <= WIDTH_RATIO_LIMIT else "wider",
        },
        "delta_revision": {
            "max_abs_same_config_d": max_d,
            "delta_rev": delta_rev,
            "differs_from_original": delta_rev != margin,
            "H_V2_at_delta_rev": decide_non_inferiority(boot[0], analytic["ci95"][0], delta_rev)
            if delta_rev != margin
            else None,
        },
    }


def reread_test_pair(run_p: Path, run_c: Path, sigma2_p: float, sigma2_c: float) -> dict[str, Any]:
    """設計に使った test の 1 回ずつの組の差に，H で測った実行の水準の揺れの分散を足した解析的な区間（記述）．

    SE² = (問の差の標本標準偏差 / √n)² + σ²_u,P + σ²_u,C（各 1 回の実行なので 1 倍）．σ² の項が 0 なら t = 1.96，
    正なら Welch–Satterthwaite の自由度（各項 1 回の実行の揺れで自由度 2）の t．
    """
    p, _ = read_outcomes(run_p)
    c, _ = read_outcomes(run_c)
    shared = sorted(set(p) & set(c))
    diff = np.array([float(p[q]) - float(c[q]) for q in shared], dtype=np.float64)
    n = len(shared)
    se_q = float(diff.std(ddof=1) / math.sqrt(n))
    se_tot = math.sqrt(se_q**2 + sigma2_p + sigma2_c)
    if sigma2_p + sigma2_c == 0.0:
        quantile, df = Z_975, None
    else:
        df = se_tot**4 / ((sigma2_p**2 + sigma2_c**2) / 2.0)
        quantile = student_t_quantile(1.0 - ALPHA / 2, df)
    d = float(diff.mean())
    low, high = paired_bootstrap_ci(diff)
    return {
        "p_run": run_p.name,
        "c_run": run_c.name,
        "n": n,
        "delta": d,
        "bootstrap_ci95": [low, high],
        "extended_ci95": [d - quantile * se_tot, d + quantile * se_tot],
        "se_q": se_q,
        "se_total": se_tot,
        "df": df,
        "t": quantile,
    }


def reread_test(
    result: Mapping[str, Any],
    test_pairs: Mapping[str, tuple[Path, Path]],
    reference_diffs: Mapping[str, float],
) -> dict[str, Any]:
    """test の読み直し（記述）：test の組に H の σ²_u を足した区間と，参照の差（A2 の A5 対 A6 など）を H の同じ設定の組の差と並べる．"""
    sigma_p = result["analytic"]["sigma2_u_p"]
    sigma_c = result["analytic"]["sigma2_u_c"]
    pair_d = [p["d"] for c in result["same_config_pairs"] for p in result["same_config_pairs"][c]]
    max_d = result["delta_revision"]["max_abs_same_config_d"]
    return {
        "sigma2_u": {"P": sigma_p, "C": sigma_c},
        "test_pairs": {
            name: reread_test_pair(run_p, run_c, sigma_p, sigma_c)
            for name, (run_p, run_c) in test_pairs.items()
        },
        "same_config_d": {
            "values": pair_d,
            "max_abs": max_d,
            "rms": math.sqrt(sum(d * d for d in pair_d) / len(pair_d)),
        },
        "reference_diffs": {
            name: {
                "value": value,
                "ratio_to_max_abs_same_config_d": abs(value) / max_d if max_d else None,
                "n_same_config_pairs_with_abs_d_at_least_value": sum(
                    abs(d) >= abs(value) for d in pair_d
                ),
            }
            for name, value in reference_diffs.items()
        },
    }


def write_outputs(out_dir: Path, result: dict[str, Any]) -> None:
    """rerun_variance.json と verdict.json（verdict_original：δ = 0.02．verdict_revised：δ_rev ≠ 0.02 のときだけ）を書く．"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rerun_variance.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    summary = {
        "delta": result["delta"],
        "bootstrap_two_stage_ci95": result["bootstrap_two_stage"]["ci95"],
        "analytic_ci95": result["analytic"]["ci95"],
        "H_V1": result["H_V1"],
        "H_V2": result["H_V2"],
        "H_V3": result["H_V3"],
    }
    revision = result["delta_revision"]
    revised = None
    if revision["differs_from_original"]:
        revised = {
            **summary,
            "H_V2": {**revision["H_V2_at_delta_rev"], "superior": result["H_V2"]["superior"]},
            "post_hoc": True,
            "delta_rev": revision["delta_rev"],
            "reason": (
                "元の δ は記録時点の流し直しの幅の最大 0.006 の約 3 倍．今回測り直した同じ設定の組の差の絶対値の最大 "
                f"{revision['max_abs_same_config_d']:.4f} の 3 倍を 0.005 単位に切り上げた値"
            ),
        }
    write_verdict(out_dir, summary, revised)
