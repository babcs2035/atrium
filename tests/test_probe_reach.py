"""probe-reach（atrium.probe_reach）の集計の規則の仕様．"""

from __future__ import annotations

import numpy as np

from atrium.probe_reach import candidates_of, devices_reached, reach_for_set, summarize_counts

# 4 受信箱を 2 台に置く：ノード 0 が a・b，ノード 1 が c・d
NODE_OF = np.array([0, 0, 1, 1], np.int64)
FIRST = np.array([[0.9, 0.8, 0.1, 0.2], [0.1, 0.2, 0.9, 0.8]], np.float32)
SECOND = np.array([[0.1, 0.7, 0.9, 0.6], [0.5, 0.4, 0.3, 0.6]], np.float32)


def test_candidates_are_the_top_k_of_the_first_stage_with_ties_to_the_smaller_index() -> None:
    first = np.array([[0.5, 0.5, 0.9]], np.float32)
    assert candidates_of(first, 2).tolist() == [[2, 0]]


def test_devices_reached_counts_each_device_once() -> None:
    assert devices_reached(candidates_of(FIRST, 2), NODE_OF).tolist() == [1, 1]
    assert devices_reached(candidates_of(FIRST, 3), NODE_OF).tolist() == [2, 2]


def test_summarize_counts_gives_histogram_and_range() -> None:
    summary = summarize_counts(np.array([1, 2, 2, 2], np.int64))
    assert summary["histogram"] == {"1": 1, "2": 3}
    assert (summary["min"], summary["max"], summary["median"]) == (1, 2, 2.0)


def test_recall_at_k_is_the_share_of_gold_inboxes_in_the_first_stage_top_k() -> None:
    names = ["a", "b", "c", "d"]
    result = reach_for_set(FIRST, SECOND, [0, 3], NODE_OF, names, 2)
    by_k = {r["k"]: r for r in result["by_k"]}
    # 問 0 の正解 a は 1 位，問 1 の正解 d は 2 位
    assert by_k[4]["recall_at_k"] == 1.0
    assert by_k[8]["recall_at_k"] == 1.0
