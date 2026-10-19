"""エネルギーの記録（scripts/remote/energy_log.sh が書く 1 秒ごとの CSV）の積分と，1 問あたりのエネルギーの計算（p0007 K5）．

CSV の形式（標準ライブラリだけで読めるよう，制御点の python3 から `python3 -m atrium.energy` でも呼べる）:
  `# max_energy_range_uj=<整数>`（RAPL を読めるホストだけ）の後に `unix_time,gpu_w,rapl_uj` の行が続く．
  gpu_w は GPU の消費電力の合計（W．GPU が無いと空），rapl_uj は intel-rapl:0 の energy_uj（読めないと空）．

使い方:
  PYTHONPATH=src python3 -m atrium.energy <csv のディレクトリ> [<問の数>]
  → ホストごとの積分（J）と，問の数があれば 1 問あたり（J/問）を JSON で標準出力へ出す．
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_RANGE_PREFIX = "# max_energy_range_uj="
MICROJOULES_PER_JOULE = 1_000_000.0


@dataclass(frozen=True)
class EnergySample:
    """1 秒ごとの標本 1 行．"""

    unix_time: float
    gpu_w: float | None
    rapl_uj: int | None


@dataclass(frozen=True)
class HostEnergy:
    """1 ホストの区間の積分．読めなかった項目は None．"""

    duration_s: float
    gpu_joules: float | None
    cpu_joules: float | None


def rapl_delta_uj(previous_uj: int, current_uj: int, max_energy_range_uj: int) -> int:
    """RAPL のカウンタの 2 点の差（µJ）．カウンタは max_energy_range_uj で 0 へ折り返すので，減ったら 1 周分を足す．"""
    if current_uj >= previous_uj:
        return current_uj - previous_uj
    return current_uj + max_energy_range_uj - previous_uj


def integrate_rapl_joules(samples: list[EnergySample], max_energy_range_uj: int) -> float | None:
    """RAPL のカウンタの差の合計（J）．値のある標本が 2 つ未満なら None．"""
    values = [s.rapl_uj for s in samples if s.rapl_uj is not None]
    if len(values) < 2:
        return None
    total_uj = sum(
        rapl_delta_uj(a, b, max_energy_range_uj) for a, b in zip(values, values[1:], strict=False)
    )
    return total_uj / MICROJOULES_PER_JOULE


def integrate_gpu_joules(samples: list[EnergySample]) -> float | None:
    """GPU の消費電力（W）を台形則で時間積分する（J）．値のある標本が 2 つ未満なら None．"""
    points = [(s.unix_time, s.gpu_w) for s in samples if s.gpu_w is not None]
    if len(points) < 2:
        return None
    return sum(
        (t1 - t0) * (w0 + w1) / 2.0 for (t0, w0), (t1, w1) in zip(points, points[1:], strict=False)
    )


def joules_per_question(total_joules: float, question_count: int) -> float:
    """区間の積分（J）を処理した問の数で割る．問の数が 1 未満なら ValueError．"""
    if question_count < 1:
        raise ValueError(f"question_count must be >= 1: {question_count}")
    return total_joules / question_count


def parse_energy_csv(text: str) -> tuple[list[EnergySample], int | None]:
    """energy_log.sh の CSV を（標本の列，max_energy_range_uj）へ読む．壊れた行（途中で切れた最終行など）は読み飛ばす．"""
    samples: list[EnergySample] = []
    max_range: int | None = None
    for line in text.splitlines():
        if line.startswith(MAX_RANGE_PREFIX):
            max_range = int(line[len(MAX_RANGE_PREFIX) :])
            continue
        fields = line.split(",")
        if line.startswith("#") or len(fields) != 3:
            continue
        try:
            samples.append(
                EnergySample(
                    unix_time=float(fields[0]),
                    gpu_w=float(fields[1]) if fields[1] else None,
                    rapl_uj=int(fields[2]) if fields[2] else None,
                )
            )
        except ValueError:
            continue
    return samples, max_range


def summarize_host(text: str) -> HostEnergy:
    """1 ホストの CSV の全区間の積分．"""
    samples, max_range = parse_energy_csv(text)
    duration = samples[-1].unix_time - samples[0].unix_time if len(samples) >= 2 else 0.0
    cpu = integrate_rapl_joules(samples, max_range) if max_range is not None else None
    return HostEnergy(duration_s=duration, gpu_joules=integrate_gpu_joules(samples), cpu_joules=cpu)


def summarize_directory(
    csv_dir: Path, question_count: int | None
) -> dict[str, dict[str, float | None]]:
    """ディレクトリの <host>.csv をホストごとに積分し，全ホストの合計（total）を加える．"""
    result: dict[str, dict[str, float | None]] = {}
    for path in sorted(csv_dir.glob("*.csv")):
        energy = summarize_host(path.read_text())
        result[path.stem] = {
            "duration_s": energy.duration_s,
            "gpu_joules": energy.gpu_joules,
            "cpu_joules": energy.cpu_joules,
        }
    # 1 台も読めなかった項目は 0 J ではなく None にする（0 と読めなかったことを区別するため）
    gpu_values = [v["gpu_joules"] for v in result.values() if v["gpu_joules"] is not None]
    cpu_values = [v["cpu_joules"] for v in result.values() if v["cpu_joules"] is not None]
    gpu_total = sum(gpu_values) if gpu_values else None
    cpu_total = sum(cpu_values) if cpu_values else None
    total: dict[str, float | None] = {"gpu_joules": gpu_total, "cpu_joules": cpu_total}
    if question_count is not None:
        total["gpu_joules_per_question"] = (
            None if gpu_total is None else joules_per_question(gpu_total, question_count)
        )
        total["cpu_joules_per_question"] = (
            None if cpu_total is None else joules_per_question(cpu_total, question_count)
        )
    result["total"] = total
    return result


def main(argv: list[str]) -> int:
    """コマンドライン: <csv のディレクトリ> [<問の数>]．"""
    if not 2 <= len(argv) <= 3:
        print("usage: python3 -m atrium.energy <csv_dir> [<question_count>]", file=sys.stderr)
        return 2
    count = int(argv[2]) if len(argv) == 3 else None
    print(json.dumps(summarize_directory(Path(argv[1]), count), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
