"""二段の方式の参加・離脱のオフラインの評価（atrium.churn_cascade_eval）の経路の選び方・分解・ゲート・判定の仕様．"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from atrium.advert_eval import ranks_of_gold
from atrium.arrays import F32Array
from atrium.cascade_eval import cascade_scores
from atrium.churn_cascade_eval import (
    HC2_BASELINE,
    HC2_METHOD,
    Cascade,
    gate_g4,
    judge_e1,
    judge_hc2,
    node_of_device,
    route_cascade_under_churn,
    run_record,
    single_stage_breakdown,
)
from atrium.churn_eval import (
    JUDGED_AVAILABILITY,
    REFRESH_STEPS,
    BoolArray,
    route_under_churn,
    simulate_availability,
    stale_view,
)
from atrium.device_probe_eval import device_candidate_mask, masked_scores

# 3 ノードに 2 受信箱ずつ．placement.nodes の順（NODE_OF）と受信箱の名前の初出順（デバイスの番号）をわざとずらす
NODE_OF = np.array([2, 2, 0, 0, 1, 1], np.int64)
DEVICE_OF = np.array([0, 0, 1, 1, 2, 2], np.int64)
DEVICE_NODE = np.array([2, 0, 1], np.int64)
M = 2
N_STEPS = 300


def _random_case(seed: int) -> tuple[F32Array, F32Array, list[int], BoolArray]:
    """乱数の採点・正解・時刻表（稼働率 0.6，稼働中の平均 5 刻み）．"""
    rng = np.random.default_rng(seed)
    first = rng.random((N_STEPS, 6)).astype(np.float32)
    second = rng.random((N_STEPS, 6)).astype(np.float32)
    gold = rng.integers(0, 6, N_STEPS).tolist()
    online = simulate_availability(3, N_STEPS, 0.6, 5, rng)
    return first, second, gold, online


def _route(
    first: F32Array,
    second: F32Array,
    gold: list[int],
    view: BoolArray,
    online: BoolArray,
    m: int,
    cascade: Cascade,
) -> Any:
    return route_cascade_under_churn(
        first, second, gold, NODE_OF, DEVICE_OF, DEVICE_NODE, view, online, m, cascade
    )


def test_device_numbers_map_to_the_placement_node_numbers() -> None:
    assert node_of_device(DEVICE_OF, NODE_OF).tolist() == DEVICE_NODE.tolist()


def test_a_device_spanning_two_nodes_is_rejected() -> None:
    with pytest.raises(ValueError):
        node_of_device(np.array([0, 0], np.int64), np.array([0, 1], np.int64))


@pytest.mark.parametrize("d", [1, 2, 3])
def test_without_churn_device_probe_matches_the_iteration7_rule(d: int) -> None:
    first, second, gold, _ = _random_case(0)
    everyone = np.ones((N_STEPS, 3), np.bool_)
    out, _ = _route(first, second, gold, everyone, everyone, M, Cascade("d", "device_probe", d))
    mask = device_candidate_mask(first, DEVICE_OF, d, "max")
    expected = ranks_of_gold(masked_scores(second, mask), gold) <= M
    assert out.hit.tolist() == expected.tolist()


@pytest.mark.parametrize("k", [2, 4, 6])
def test_without_churn_centroid_probe_matches_the_iteration1_rule(k: int) -> None:
    first, second, gold, _ = _random_case(0)
    everyone = np.ones((N_STEPS, 3), np.bool_)
    out, _ = _route(first, second, gold, everyone, everyone, M, Cascade("k", "centroid_probe", k))
    expected = ranks_of_gold(cascade_scores(first, second, k), gold) <= M
    assert out.hit.tolist() == expected.tolist()


@pytest.mark.parametrize("refresh", [1, 7, 50])
def test_full_candidate_sets_reproduce_flood_score_exactly(refresh: int) -> None:
    # G3：K = 受信箱の数・D = デバイスの数は，hit・probe したノード・離脱していたノードが flood_score と一致する
    first, second, gold, online = _random_case(1)
    view = stale_view(online, refresh)
    flood = route_under_churn(second, gold, NODE_OF, view, online, M, True)
    for cascade in (Cascade("k6", "centroid_probe", 6), Cascade("d3", "device_probe", 3)):
        out, _ = _route(first, second, gold, view, online, M, cascade)
        assert out.hit.tolist() == flood.hit.tolist()
        assert out.contacted.tolist() == flood.contacted.tolist()
        assert out.wasted.tolist() == flood.wasted.tolist()


def test_lost_questions_split_into_exclusive_causes_that_sum_to_the_questions() -> None:
    # G4：hit と (i)〜(iv) の和が問の数に等しい（二段の方式と 1 段の方式の両方）
    first, second, gold, online = _random_case(2)
    view = stale_view(online, 10)
    records = []
    for cascade in (Cascade("k3", "centroid_probe", 3), Cascade("d1", "device_probe", 1)):
        out, loss = _route(first, second, gold, view, online, M, cascade)
        records.append(run_record(cascade.name, 0.6, 10, 0, out, loss))
    for name, scores, flood in (("multi", first, False), ("flood", second, True)):
        out = route_under_churn(scores, gold, NODE_OF, view, online, M, flood)
        loss = single_stage_breakdown(scores, gold, NODE_OF, view, online, M, flood, out.hit)
        records.append(run_record(name, 0.6, 10, 0, out, loss))
    for r in records:
        total = (
            r["n_hit"]
            + r["n_gold_offline"]
            + r["n_gold_invisible"]
            + r["n_not_selected"]
            + r["n_slot_taken"]
        )
        assert total == N_STEPS
        assert r["n_gold_invisible"] > 0
    assert gate_g4(records)["passed"] is True
    assert records[0]["n_not_candidate"] + records[0]["n_not_top_m"] == records[0]["n_not_selected"]
    assert records[3]["n_slot_taken"] == 0


# 1 問．デバイス 0（受信箱 0・1，ノード 2），デバイス 1（受信箱 2・3，ノード 0），デバイス 2（受信箱 4・5，ノード 1）
FIRST = np.array([[0.9, 0.1, 0.8, 0.2, 0.3, 0.4]], np.float32)
SECOND = np.array([[0.5, 0.1, 0.9, 0.2, 0.3, 0.6]], np.float32)


def test_a_departed_candidate_is_wasted_but_does_not_take_the_final_slot() -> None:
    # ノード 0（受信箱 2 が 2 段目で最高）は離脱したが表に残る．正解は受信箱 0
    view = np.ones((1, 3), np.bool_)
    online = np.array([[False, True, True]])
    out, _ = _route(FIRST, SECOND, [0], view, online, 1, Cascade("d2", "device_probe", 2))
    assert out.hit.tolist() == [True]
    assert (out.contacted.tolist(), out.wasted.tolist()) == ([2], [1])
    # 上位 m 個を表から直接選ぶ 1 段の方式では，正解が受信箱 5（稼働中で 2 位）の問が，離脱済みの受信箱 2 に枠を取られる
    single = route_under_churn(SECOND, [5], NODE_OF, view, online, 1, False)
    loss = single_stage_breakdown(SECOND, [5], NODE_OF, view, online, 1, False, single.hit)
    assert single.hit.tolist() == [False]
    assert loss.slot_taken.tolist() == [True]


def test_device_probe_fills_its_devices_from_the_visible_ones_only() -> None:
    # 1 段目で最高のデバイス 0（ノード 2）は表で見えない．D = 1 は次のデバイス 1（ノード 0）だけへ送る
    view = np.array([[True, True, False]])
    online = np.ones((1, 3), np.bool_)
    out, loss = _route(FIRST, SECOND, [0], view, online, 1, Cascade("d1", "device_probe", 1))
    assert (out.contacted.tolist(), out.wasted.tolist()) == ([1], [0])
    assert out.hit.tolist() == [False]
    assert loss.gold_invisible.tolist() == [True]


def test_centroid_probe_splits_not_selected_into_not_candidate_and_not_top_m() -> None:
    everyone = np.ones((1, 3), np.bool_)
    cascade = Cascade("k2", "centroid_probe", 2)
    # 1 段目の上位 2 個は受信箱 0・2．受信箱 5 は候補に入らない
    _, outside = _route(FIRST, SECOND, [5], everyone, everyone, 1, cascade)
    assert outside.not_candidate is not None and outside.not_candidate.tolist() == [True]
    # 受信箱 0 は候補だが，2 段目で受信箱 2 に負けて上位 1 個に入らない
    _, beaten = _route(FIRST, SECOND, [0], everyone, everyone, 1, cascade)
    assert beaten.not_top_m is not None and beaten.not_top_m.tolist() == [True]


def test_g4_fails_when_the_causes_do_not_sum_to_the_questions() -> None:
    bad = {
        "method": "x",
        "availability": 0.7,
        "refresh": 1,
        "seed": 0,
        "n": 10,
        "n_hit": 5,
        "n_gold_offline": 2,
        "n_gold_invisible": 0,
        "n_not_selected": 2,
        "n_not_candidate": None,
        "n_not_top_m": None,
        "n_slot_taken": 0,
    }
    assert gate_g4([bad])["passed"] is False


def _flood_runs(drops: list[int], gains: list[int]) -> list[dict[str, Any]]:
    """R = 最大でだけ hit が drops 問減り，(ii) が gains 問増える flood_score の合成の結果．"""
    runs: list[dict[str, Any]] = []
    for seed, (drop, gain) in enumerate(zip(drops, gains, strict=True)):
        for refresh in REFRESH_STEPS:
            last = refresh == max(REFRESH_STEPS)
            runs.append(
                {
                    "method": "flood_score",
                    "availability": JUDGED_AVAILABILITY,
                    "refresh": refresh,
                    "seed": seed,
                    "n": 100,
                    "n_hit": 60 - (drop if last else 0),
                    "n_gold_offline": 30,
                    "n_gold_invisible": gain if last else 0,
                }
            )
    return runs


def test_e1_is_confirmed_only_when_every_seed_reaches_the_share() -> None:
    confirmed = judge_e1(_flood_runs([10] * 5, [8] * 5), True)
    assert confirmed["confirmed"] is True
    assert confirmed["pooled_share"] == pytest.approx(0.8)
    one_low = judge_e1(_flood_runs([10] * 5, [8, 8, 8, 8, 7]), True)
    assert one_low["confirmed"] is False


def test_e1_treats_a_seed_without_a_drop_as_not_confirmed() -> None:
    out = judge_e1(_flood_runs([10, 10, 10, 10, 0], [9, 9, 9, 9, 0]), True)
    assert out["confirmed"] is False
    assert out["per_seed"][4]["share"] is None


def test_hc2_needs_a_smaller_drop_than_multi_centroid_in_every_seed() -> None:
    hc3: dict[str, Any] = {
        "availability": JUDGED_AVAILABILITY,
        "max_refresh": max(REFRESH_STEPS),
        "per_method": {
            HC2_METHOD: {"rel_drop": [0.1] * 5},
            HC2_BASELINE: {"rel_drop": [0.2, 0.2, 0.2, 0.2, 0.1]},
        },
    }
    assert judge_hc2(hc3, True)["supported"] is False
    hc3["per_method"][HC2_BASELINE]["rel_drop"] = [0.2] * 5
    assert judge_hc2(hc3, True)["verdict"] == "支持"
    assert judge_hc2(hc3, False)["verdict"] == "退行ゲートを通らないため判定しない"
