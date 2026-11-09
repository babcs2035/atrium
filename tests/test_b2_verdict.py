"""scripts/d0009/b2_verdict.py（採点を GPU に分けた測定のゲート G-S と有効な実行の判定）の単体テスト．"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "d0009" / "b2_verdict.py"


def load_script() -> ModuleType:
    """scripts/ はパッケージではないので，パスから読み込む．"""
    spec = importlib.util.spec_from_file_location("b2_verdict", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B2 = load_script()


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def make_gs_run(tmp_path: Path, remote_scores: dict[tuple[int, str], float]) -> tuple[Path, Path]:
    """2 候補・各 1 通の 1 問（q1）の表と，server 経由の得点（候補の番号・文書 ID → 得点）を作る．"""
    table = tmp_path / "table.jsonl"
    write_jsonl(
        table,
        [
            {"qid": "q1", "query": "x", "doc_id": "d0", "score": 1.0},
            {"qid": "q1", "query": "x", "doc_id": "d1", "score": 1.0005},
        ],
    )
    run = tmp_path / "gs"
    run.mkdir()
    write_jsonl(
        run / "scores.jsonl",
        [
            {"qid": "q1", "candidate": c, "doc_id": d, "score": s, "target": 0}
            for (c, d), s in remote_scores.items()
        ],
    )
    (run / "score_bench.json").write_text(
        json.dumps({"n_errors": 0, "n_questions_input": 1}), encoding="utf-8"
    )
    return run, table


def test_gs_tie_in_the_table_is_tolerated_when_the_pick_differs(tmp_path: Path) -> None:
    """表の上位 2 候補の差が 2e-3 未満なら，選択の食い違いは同点として数えるだけで，得点が 1e-3 以内なら通る（組の数の条件は別）．"""
    run, table = make_gs_run(tmp_path, {(0, "d0"): 1.0006, (1, "d1"): 1.0005})
    result = B2.gate_gs(run, table)
    assert result["n_pairs_over_tolerance"] == 0
    assert result["n_selection_mismatch"] == 0
    assert result["n_selection_mismatch_tolerated_as_tie"] == 1
    # 4,320 組でないので全体としては不合格（本番の入力は 288 問の全組）
    assert result["pass"] is False


def test_gs_score_difference_over_tolerance_is_counted(tmp_path: Path) -> None:
    run, table = make_gs_run(tmp_path, {(0, "d0"): 1.01, (1, "d1"): 1.0005})
    result = B2.gate_gs(run, table)
    assert result["n_pairs_over_tolerance"] == 1
    assert abs(result["max_abs_diff"] - 0.01) < 1e-9


def test_gs_pick_mismatch_beyond_the_tie_gap_is_a_failure(tmp_path: Path) -> None:
    table = tmp_path / "table.jsonl"
    write_jsonl(
        table,
        [
            {"qid": "q1", "query": "x", "doc_id": "d0", "score": 1.0},
            {"qid": "q1", "query": "x", "doc_id": "d1", "score": 2.0},
        ],
    )
    run = tmp_path / "gs"
    run.mkdir()
    write_jsonl(
        run / "scores.jsonl",
        [
            {"qid": "q1", "candidate": 0, "doc_id": "d0", "score": 3.0, "target": 1},
            {"qid": "q1", "candidate": 1, "doc_id": "d1", "score": 2.0, "target": 0},
        ],
    )
    (run / "score_bench.json").write_text(json.dumps({"n_errors": 0, "n_questions_input": 1}))
    result = B2.gate_gs(run, table)
    assert result["n_selection_mismatch"] == 1
    assert result["max_abs_diff_by_target"]["1"] == 2.0


def write_run(run: Path, temp: float, sw: str, counter_after: int, other: int, errors: int) -> None:
    run.mkdir()
    (run / "score_bench.json").write_text(
        json.dumps(
            {
                "n_errors": errors,
                "n_request_errors_total": errors,
                "requests_per_target_in_window": [5],
            }
        ),
        encoding="utf-8",
    )
    (run / "snap_h_pre.txt").write_text("counter_us=10\nother_procs=0\n", encoding="utf-8")
    (run / "snap_h_post.txt").write_text(
        f"counter_us={counter_after}\nother_procs={other}\n", encoding="utf-8"
    )
    (run / "gpu_h.csv").write_text(f"1.0,{temp},50,1000,{sw},NotActive\n", encoding="utf-8")


def test_check_run_accepts_a_cool_run_without_failures(tmp_path: Path) -> None:
    write_run(tmp_path / "ok", 70, "NotActive", 10, 0, 0)
    assert B2.check_run(tmp_path / "ok", ["h"], smoke=False)["valid"] is True


def test_check_run_rejects_each_invalidating_condition(tmp_path: Path) -> None:
    cases = {
        "hot": (83, "NotActive", 10, 0, 0),
        "slow": (70, "Active", 10, 0, 0),
        "counter": (70, "NotActive", 11, 0, 0),
        "other": (70, "NotActive", 10, 1, 0),
        "errors": (70, "NotActive", 10, 0, 1),
    }
    for name, (temp, sw, counter, other, errors) in cases.items():
        write_run(tmp_path / name, temp, sw, counter, other, errors)
        result = B2.check_run(tmp_path / name, ["h"], smoke=False)
        assert result["valid"] is False, name


def test_round_orders_follow_the_preregistered_shuffle_with_seed_37() -> None:
    """格子が既定の {1,2,4,8} のとき，ラウンドごとの G の順は事前登録の種 37 の並べ替えの結果．"""
    assert B2.round_orders() == [[2, 8, 4, 1], [8, 1, 2, 4], [1, 2, 4, 8]]
    assert B2.DEFAULT_GRID == [1, 2, 4, 8]
