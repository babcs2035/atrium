"""freshness-run・failure-run・join-run（p0007 I5〜I7）が共有する小さな部品：時刻合わせ・質問と正解の対応・JSON の書き出し．"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from atrium.benchmarks import Question, load_questions
from atrium.data_enronqa import read_gold
from atrium.paths import DatasetPaths

# 待ち時間の刻み（これより細かい待ちは asyncio.sleep の精度に任せる）
MIN_SLEEP_S = 0.0


async def sleep_until(origin: float, offset_s: float) -> None:
    """time.perf_counter() が origin + offset_s になるまで待つ（既に過ぎていれば待たない）．"""
    delay = origin + offset_s - time.perf_counter()
    await asyncio.sleep(max(MIN_SLEEP_S, delay))


def write_json(path: Path, value: Mapping[str, Any] | Sequence[Any]) -> None:
    """JSON を書く（親ディレクトリを作る．無限大は書けないので呼び出し側が None にしておく）．"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False, allow_nan=False), "utf-8")


def load_test_questions(paths: DatasetPaths) -> tuple[list[Question], dict[str, dict[str, Any]]]:
    """test の全質問と，質問 ID → 正解の情報（受信箱・メールの path）を読む．"""
    return load_questions(paths.questions, None), read_gold(paths)


def is_hit(record: Mapping[str, Any], doc_id: str) -> bool:
    """質問者の結果の 1 行が，正解のメール doc_id を上位の文脈に含み，失敗していないか（「答えられる」状態の判定）．"""
    return record.get("error") is None and doc_id in record.get("top_doc_ids", [])
