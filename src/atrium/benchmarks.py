"""ベンチマークの質問の読み込み．

中継点がデータセットごとの質問を共通の書式 `questions.jsonl` に変換しておき，質問者と分析はこれだけを読む．
1 行の書式: {"qid": "medqa/0000", "bank": "medqa", "question": "...", "options": {"A": "..."}, "answer": "A"}
qid は問題集の名前を前に付けて，問題集をまたいでも一意にする．
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

QUESTIONS_FILENAME = "questions.jsonl"


@dataclass(frozen=True)
class Question:
    """ベンチマークの質問 1 問．"""

    qid: str
    bank: str
    question: str
    options: dict[str, str]
    answer: str | None

    @property
    def source_qid(self) -> str:
        """元のデータセットでの ID（FeB4RAG の検索結果・qrels はこの ID で引く）．"""
        return self.qid.split("/", 1)[1]


def mirage_to_questions(raw: dict[str, dict[str, dict[str, object]]]) -> list[Question]:
    """MIRAGE の benchmark.json を質問の一覧へ変換する．"""
    out: list[Question] = []
    for bank in sorted(raw):
        for qid, item in raw[bank].items():
            options = item["options"]
            if not isinstance(options, dict):
                raise ValueError(f"{bank}/{qid}: options must be a dict")
            out.append(
                Question(
                    qid=f"{bank}/{qid}",
                    bank=bank,
                    question=str(item["question"]),
                    options={str(k): str(v) for k, v in options.items()},
                    answer=str(item["answer"]),
                )
            )
    return out


def feb4rag_to_questions(lines: Iterable[str]) -> list[Question]:
    """FeB4RAG の requests.jsonl を質問の一覧へ変換する（選択肢と正解はない）．"""
    out: list[Question] = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        out.append(
            Question(
                qid=f"feb4rag/{row['_id']}",
                bank="feb4rag",
                question=str(row["text"]),
                options={},
                answer=None,
            )
        )
    return out


def write_questions(path: Path, questions: Iterable[Question]) -> None:
    """質問を questions.jsonl へ書く．"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for q in questions:
            f.write(
                json.dumps(
                    {
                        "qid": q.qid,
                        "bank": q.bank,
                        "question": q.question,
                        "options": q.options,
                        "answer": q.answer,
                    }
                )
                + "\n"
            )


def load_questions(path: Path, limit_per_bank: int | None = None) -> list[Question]:
    """questions.jsonl を読む．limit_per_bank を与えると各問題集の先頭からその数だけにする．"""
    out: list[Question] = []
    taken: dict[str, int] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            bank = str(row["bank"])
            if limit_per_bank is not None and taken.get(bank, 0) >= limit_per_bank:
                continue
            taken[bank] = taken.get(bank, 0) + 1
            out.append(
                Question(
                    qid=row["qid"],
                    bank=bank,
                    question=row["question"],
                    options=row["options"],
                    answer=row["answer"],
                )
            )
    return out
