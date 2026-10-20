"""p0007 A3 のゲートの規則（atrium.frontier_a3）：族ごとの dev の選択・要求数の上限・ゲートの判定．合成の frontier の行だけを使う．"""

from __future__ import annotations

from typing import Any

from atrium.frontier_a3 import BASELINE_PARAMS, gate, pick_in_family, requests_at


def _row(family: str, method: str, split: str, d: int, hit3: float, **extra: Any) -> dict[str, Any]:
    """frontier.json の 1 行（m = 2 の実行．要求数 = D + 2）．"""
    return {
        "family": family,
        "method": method,
        "params": {"V": 64000, "w": 0.5, "D": d, **extra},
        "split": split,
        "subset": "all",
        "hit@3": hit3,
        "requests_per_query": {"mean": float(d + 2)},
    }


def _rows(bm25_test_d24: float, bm25_dev_d30: float = 0.99) -> list[dict[str, Any]]:
    """基準（D = 16．hit@3 0.84）と，bm25_shared の D = 24・30，他の族 2 つ．"""
    rows = [_row("hybrid_summary", "hybrid_summary", "test", 16, 0.84)]
    rows[0]["params"] = dict(BASELINE_PARAMS)
    for split in ("dev", "test"):
        rows.append(
            _row(
                "bm25_shared",
                "bm25_probe",
                split,
                24,
                0.9 if split == "dev" else bm25_test_d24,
                idf="exact",
            )
        )
        rows.append(_row("bm25_shared", "bm25_probe", split, 30, bm25_dev_d30, idf="exact"))
        rows.append(_row("bm25_fuse", "bm25_probe", split, 24, 0.8, idf="exact", wb=1.0))
        rows.append(_row("hybrid_xl", "hybrid_xl", split, 24, 0.7))
    # A 群を除く部分集合の行（test）．値は全問と同じにしてよい（選択の規則には使わない）
    rows += [dict(r, subset="no_owner_name") for r in rows if r["split"] == "test"]
    return rows


def test_pick_in_family_excludes_configs_above_the_request_limit() -> None:
    picked = pick_in_family(_rows(0.9), "bm25_shared", 3, 28.5)
    assert (
        picked["params"]["D"] == 24
    )  # D = 30（33 要求）は上限 28.5 を超える．dev の hit が高くても選ばない


def test_gate_passes_only_with_a_gain_of_at_least_0_05_within_the_request_limit() -> None:
    rows = _rows(bm25_test_d24=0.90)
    baseline = rows[0]
    verdict = gate(rows, baseline, 3)
    assert requests_at(baseline, 3) == 19.0
    assert verdict["selected_family"] == "bm25_shared"
    assert verdict["passed"] is True
    assert verdict["by_family"]["hybrid_xl"]["passed"] is False  # 利得が 0.05 に届かない
    rows_small_gain = _rows(bm25_test_d24=0.88)
    assert gate(rows_small_gain, rows_small_gain[0], 3)["passed"] is False
