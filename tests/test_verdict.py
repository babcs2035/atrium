"""verdict.json と精度の判定（p0007 I9・§4.2）の仕様：対応ありのブートストラップによる非劣性・McNemar による優越・verdict_original の保全．"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from atrium.cli import main
from atrium.verdict import (
    VERDICT_NON_INFERIOR,
    VERDICT_NOT_NON_INFERIOR,
    VERDICT_SUPERIOR,
    judge_accuracy,
    make_verdict,
    paired_bootstrap_ci,
    write_verdict,
)

N = 2000


def rows_from(correct: np.ndarray) -> list[dict[str, Any]]:
    """正誤の配列から，採点済みの results.jsonl の行を作る．"""
    return [{"qid": f"q{i:04d}", "correct": bool(c), "error": None} for i, c in enumerate(correct)]


def pair_with_gap(
    n_only_a: int, n_only_b: int, n_both: int = 1000
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A だけが正解の問が n_only_a，B だけが正解の問が n_only_b，両方正解が n_both，両方不正解が残り（合計 N）の組．"""
    a = np.zeros(N, bool)
    b = np.zeros(N, bool)
    a[:n_only_a] = True
    b[n_only_a : n_only_a + n_only_b] = True
    a[n_only_a + n_only_b : n_only_a + n_only_b + n_both] = True
    b[n_only_a + n_only_b : n_only_a + n_only_b + n_both] = True
    return rows_from(a), rows_from(b)


def test_ci_is_paired_and_uses_the_given_seed() -> None:
    diff = np.array([1.0] * 30 + [-1.0] * 10 + [0.0] * 60)
    first = paired_bootstrap_ci(diff, samples=2000, seed=12)
    assert first == paired_bootstrap_ci(diff, samples=2000, seed=12)
    assert first[0] < diff.mean() < first[1]
    assert first != paired_bootstrap_ci(diff, samples=2000, seed=13)


def test_ci_of_a_constant_difference_is_that_constant() -> None:
    assert paired_bootstrap_ci(np.full(50, -0.0), samples=100) == (0.0, 0.0)


def test_equal_runs_are_non_inferior_but_not_superior() -> None:
    a, b = pair_with_gap(50, 50)
    result = judge_accuracy(a, b, samples=2000)
    assert result["verdict"] == VERDICT_NON_INFERIOR
    assert result["non_inferiority"]["holds"] and not result["superiority"]["holds"]
    assert result["difference"] == 0.0 and result["n_shared"] == N


def test_a_clearly_higher_run_is_superior() -> None:
    a, b = pair_with_gap(200, 50)
    result = judge_accuracy(a, b, samples=2000)
    assert result["verdict"] == VERDICT_SUPERIOR
    assert result["superiority"]["only_p2p_correct"] == 200
    assert result["difference"] == pytest.approx(150 / N)


def test_a_clearly_lower_run_is_not_non_inferior() -> None:
    # 差は -0.1（A だけ正解 50，B だけ正解 250）で，下端は -0.02 を下回る
    a, b = pair_with_gap(50, 250)
    result = judge_accuracy(a, b, samples=2000)
    assert result["verdict"] == VERDICT_NOT_NON_INFERIOR
    assert result["ci95"][1] < 0


def test_the_margin_decides_non_inferiority_for_the_same_data() -> None:
    # 差 -0.02（A だけ 50，B だけ 90．N = 2000）：区間の下端は -0.02 より下になる
    a, b = pair_with_gap(50, 90)
    assert judge_accuracy(a, b, margin=0.02, samples=2000)["verdict"] == VERDICT_NOT_NON_INFERIOR
    assert judge_accuracy(a, b, margin=0.05, samples=2000)["verdict"] == VERDICT_NON_INFERIOR


def test_only_questions_graded_in_both_runs_are_compared() -> None:
    a, b = pair_with_gap(10, 10)
    b[0]["error"] = "boom"
    del a[1]["correct"]
    assert judge_accuracy(a, b, samples=200)["n_shared"] == N - 2


def test_no_shared_question_is_an_error() -> None:
    with pytest.raises(ValueError):
        judge_accuracy(rows_from(np.ones(3)), [{"qid": "other", "correct": True}], samples=10)


def test_write_verdict_never_overwrites_an_existing_original(tmp_path: Path) -> None:
    original = {"verdict": "not_non_inferior"}
    write_verdict(tmp_path, original)
    revised = {"verdict": "non_inferior", "post_hoc": True}

    written = write_verdict(tmp_path, original, revised)

    assert written == make_verdict(original, revised)
    assert json.loads((tmp_path / "verdict.json").read_text()) == make_verdict(original, revised)
    with pytest.raises(ValueError, match="verdict_original"):
        write_verdict(tmp_path, {"verdict": "non_inferior"})


def test_a_later_write_without_original_keeps_the_stored_one(tmp_path: Path) -> None:
    write_verdict(tmp_path, {"verdict": "superior"})
    assert write_verdict(tmp_path, None, {"verdict": "x"})["verdict_original"] == {
        "verdict": "superior"
    }


def test_compare_command_writes_original_and_a_post_hoc_revised_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = pair_with_gap(50, 90)
    for name, rows in (("p2p", a), ("central", b)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "results.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
    out = tmp_path / "out"
    args = [
        "compare",
        str(tmp_path / "p2p"),
        str(tmp_path / "central"),
        "--out-dir",
        str(out),
        "--samples",
        "2000",
    ]

    assert main(args) == 0
    assert main([*args, "--revised-margin", "0.05", "--revised-reason", "measurement noise"]) == 0

    verdict = json.loads((out / "verdict.json").read_text())
    assert verdict["verdict_original"]["verdict"] == VERDICT_NOT_NON_INFERIOR
    assert verdict["verdict_revised"]["verdict"] == VERDICT_NON_INFERIOR
    assert verdict["verdict_revised"]["post_hoc"] is True
    capsys.readouterr()


def test_compare_command_without_out_dir_only_prints_the_mcnemar_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, b = pair_with_gap(5, 5)
    for name, rows in (("a", a), ("b", b)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "results.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
    assert main(["compare", str(tmp_path / "a"), str(tmp_path / "b")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mcnemar_p"] == 1.0 and not (tmp_path / "verdict.json").exists()


def test_compare_command_reads_correctness_from_judgements_jsonl(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """EnronQA の results.jsonl は正誤を持たず，採点は judgements.jsonl にある（回帰：実機の実行で no paired questions になった）．"""
    a, b = pair_with_gap(60, 20)
    for name, rows in (("a", a), ("b", b)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "results.jsonl").write_text(
            "".join(json.dumps({"qid": r["qid"], "error": None}) + "\n" for r in rows),
            encoding="utf-8",
        )
        (tmp_path / name / "judgements.jsonl").write_text(
            "".join(
                json.dumps({"qid": r["qid"], "correct": r["correct"], "answer_overlap_lcs": 0.0})
                + "\n"
                for r in rows
            ),
            encoding="utf-8",
        )
    assert main(["compare", str(tmp_path / "a"), str(tmp_path / "b")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert (printed["only_a_correct"], printed["only_b_correct"]) == (60, 20)
