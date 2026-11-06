"""d0009 A3：流し直しの揺れを含めた差の区間（2 段のブートストラップ・分散の分解・H-V1〜H-V3・δ の見直し）の仕様．"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from atrium import rerun_variance as rv
from atrium.arrays import F64Array
from atrium.verdict import VERDICT_FILENAME

SAMPLES = 400


def random_runs(n: int, seed: int, p: float, shift: float = 0.0) -> F64Array:
    """正答率 p の 0・1 の行列（実行 3 × 問 n）．各実行の正答率を shift ずつずらす（実行の水準の揺れ）．"""
    rng = np.random.default_rng(seed)
    base = rng.random(n)
    runs = [(base + 0.3 * rng.random(n) < p + shift * r) for r in range(rv.N_RUNS)]
    return np.array(runs, dtype=np.float64)


@pytest.mark.parametrize(
    ("df", "expected"),
    [(2, 4.302653), (4, 2.776445), (10, 2.228139), (30, 2.042272), (1000, 1.962339)],
)
def test_student_t_quantile_matches_the_standard_table(df: float, expected: float) -> None:
    assert rv.student_t_quantile(0.975, df) == pytest.approx(expected, abs=2e-5)


def test_run_level_variance_is_clipped_to_zero_when_independent_flips_explain_the_spread() -> None:
    # 3 回の正答率が同じ（Var_r = 0）で，問ごとの不一致だけがある
    y = np.array([[1, 0, 1, 0], [0, 1, 1, 0], [1, 1, 0, 0]], dtype=np.float64)
    assert y.mean(axis=1).var(ddof=1) == pytest.approx(0.0, abs=1e-12)
    assert rv.run_level_variance(y) == 0.0


def test_run_level_variance_is_positive_when_a_whole_run_shifts() -> None:
    # 問ごとに 3 回の回答は同じ（v_i = 0）で，3 回目だけ全問で 1 つ下がる実行（全体に共通の揺れ）
    y = np.ones((3, 20))
    y[2, :10] = 0.0
    sigma = rv.run_level_variance(y)
    assert sigma > 0
    acc = y.mean(axis=1)
    per_question = y.var(axis=0, ddof=1).mean()
    assert sigma == pytest.approx(acc.var(ddof=1) - per_question / 20)


def test_analytic_interval_uses_welch_satterthwaite_degrees_of_freedom() -> None:
    y_p = random_runs(300, 1, 0.6, shift=0.04)
    y_c = random_runs(300, 2, 0.6, shift=-0.03)
    r = rv.analytic_interval(y_p, y_c)
    term_p, term_c = r["sigma2_u_p"] / 3, r["sigma2_u_c"] / 3
    assert term_p + term_c > 0
    assert r["df"] == pytest.approx(r["se_total"] ** 4 / ((term_p**2 + term_c**2) / 2))
    assert r["t"] == pytest.approx(rv.student_t_quantile(0.975, r["df"]))
    assert r["ci95"][1] - r["delta"] == pytest.approx(r["t"] * r["se_total"])


def test_analytic_interval_uses_1_96_when_there_is_no_run_level_variance() -> None:
    y = np.array([[1, 0, 1, 0], [0, 1, 1, 0], [1, 1, 0, 0]] * 1, dtype=np.float64)
    r = rv.analytic_interval(y, y[::-1].copy())
    assert r["sigma2_u_p"] == r["sigma2_u_c"] == 0.0
    assert r["df"] is None and r["t"] == rv.Z_975


def test_bootstrap_applies_the_same_question_indices_to_both_configurations() -> None:
    # P も C も同じ 3 つの行（全ての実行で同じ）．問の添字が P と C で共通なら Δ* は常に 0（独立に引けば 0 にならない）
    x = np.random.default_rng(0).integers(0, 2, 50).astype(np.float64)
    y = np.stack([x, x, x])
    assert rv.two_stage_bootstrap(y, y.copy(), SAMPLES, 12) == pytest.approx((0.0, 0.0), abs=1e-12)
    clusters = [f"inbox{i % 5}" for i in range(50)]
    assert rv.two_stage_bootstrap(y, y.copy(), SAMPLES, 12, clusters) == pytest.approx(
        (0.0, 0.0), abs=1e-12
    )


def test_bootstrap_is_reproducible_for_a_seed_and_differs_between_seeds() -> None:
    y_p, y_c = random_runs(200, 3, 0.6, 0.03), random_runs(200, 4, 0.55, 0.0)
    first = rv.two_stage_bootstrap(y_p, y_c, SAMPLES, 12)
    assert rv.two_stage_bootstrap(y_p, y_c, SAMPLES, 12) == first
    assert rv.two_stage_bootstrap(y_p, y_c, SAMPLES, 13) != first


def test_bootstrap_widens_when_the_runs_differ_by_a_whole_run_shift() -> None:
    y_c = random_runs(300, 6, 0.55)
    y_p_same = np.stack([random_runs(300, 7, 0.6)[0]] * 3)
    y_p_shifted = y_p_same.copy()
    y_p_shifted[2, :60] = 1 - y_p_shifted[2, :60]
    narrow = rv.two_stage_bootstrap(y_p_same, y_c, SAMPLES, 12)
    wide = rv.two_stage_bootstrap(y_p_shifted, y_c, SAMPLES, 12)
    assert wide[1] - wide[0] > narrow[1] - narrow[0]


def test_same_config_pairs_count_discordant_questions_and_z() -> None:
    y = np.array([[1, 1, 0, 0, 1, 0], [1, 0, 1, 0, 1, 1], [0, 0, 0, 0, 0, 0]], dtype=np.float64)
    first = rv.same_config_pairs(y)[0]
    assert first["runs"] == [1, 2]
    assert (first["only_first_correct"], first["only_second_correct"]) == (1, 2)
    d = 3 / 6 - 4 / 6
    assert first["d"] == pytest.approx(d)
    assert first["z"] == pytest.approx(d / (math.sqrt(3) / 6))
    assert first["mcnemar_p"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.018, 0.02), (0.02, 0.02), (0.0201, 0.025), (0.015, 0.015), (0.0151, 0.02), (0.003, 0.005)],
)
def test_delta_is_rounded_up_to_the_next_0_005(value: float, expected: float) -> None:
    assert rv.round_up_delta(value) == expected


@pytest.mark.parametrize(
    ("boot_low", "analytic_low", "verdict"),
    [
        (-0.01, -0.015, rv.VERDICT_NON_INFERIOR),
        (-0.01, -0.03, rv.VERDICT_DEPENDS),
        (-0.03, -0.01, rv.VERDICT_DEPENDS),
        (-0.03, -0.04, rv.VERDICT_NOT_NON_INFERIOR),
        (-0.02, -0.02, rv.VERDICT_NOT_NON_INFERIOR),
    ],
)
def test_non_inferiority_needs_both_interval_lower_bounds_above_minus_delta(
    boot_low: float, analytic_low: float, verdict: str
) -> None:
    assert rv.decide_non_inferiority(boot_low, analytic_low, 0.02)["verdict"] == verdict


def write_run(
    root: Path, name: str, correct: list[bool | None], failed: set[int] = frozenset()
) -> Path:
    """結果と採点の書かれた実行（correct が None なら判定不能，failed の添字は失敗）．"""
    run = root / name
    run.mkdir(parents=True)
    with (run / "results.jsonl").open("w") as f:
        for i, _ in enumerate(correct):
            f.write(json.dumps({"qid": f"q{i}", "error": "x" if i in failed else None}) + "\n")
    with (run / "judgements.jsonl").open("w") as f:
        for i, c in enumerate(correct):
            if i not in failed:
                f.write(
                    json.dumps({"qid": f"q{i}", "correct": c, "answer_overlap_lcs": 0.0}) + "\n"
                )
    return run


def test_evaluate_uses_only_questions_that_succeeded_and_were_judged_in_all_six_runs(
    tmp_path: Path,
) -> None:
    rng = np.random.default_rng(0)
    n = 60
    runs: dict[str, list[Path]] = {"P": [], "C": []}
    for c, p in (("P", 0.6), ("C", 0.62)):
        for r in range(3):
            correct: list[bool | None] = [bool(v) for v in rng.random(n) < p]
            if c == "P" and r == 0:
                correct[3] = None  # 判定不能
            runs[c].append(
                write_run(
                    tmp_path, f"{c}{r}", correct, failed={5} if c == "C" and r == 2 else set()
                )
            )
    inbox = {f"q{i}": f"inbox{i % 6}" for i in range(n)}

    result = rv.evaluate(runs, inbox, samples=SAMPLES)

    assert result["n_questions"] == n - 2
    assert result["inputs"]["runs"]["P"][0]["unparsed"] == 1
    assert result["inputs"]["runs"]["C"][2]["failed"] == 1
    assert len(result["also_single_run_pairs"]["pairs"]) == 9
    assert result["H_V3"]["reading"] in ("small", "wider")
    delta_rev = result["delta_revision"]["delta_rev"]
    assert delta_rev == rv.round_up_delta(3 * result["delta_revision"]["max_abs_same_config_d"])


def test_write_outputs_keeps_verdict_original_and_adds_verdict_revised_only_when_delta_changes(
    tmp_path: Path,
) -> None:
    rng = np.random.default_rng(1)
    n = 80
    runs: dict[str, list[Path]] = {"P": [], "C": []}
    for c in ("P", "C"):
        base = rng.random(n)
        for r in range(3):
            flips = rng.random(n) < 0.05
            runs[c].append(
                write_run(tmp_path, f"{c}{r}", [bool(v) for v in ((base < 0.6) ^ flips)])
            )
    result = rv.evaluate(runs, {f"q{i}": "a" for i in range(n)}, samples=SAMPLES)
    out = tmp_path / "out"

    rv.write_outputs(out, result)

    verdict = json.loads((out / VERDICT_FILENAME).read_text())
    assert verdict["verdict_original"]["H_V2"]["margin"] == 0.02
    assert (verdict["verdict_revised"] is not None) == result["delta_revision"][
        "differs_from_original"
    ]
    assert (out / "rerun_variance.json").exists()
