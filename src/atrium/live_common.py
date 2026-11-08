"""freshness-run・failure-run・join-run（p0007 I5〜I7）が共有する小さな部品：時刻合わせ・質問と正解の対応・JSON の書き出し．"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from atrium.benchmarks import Question, load_questions
from atrium.data_enronqa import QUESTION_FILES, read_gold
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


def load_split_questions(
    paths: DatasetPaths, split: str, limit: int | None
) -> tuple[list[Question], dict[str, dict[str, Any]]]:
    """分割（test・holdout など）の質問を，各受信箱の先頭 limit 問（None なら全問）まで読む．test で limit が None なら `load_test_questions` と同じ．

    experiment.question_split・question_limit（`atrium start` の質問の選び方）と同じ読み方にして，failure-run が主構成の質問（d0009 A6）を流せるようにする．
    """
    if split == "test" and limit is None:
        return load_test_questions(paths)
    questions_path = paths.questions.parent / QUESTION_FILES[split]
    return load_questions(questions_path, limit), read_gold(paths)


def is_hit(record: Mapping[str, Any], doc_id: str) -> bool:
    """質問者の結果の 1 行が，正解のメール doc_id を上位の文脈に含み，失敗していないか（「答えられる」状態の判定）．"""
    return record.get("error") is None and doc_id in record.get("top_doc_ids", [])


def is_owner_answered(record: Mapping[str, Any], doc_id: str) -> bool:
    """owner_answer の結果の 1 行が，失敗せずに，選んだ候補の文脈に正解のメール doc_id を含むか（d0009 A6 障害の「答えられた」）．

    `top_doc_ids` を持たない owner_answer の行のための判定で，answer.save_candidates の `context_doc_ids` を使う．正誤の採点はしない．
    """
    if record.get("error") is not None:
        return False
    owner = record.get("owner_answer") or {}
    candidates = owner.get("candidates") or []
    chosen = owner.get("chosen_index")
    if chosen is None or not 0 <= chosen < len(candidates):
        return False
    return doc_id in candidates[chosen].get("context_doc_ids", [])


def has_owner_candidate_with_gold(record: Mapping[str, Any], bank: str, doc_id: str) -> bool:
    """正解の受信箱 bank が候補にあり，その候補の文脈に正解のメール doc_id があるか（選択の規則に依らない，持ち主の候補が入った割合）．"""
    candidates = (record.get("owner_answer") or {}).get("candidates") or []
    return any(
        c.get("source") == bank and doc_id in c.get("context_doc_ids", []) for c in candidates
    )


def install_sigterm_cancel(task: asyncio.Task[Any]) -> None:
    """SIGTERM（docker stop）を受けたら task を中断させる（finally で，止めたノードの再開・離脱させたノードの復帰をさせてから終わるため）．"""
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
