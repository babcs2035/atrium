"""scripts/d0009/a4_exposure_total.py（委託先を含めた露出の全体の集計）の単体テスト．"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "d0009" / "a4_exposure_total.py"


def load_script() -> ModuleType:
    """scripts/ はパッケージではないので，パスから読み込む．"""
    spec = importlib.util.spec_from_file_location("a4_exposure_total", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


A4 = load_script()

DELEGATE_OF = {"cpu1": "gpu1"}
INBOXES = {"cpu1": {"a"}, "gpu1": {"b"}}


def make_row(
    candidates: dict[str, list[str]],
    delegated: dict[str, str | None],
    error: str | None = None,
) -> dict[str, Any]:
    """候補の source → 文脈の文書 ID と，node_answers の source → delegated_to から 1 問の行を作る．"""
    row: dict[str, Any] = {
        "qid": "q",
        "error": error,
        "owner_answer": {
            "candidates": [{"source": s, "context_doc_ids": ids} for s, ids in candidates.items()]
        },
        # node_answers の順序は candidates と逆にして，index に頼らないことを確かめる
        "node_answers": [
            {"node_id": "cpu1" if s == "a" else "gpu1", "source": s, "delegated_to": d}
            for s, d in reversed(list(delegated.items()))
        ],
        "docs_exposed_by_source": {
            s: len(candidates[s]) for s, d in delegated.items() if d is not None
        },
    }
    return row


def test_only_delegated_candidates_reach_the_gpu_and_requester_gets_all() -> None:
    row = make_row({"a": ["a/1", "a/2"], "b": ["b/1"]}, {"a": "gpu1", "b": None})
    assert A4.recipient_docs_of_row(row) == {
        "wafl-ctrl5": {"a/1", "a/2", "b/1"},
        "gpu1": {"a/1", "a/2"},
    }


def test_error_row_has_no_recipients() -> None:
    row = make_row({"a": ["a/1"]}, {"a": "gpu1"}, error="timeout")
    assert A4.recipient_docs_of_row(row) == {}


def test_recipient_does_not_count_documents_of_its_own_inbox() -> None:
    row = make_row({"a": ["a/1"], "b": ["b/1"]}, {"a": "gpu1", "b": "gpu1"})
    acc: dict[str, set[str]] = {}
    excluded = A4.accumulate([row], INBOXES, acc)
    assert acc == {"wafl-ctrl5": {"a/1", "b/1"}, "gpu1": {"a/1"}}
    assert excluded == 1


def test_union_and_pair_counts_with_delegation_subset_of_requester_docs() -> None:
    rows = [
        make_row({"a": ["a/1", "a/2"], "b": ["b/1"]}, {"a": "gpu1", "b": None}),
        make_row({"a": ["a/2", "a/3"]}, {"a": "gpu1"}),
    ]
    acc: dict[str, set[str]] = {}
    A4.accumulate(rows, INBOXES, acc)
    m = A4.exposure_metrics(acc, 10, INBOXES, {"a": 5, "b": 5}, {"gpu1"})
    assert (m["docs_req"], m["docs_deleg"], m["docs_any_total"]) == (4, 3, 4)
    assert m["docs_pair_total"] == 7
    assert m["docs_max_gpu"] == 3
    assert m["docs_with_2plus_recipients"] == 3
    assert m["E_any_total"] == 0.4


def test_delegation_gate_detects_wrong_target_and_sum_mismatch() -> None:
    good = make_row({"a": ["a/1"]}, {"a": "gpu1"})
    wrong = make_row({"a": ["a/1"]}, {"a": "gpu9"})
    gate = A4.delegation_gate([good, wrong], DELEGATE_OF)
    assert gate["n_delegated_candidates"] == 2
    assert gate["rates"]["delegated_to_mismatch"] == 0.5
    assert gate["G_D_passes"] is False

    broken = make_row({"a": ["a/1"]}, {"a": "gpu1"})
    broken["docs_exposed_by_source"] = {"a": 4}
    assert A4.delegation_gate([broken], DELEGATE_OF)["n_questions_docs_exposed_sum_mismatch"] == 1


def test_delegation_gate_skips_error_rows() -> None:
    err = make_row({"a": ["a/1"]}, {"a": "gpu1"}, error="x")
    gate = A4.delegation_gate([err], DELEGATE_OF)
    assert gate["n_questions"] == 0 and gate["G_D_passes"] is True
