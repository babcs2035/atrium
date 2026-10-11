"""E2 の leave-one-source-out の評価（小さな合成データ．実機・モデルの読み込みは使わない）．"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from atrium import loso
from atrium.arrays import F32Array
from atrium.config import AtriumConfig
from atrium.paths import dataset_paths
from atrium.routing import AllRouter, RoutingQuery, SourceProfile
from atrium.routing.advert import CentroidSimRouter
from atrium.train_router import train_router
from tests.conftest import write_separable_medrag_data


def _profile(name: str, centroid: list[float]) -> SourceProfile:
    return SourceProfile(name, "faiss", name, "enc", np.array(centroid, np.float32), 1, (name,))


def _data(labels: dict[str, list[str]], queries: dict[str, list[float]]) -> loso.LosoData:
    profiles = [_profile("a", [1.0, 0.0]), _profile("b", [0.0, 1.0]), _profile("c", [-1.0, 0.0])]
    return loso.LosoData(
        sources=[p.source for p in profiles],
        profiles=profiles,
        queries={
            q: RoutingQuery(q, "", {"enc": np.array(v, np.float32)}) for q, v in queries.items()
        },
        labels=labels,
        split={"train": list(queries), "val": [], "test": list(queries)},
    )


def test_choose_k_ignores_the_holdout_source_and_its_labels() -> None:
    # c は全ての質問で関連ありだが，c を除いた世界では a だけが関連ありなので k=1 が最良
    data = _data(
        {"q1": ["a", "c"], "q2": ["a", "c"]},
        {"q1": [1.0, 0.1], "q2": [1.0, 0.2]},
    )

    k, f1 = loso.choose_k(CentroidSimRouter, (1, 2, 3), data, "c", ["q1", "q2"])

    assert k == 1
    assert f1[1] == 1.0
    assert f1[2] < 1.0


def test_choose_k_breaks_ties_toward_the_smaller_k() -> None:
    data = _data({"q1": ["a", "b"]}, {"q1": [1.0, 1.0]})

    k, f1 = loso.choose_k(CentroidSimRouter, (2, 3), data, "c", ["q1"])

    assert f1[2] == f1[3] == 1.0
    assert k == 2


def test_newcomer_recall_pools_pairs_across_holdouts() -> None:
    labels = {"q1": ["a"], "q2": ["a", "b"], "q3": ["c"]}
    rows_by_holdout = {
        "a": [
            {"qid": "q1", "selected_sources": ["a"]},
            {"qid": "q2", "selected_sources": ["b"]},
            {"qid": "q3", "selected_sources": ["c"]},
        ],
        "b": [
            {"qid": "q1", "selected_sources": ["a"]},
            {"qid": "q2", "selected_sources": ["a", "b"]},
            {"qid": "q3", "selected_sources": ["c"]},
        ],
    }

    summary = loso.summarize_method(rows_by_holdout, labels, 3, {})

    assert summary["by_holdout"]["a"]["newcomer_recall"] == 0.5
    assert summary["by_holdout"]["b"]["newcomer_recall"] == 1.0
    # 組を合わせる：a は 2 組中 1，b は 1 組中 1 → 3 組中 2（除外ごとの値の平均 0.75 ではない）
    assert summary["pooled"]["newcomer_recall"] == 2 / 3
    assert summary["pooled"]["n_newcomer_pairs"] == 3
    assert "mean_shards_queried" not in summary["pooled"]


def _method(mean_selected: float, recall: float) -> dict[str, Any]:
    return {"pooled": {"mean_selected_sources": mean_selected, "newcomer_recall": recall}}


def test_judge_requires_both_criteria_and_picks_the_best_recall() -> None:
    verdict = loso.judge(
        {
            "ragroute-upper": _method(2.0, 0.9),
            # (a) を満たすが (b) は 0.70 < 0.72 で満たさない
            "desc_sim": _method(2.0, 0.70),
            # 両方を満たす（0.75 ≥ 0.72）
            "centroid_sim": _method(3.0, 0.75),
        }
    )

    assert verdict["methods"]["desc_sim"]["success"] is False
    assert verdict["methods"]["desc_sim"]["b_newcomer_recall_ok"] is False
    assert verdict["methods"]["centroid_sim"]["success"] is True
    assert verdict["chosen"] == "centroid_sim"


def test_judge_rejects_methods_that_query_too_many_sources() -> None:
    verdict = loso.judge({"ragroute-upper": _method(2.0, 0.5), "centroid_sim": _method(3.5, 1.0)})

    assert verdict["methods"]["centroid_sim"]["a_mean_selected_ok"] is False
    assert verdict["chosen"] is None


def test_top_doc_overlap_compares_successful_questions_only() -> None:
    reference = [
        {"qid": "q1", "error": None, "top_doc_ids": ["d1", "d2"]},
        {"qid": "q2", "error": None, "top_doc_ids": ["d3", "d4"]},
        {"qid": "q3", "error": "timeout", "top_doc_ids": None},
    ]
    run = [
        {"qid": "q1", "error": None, "top_doc_ids": ["d1", "d2"]},
        {"qid": "q2", "error": None, "top_doc_ids": ["d4", "d9"]},
        {"qid": "q3", "error": None, "top_doc_ids": ["d5"]},
    ]

    overlap = loso.top_doc_overlap(run, reference)

    assert overlap == {"n": 2, "mean_overlap": 0.75, "exact_match_rate": 0.5}


def _fake_describe(cfg: AtriumConfig, profiles: Sequence[SourceProfile]) -> dict[str, F32Array]:
    # 自己紹介文の埋め込みの代わりに重心を使う（MedCPT を読み込まないため）
    return {p.source: p.centroid for p in profiles}


def test_evaluate_runs_every_method_for_every_holdout(cfg: AtriumConfig, tmp_path: Path) -> None:
    write_separable_medrag_data(cfg, tmp_path)
    paths = dataset_paths(tmp_path, "medrag")
    train_router(cfg, "medrag", paths)

    metrics = loso.evaluate(cfg, "medrag", paths, "val", _fake_describe)

    sources = cfg.data.medrag.sources
    assert metrics["n_questions"] == 40
    assert set(metrics["methods"]) == {
        "desc_sim",
        "centroid_sim",
        "ragroute-frozen",
        "ragroute-upper",
        "all",
        "none",
        "random",
    }
    for m in metrics["methods"].values():
        assert set(m["by_holdout"]) == set(sources)
    assert metrics["methods"]["all"]["pooled"]["newcomer_recall"] == 1.0
    assert metrics["methods"]["none"]["pooled"]["newcomer_recall"] == 0.0
    for holdout in sources:
        assert (paths.root / "loso" / holdout / "router.pt").exists()
        assert metrics["methods"]["centroid_sim"]["by_holdout"][holdout]["k"] in (1, 2, 3)
    assert metrics["criteria"]["upper_newcomer_recall"] > 0.0
    report = loso.render_report(metrics)
    assert "S4 の成功条件" in report
    assert "| ragroute-frozen |" in report


def test_select_all_keeps_the_rows_readable_by_selection_metrics() -> None:
    data = _data({"q1": ["a"]}, {"q1": [1.0, 0.0]})

    rows = loso.select_all(AllRouter(), data, ["q1"], data.profiles)

    assert rows == [{"qid": "q1", "selected_sources": ["a", "b", "c"]}]
