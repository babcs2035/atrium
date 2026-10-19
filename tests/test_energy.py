"""atrium.energy（RAPL の折り返し，GPU の台形積分，1 問あたりのエネルギー，CSV の読み込み）の単体テスト．"""

from __future__ import annotations

from pathlib import Path

import pytest

from atrium.energy import (
    EnergySample,
    integrate_gpu_joules,
    integrate_rapl_joules,
    joules_per_question,
    parse_energy_csv,
    rapl_delta_uj,
    summarize_directory,
    summarize_host,
)

MAX_RANGE = 1_000_000


def test_rapl_delta_without_wrap() -> None:
    assert rapl_delta_uj(100, 350, MAX_RANGE) == 250


def test_rapl_delta_wraps_when_counter_decreases() -> None:
    assert rapl_delta_uj(990_000, 10_000, MAX_RANGE) == 20_000


def test_integrate_rapl_handles_wrap_in_sequence() -> None:
    samples = [EnergySample(float(i), None, uj) for i, uj in enumerate([900_000, 950_000, 20_000])]
    assert integrate_rapl_joules(samples, MAX_RANGE) == pytest.approx(0.12)


def test_integrate_rapl_needs_two_values() -> None:
    assert integrate_rapl_joules([EnergySample(0.0, None, 5)], MAX_RANGE) is None
    assert (
        integrate_rapl_joules(
            [EnergySample(0.0, 1.0, None), EnergySample(1.0, 1.0, None)], MAX_RANGE
        )
        is None
    )


def test_integrate_gpu_uses_trapezoid() -> None:
    samples = [
        EnergySample(0.0, 10.0, None),
        EnergySample(1.0, 30.0, None),
        EnergySample(3.0, 30.0, None),
    ]
    assert integrate_gpu_joules(samples) == pytest.approx(20.0 + 60.0)


def test_integrate_gpu_none_when_no_gpu() -> None:
    assert (
        integrate_gpu_joules([EnergySample(0.0, None, None), EnergySample(1.0, None, None)]) is None
    )


def test_joules_per_question() -> None:
    assert joules_per_question(300.0, 4) == pytest.approx(75.0)


def test_joules_per_question_rejects_zero_questions() -> None:
    with pytest.raises(ValueError):
        joules_per_question(10.0, 0)


def test_parse_skips_truncated_last_line_and_reads_max_range() -> None:
    text = "# max_energy_range_uj=1000\n1.0,5.0,100\n2.0,,200\n3.0,7"
    samples, max_range = parse_energy_csv(text)
    assert max_range == 1000
    assert samples == [EnergySample(1.0, 5.0, 100), EnergySample(2.0, None, 200)]


def test_summarize_host_gpu_only_has_no_cpu_joules() -> None:
    energy = summarize_host("1.0,10.0,\n2.0,10.0,\n")
    assert energy.gpu_joules == pytest.approx(10.0)
    assert energy.cpu_joules is None
    assert energy.duration_s == pytest.approx(1.0)


def test_summarize_directory_totals_and_per_question(tmp_path: Path) -> None:
    (tmp_path / "hostA.csv").write_text("1.0,10.0,\n3.0,10.0,\n")
    (tmp_path / "hostB.csv").write_text("# max_energy_range_uj=1000\n1.0,,100\n2.0,,300\n")
    result = summarize_directory(tmp_path, 4)
    assert result["hostA"]["gpu_joules"] == pytest.approx(20.0)
    assert result["hostB"]["cpu_joules"] == pytest.approx(0.0002)
    assert result["total"]["gpu_joules_per_question"] == pytest.approx(5.0)


def test_summarize_directory_total_is_none_when_nothing_was_readable(tmp_path: Path) -> None:
    (tmp_path / "hostA.csv").write_text("1.0,10.0,\n3.0,10.0,\n")
    total = summarize_directory(tmp_path, 2)["total"]
    assert total["cpu_joules"] is None
    assert total["cpu_joules_per_question"] is None
