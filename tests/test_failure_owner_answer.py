"""障害の測定の owner_answer 対応（d0009 A6．atrium.failure_run・atrium.live_common）の仕様：質問集合の選び方・「答えられた」の判定・失敗の形・併記の集計．"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from atrium.benchmarks import Question, write_questions
from atrium.failure_run import (
    FAILURE_DEADLINE,
    FAILURE_HTTP,
    FAILURE_OTHER,
    FAILURE_TABLE_MISS,
    FailureParams,
    classify_failure,
    owner_row_fields,
    summarize_owner_rows,
)
from atrium.live_common import (
    has_owner_candidate_with_gold,
    is_owner_answered,
    load_split_questions,
)
from atrium.paths import dataset_paths

GOLD = "bob/inbox/3."


def _record(
    chosen: int | None = 1, error: str | None = None, with_candidates: bool = True
) -> dict[str, Any]:
    """3 候補（alice・bob・carol）の owner_answer の結果の 1 行．正解のメール GOLD は bob の文脈にだけある．"""
    candidates = [
        {"source": "alice", "context_doc_ids": ["alice/inbox/0."]},
        {"source": "bob", "context_doc_ids": ["bob/inbox/1.", GOLD]},
        {"source": "carol", "context_doc_ids": []},
    ]
    owner: dict[str, Any] = {"chosen_index": chosen}
    if with_candidates:
        owner["candidates"] = candidates
    return {"error": error, "owner_answer": owner}


def test_answered_when_the_chosen_candidate_context_has_the_gold_mail() -> None:
    assert is_owner_answered(_record(chosen=1), GOLD)


def test_not_answered_when_only_another_candidate_has_the_gold_mail() -> None:
    assert not is_owner_answered(_record(chosen=0), GOLD)


def test_not_answered_when_the_question_failed_even_if_the_gold_mail_is_in_the_context() -> None:
    assert not is_owner_answered(_record(chosen=1, error="HTTPStatusError: x"), GOLD)


def test_not_answered_when_no_candidate_was_saved() -> None:
    assert not is_owner_answered(_record(with_candidates=False), GOLD)
    assert not is_owner_answered({"error": None}, GOLD)


def test_owner_candidate_counts_regardless_of_which_candidate_was_chosen() -> None:
    assert has_owner_candidate_with_gold(_record(chosen=0), "bob", GOLD)
    assert not has_owner_candidate_with_gold(_record(chosen=1), "alice", GOLD)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, (None, None, None)),
        ("KeyError: ('q', 'bob/inbox/1.')", (FAILURE_TABLE_MISS, "KeyError", None)),
        (
            "HTTPStatusError: Server error '503 Service Unavailable' for url 'http://x/v1/answer'",
            (FAILURE_HTTP, "HTTPStatusError", 503),
        ),
        ("ConnectError: All connection attempts failed", (FAILURE_HTTP, "ConnectError", None)),
        ("ValueError: bad", (FAILURE_OTHER, "ValueError", None)),
    ],
)
def test_failure_kind_is_taken_from_the_exception_type(
    error: str | None, expected: tuple[str | None, str | None, int | None]
) -> None:
    assert classify_failure(error) == expected
    assert issubclass(httpx.ConnectError, httpx.HTTPError)


def test_owner_row_fields_report_return_owner_and_failure_kind() -> None:
    ok = owner_row_fields(_record(chosen=0), "bob", GOLD)
    assert ok["returned"] and ok["owner_hit"] and ok["failure_kind"] is None
    failed = owner_row_fields(_record(error="KeyError: x"), "bob", GOLD)
    assert not failed["returned"] and failed["failure_kind"] == FAILURE_TABLE_MISS


def _row(
    asked_at: float,
    *,
    hit: bool,
    returned: bool = True,
    owner_hit: bool = True,
    e2e_s: float = 1.0,
    kind: str | None = None,
) -> dict[str, Any]:
    return {
        "asked_at": asked_at,
        "hit": hit,
        "returned": returned,
        "owner_hit": owner_hit,
        "e2e_s": e2e_s,
        "failure_kind": kind,
        "http_status": None,
    }


def test_owner_summary_splits_baseline_and_fault_and_counts_failure_kinds() -> None:
    params = FailureParams(answer_deadline_s=10.0, down_s=5.0)
    rows = [
        _row(1.0, hit=True, e2e_s=3.0),  # baseline（止める頼みの 2.0 より前）
        _row(1.5, hit=True, e2e_s=8.0),  # baseline．締め切りの半分（5 秒）を超える
        _row(3.0, hit=False, returned=False, owner_hit=False, e2e_s=10.0, kind=FAILURE_DEADLINE),
        _row(4.0, hit=True, e2e_s=4.0),
        _row(5.0, hit=False, returned=False, owner_hit=False, e2e_s=0.1, kind=FAILURE_TABLE_MISS),
    ]
    # t0 = 2.5 として止める頼みを 2.0 に出した場合の，t0 からの秒
    rel = [r["asked_at"] - 2.5 for r in rows]
    owner = summarize_owner_rows(rows, rel, 2.0, params)["owner"]
    assert owner["baseline"]["n"] == 2
    assert owner["baseline"]["rates"]["hit"] == 1.0
    assert owner["baseline"]["rates"]["hit_within_half_deadline"] == 0.5
    assert owner["fault"]["n"] == 3
    assert owner["fault"]["rates"]["returned"] == pytest.approx(1 / 3)
    assert owner["fault"]["failure_kinds"] == {FAILURE_DEADLINE: 1, FAILURE_TABLE_MISS: 1}


def test_owner_summary_is_empty_for_rows_without_owner_fields() -> None:
    rows = [{"asked_at": 1.0, "hit": True}]
    assert summarize_owner_rows(rows, [0.0], 0.0, FailureParams()) == {}


def _write_split(data_dir: Path) -> None:
    bench = data_dir / "enronqa" / "benchmark"
    bench.mkdir(parents=True)
    tests = [Question(f"t{i}", "bob", f"tq{i}", {}, None) for i in range(3)]
    holdout = [Question(f"h{i}", b, f"hq{i}", {}, None) for i, b in enumerate("aabbb")]
    write_questions(bench / "questions.jsonl", tests)
    write_questions(bench / "holdout_questions.jsonl", holdout)
    for name, rows in (("gold.jsonl", tests), ("holdout_gold.jsonl", holdout)):
        with (bench / name).open("w", encoding="utf-8") as f:
            for q in rows:
                f.write(json.dumps({"qid": q.qid, "bank": q.bank, "path": f"{q.bank}/x"}) + "\n")


def test_split_questions_are_limited_per_inbox_and_test_with_no_limit_is_unchanged(
    tmp_path: Path,
) -> None:
    _write_split(tmp_path)
    paths = dataset_paths(tmp_path, "enronqa")
    questions, gold = load_split_questions(paths, "holdout", 1)
    assert [q.qid for q in questions] == ["h0", "h2"]
    assert gold["h2"]["path"] == "b/x"
    tests, _ = load_split_questions(paths, "test", None)
    assert [q.qid for q in tests] == ["t0", "t1", "t2"]
