"""d0009 B3 Iteration 41 の受信箱の中の文脈の選び方（atrium.inbox_context_eval）の仕様：文脈の作り方・X の選び方・G-D・作り直す候補・腕の実行のディレクトリ．"""

from __future__ import annotations

import json
from typing import Any

import httpx
import numpy as np
import pytest

from atrium.answer_verify import VerifyJob, run_jobs
from atrium.hybrid import Bm25Index
from atrium.inbox_context_eval import (
    GATE_SAMPLE,
    GenItem,
    InboxView,
    Retrieval,
    _post_generation,
    arm_rows,
    bm25_context,
    ce_context,
    context_for_rule,
    g_gen,
    gate_d,
    gate_d_verdict,
    generation_items,
    retrieve,
    rrf_context,
    seed_verify_rows,
    select_context_rule,
    summarize_plan,
)
from atrium.protocol import DocOut


def retrieval(dense: list[float], bm25: list[float], rrf_order: list[int]) -> Retrieval:
    """内積・BM25 の得点と RRF の順を直接与えた検索の結果．"""
    return Retrieval(np.asarray(dense), np.asarray(bm25), rrf_order)


# ── 文脈の作り方 ──────────────────────────────────────────────────────────


def test_rrf_context_orders_the_rrf_top_k_by_dense_score_descending() -> None:
    r = retrieval([0.1, 0.9, 0.5, 0.7], [0.0] * 4, rrf_order=[0, 2, 3, 1])

    assert rrf_context(r, 3) == [3, 2, 0]


def test_bm25_context_takes_the_top_k_by_bm25_with_ties_to_the_smaller_row() -> None:
    r = retrieval([0.0] * 4, [2.0, 5.0, 5.0, 1.0], rrf_order=[0, 1, 2, 3])

    assert bm25_context(r, 3) == [1, 2, 0]


def test_ce_context_reorders_only_the_top_depth_rrf_documents_by_score() -> None:
    r = retrieval([0.0] * 5, [0.0] * 5, rrf_order=[4, 3, 2, 1, 0])
    scores = [
        0.1,
        0.9,
        0.3,
        0.99,
        0.2,
    ]  # RRF の順の得点．depth = 3 なら先頭 3 通（行 4・3・2）だけが対象

    assert ce_context(r, scores, depth=3, k=2) == [3, 2]
    assert ce_context(r, scores, depth=5, k=2) == [1, 3]


def test_ce_context_rejects_fewer_scores_than_documents() -> None:
    r = retrieval([0.0] * 3, [0.0] * 3, rrf_order=[0, 1, 2])

    with pytest.raises(ValueError, match="scores"):
        ce_context(r, [0.5], depth=3, k=2)


def test_context_for_rule_requires_cross_encoder_scores_for_ce_only() -> None:
    r = retrieval([0.0] * 3, [3.0, 2.0, 1.0], rrf_order=[0, 1, 2])

    assert context_for_rule("bm25", r, None, 2) == [0, 1]
    with pytest.raises(ValueError, match="needs cross-encoder scores"):
        context_for_rule("ce10", r, None, 2)


def test_retrieve_uses_the_inbox_only_bm25_and_rrf_depth() -> None:
    texts = ["alpha beta", "gamma delta", "alpha alpha"]
    view = InboxView(
        paths=["i/0", "i/1", "i/2"],
        titles=["t0", "t1", "t2"],
        contents=texts,
        emb=np.eye(3, dtype=np.float32),
        bm25=Bm25Index(t.split() for t in texts),
        row_of={"i/0": 0, "i/1": 1, "i/2": 2},
    )

    r = retrieve(view, np.asarray([0.0, 1.0, 0.0], dtype=np.float32), "alpha", fusion_depth=2)

    assert r.dense.tolist() == [0.0, 1.0, 0.0]
    assert r.bm25[1] == 0.0 and r.bm25[0] > 0 and r.bm25[2] > 0
    assert set(r.rrf_order) == {0, 1, 2}
    assert view.ce_text(0) == "t0\nalpha beta"


# ── X の選び方・G-D ───────────────────────────────────────────────────────


def recall(bm25: float, ce10: float, ce20: float, ce50: float) -> dict[str, float]:
    """X の候補の Recall."""
    return {"bm25": bm25, "ce10": ce10, "ce20": ce20, "ce50": ce50}


def test_select_picks_the_largest_recall() -> None:
    assert select_context_rule(recall(0.90, 0.95, 0.94, 0.93))["chosen"] == "ce10"
    assert select_context_rule(recall(0.90, 0.92, 0.94, 0.97))["chosen"] == "ce50"


def test_select_prefers_the_cheaper_rule_within_the_tolerance() -> None:
    chosen = select_context_rule(recall(0.9500, 0.9490, 0.9510, 0.9520))

    assert chosen["chosen"] == "bm25"
    assert chosen["within_tolerance"] == ["bm25", "ce10", "ce20", "ce50"]


def test_select_tolerance_boundary_is_inclusive_at_0_003_and_exclusive_above() -> None:
    assert select_context_rule(recall(0.950, 0.940, 0.940, 0.953))["chosen"] == "bm25"
    assert select_context_rule(recall(0.950, 0.940, 0.940, 0.9531))["chosen"] == "ce50"


def flags_with_gain(n: int, gains: int) -> tuple[np.ndarray, np.ndarray]:
    """RRF が外し X が当てた問が gains 件，他は両方当てた問（Recall の差 = gains / n）．"""
    rrf = np.ones(n)
    x = np.ones(n)
    rrf[:gains] = 0.0
    return x, rrf


def test_gate_d_passes_when_the_gain_reaches_0_010_and_the_interval_excludes_zero() -> None:
    x, rrf = flags_with_gain(3000, 60)

    result = gate_d(x, rrf)

    assert result["passed"] is True
    assert result["delta"] == pytest.approx(0.02)
    assert result["bootstrap_ci95"][0] > 0


def test_gate_d_fails_below_the_minimum_gain_even_with_a_positive_interval() -> None:
    x, rrf = flags_with_gain(3000, 27)  # +0.009

    result = gate_d(x, rrf)

    assert result["delta"] == pytest.approx(0.009)
    assert result["bootstrap_ci95"][0] > 0
    assert result["passed"] is False


def test_gate_d_fails_when_the_interval_includes_zero() -> None:
    x, rrf = flags_with_gain(100, 2)  # +0.02 でも 100 問では区間が 0 をまたぐ

    result = gate_d(x, rrf)

    assert result["delta"] >= 0.010
    assert result["bootstrap_ci95"][0] <= 0
    assert result["passed"] is False


def test_gate_d_boundary_gain_of_exactly_0_010_with_positive_interval_passes() -> None:
    x, rrf = flags_with_gain(3000, 30)  # +0.010 ちょうど

    result = gate_d(x, rrf)

    assert result["delta"] == pytest.approx(0.010)
    assert result["passed"] is (result["bootstrap_ci95"][0] > 0)
    assert result["passed"] is True


def test_gate_d_verdict_is_written_only_when_the_gate_is_not_passed() -> None:
    passed = {"G_D": {"passed": True}, "recall": {}}
    failed = {
        "G_D": {
            "passed": False,
            "chosen": "bm25",
            "delta": 0.004,
            "bootstrap_ci95": [-0.1, 0.1],
            "min_gain": 0.01,
        },
        "recall": {"rrf": 0.9},
    }

    assert gate_d_verdict(passed) is None
    verdict = gate_d_verdict(failed)
    assert verdict is not None
    assert verdict["verdict"] == "rejected_gate_not_passed"
    assert verdict["live_run"] is False


# ── 作り直す候補・腕の実行のディレクトリ ─────────────────────────────────────


def saved_row(qid: str, answers: list[str]) -> dict[str, Any]:
    """3 候補（受信箱 a・b・c．文脈は <受信箱>/0〜4）の保存した行．"""
    return {
        "qid": qid,
        "bank": "a",
        "error": None,
        "final_answer": "saved final",
        "node_answers": [
            {"source": s, "prompt_tokens": 100 + i} for i, s in enumerate(["a", "b", "c"])
        ],
        "owner_answer": {
            "candidates": [
                {
                    "source": s,
                    "answer": answers[i],
                    "unknown": False,
                    "top_score": 0.5,
                    "probe_fused": 1.0,
                    "bm25_max": 2.0,
                    "rrf_max": 0.03,
                    "avg_logprob": -0.1,
                    "n_logprob_tokens": 5,
                    "context_doc_ids": [f"{s}/{j}" for j in range(5)],
                }
                for i, s in enumerate(["a", "b", "c"])
            ]
        },
    }


def plan_entry(qid: str, index: int, changed: bool) -> dict[str, Any]:
    """作り直す（new の文脈が先頭 2 通を入れ替えた）候補か，同じ文脈の候補．"""
    source = "abc"[index]
    base = [f"{source}/{j}" for j in range(5)]
    new = [base[1], base[0], *base[2:]] if changed else base
    return {
        "qid": qid,
        "index": index,
        "source": source,
        "base_ids": base,
        "new_ids": new,
        "changed": changed,
        "set_changed": False,
        "new_top_score": 0.9,
    }


def record(arm: str, qid: str, index: int, answer: str, error: str | None = None) -> dict[str, Any]:
    """生成の記録 1 件．"""
    return {
        "key": f"{arm}|{qid}|{index}",
        "arm": arm,
        "qid": qid,
        "index": index,
        "error": error,
        "answer": answer,
        "prompt_tokens": 100 + index,
        "avg_logprob": -0.5,
        "n_logprob_tokens": 7,
    }


def test_arm_rows_overwrite_only_rebuilt_candidates_and_the_new_arm_also_changes_the_context() -> (
    None
):
    saved = [saved_row("q1", ["A0", "A1", "A2"])]
    plan = [plan_entry("q1", 0, False), plan_entry("q1", 1, True), plan_entry("q1", 2, False)]
    records = {
        "base|q1|1": record("base", "q1", 1, "Answer: base B"),
        "new|q1|1": record("new", "q1", 1, "Answer: UNKNOWN"),
    }

    base = arm_rows(saved, plan, records, "base")[0]
    new = arm_rows(saved, plan, records, "new")[0]

    assert base["b3ic_rebuilt"] == new["b3ic_rebuilt"] == [1]
    b1, n1 = base["owner_answer"]["candidates"][1], new["owner_answer"]["candidates"][1]
    assert (b1["answer"], b1["unknown"]) == ("base B", False)
    assert b1["context_doc_ids"] == ["b/0", "b/1", "b/2", "b/3", "b/4"]
    assert (n1["answer"], n1["unknown"]) == ("UNKNOWN", True)
    assert n1["context_doc_ids"] == ["b/1", "b/0", "b/2", "b/3", "b/4"]
    assert n1["top_score"] == 0.9 and b1["top_score"] == 0.5
    assert (n1["avg_logprob"], n1["n_logprob_tokens"]) == (-0.5, 7)
    # 作り直さない候補は両腕とも保存したまま．元の行は変えない
    assert base["owner_answer"]["candidates"][0] == saved[0]["owner_answer"]["candidates"][0]
    assert new["owner_answer"]["candidates"][2]["answer"] == "A2"
    assert saved[0]["owner_answer"]["candidates"][1]["answer"] == "A1"


def test_arm_rows_reject_a_failed_generation() -> None:
    saved = [saved_row("q1", ["A0", "A1", "A2"])]
    plan = [plan_entry("q1", 1, True)]
    records = {"base|q1|1": record("base", "q1", 1, "", error="HTTPError: boom")}

    with pytest.raises(ValueError, match="no successful generation"):
        arm_rows(saved, plan, records, "base")


def test_seed_verify_rows_keep_only_candidates_whose_context_and_answer_are_unchanged() -> None:
    saved = [saved_row("q1", ["A0", "A1", "A2"])]
    plan = [plan_entry("q1", 0, True), plan_entry("q1", 1, True), plan_entry("q1", 2, False)]
    records = {
        # 候補 0：base は同じ回答（文脈も同じ）・new は文脈が変わる．候補 1：base は回答が変わる
        "base|q1|0": record("base", "q1", 0, "Answer: A0"),
        "new|q1|0": record("new", "q1", 0, "Answer: A0"),
        "base|q1|1": record("base", "q1", 1, "Answer: changed"),
        "new|q1|1": record("new", "q1", 1, "Answer: A1"),
    }
    verify = {("q1", i): {"qid": "q1", "index": i, "v": float(i)} for i in range(3)}

    base_seed = seed_verify_rows(arm_rows(saved, plan, records, "base"), saved, verify)
    new_seed = seed_verify_rows(arm_rows(saved, plan, records, "new"), saved, verify)

    assert [r["index"] for r in base_seed] == [0, 2]
    assert [r["index"] for r in new_seed] == [2]


def test_summarize_plan_counts_candidates_and_questions() -> None:
    plan = [plan_entry("q1", 0, True), plan_entry("q1", 1, False), plan_entry("q2", 0, False)]

    summary = summarize_plan(plan)

    assert summary["n_candidates"] == 3 and summary["n_changed"] == 1
    assert summary["n_questions"] == 2 and summary["n_questions_with_changed"] == 1


def test_generation_items_order_base_then_new_per_changed_candidate_with_the_context_order() -> (
    None
):
    texts = [f"body {j}" for j in range(5)]
    views = {
        s: InboxView(
            paths=[f"{s}/{j}" for j in range(5)],
            titles=[f"title {j}" for j in range(5)],
            contents=texts,
            emb=np.zeros((5, 1), dtype=np.float32),
            bm25=Bm25Index(t.split() for t in texts),
            row_of={f"{s}/{j}": j for j in range(5)},
        )
        for s in "abc"
    }
    plan = [plan_entry("q1", 0, False), plan_entry("q1", 1, True)]

    items = generation_items(plan, views, {"q1": "Who?"})

    assert [i.key for i in items] == ["base|q1|1", "new|q1|1"]
    assert [d.doc_id for d in items[0].docs] == ["b/0", "b/1", "b/2", "b/3", "b/4"]
    assert [d.doc_id for d in items[1].docs][:2] == ["b/1", "b/0"]
    assert items[1].docs[0].title == "title 1"


def test_post_generation_records_logprobs_and_node_and_keeps_failures_as_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["logprobs"] is True and body["allow_unknown"] is False
        if body["question"] == "boom":
            return httpx.Response(500)
        return httpx.Response(
            200,
            json={
                "node_id": "n",
                "answer": "Answer: x",
                "llm": {
                    "prompt_tokens": 12,
                    "output_tokens": 3,
                    "prefill_s": 0.1,
                    "decode_s": 0.2,
                    "total_s": 0.3,
                },
                "avg_logprob": -0.25,
                "n_logprob_tokens": 3,
            },
        )

    docs = [DocOut(doc_id="a/0", title="t", content="c", score=0.0)]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ok = _post_generation(client, "http://node", GenItem("base", "q1", 2, "Who?", docs))
        bad = _post_generation(client, "http://node", GenItem("new", "q1", 2, "boom", docs))

    assert ok["error"] is None and ok["key"] == "base|q1|2" and ok["node"] == "http://node"
    assert (ok["prompt_tokens"], ok["avg_logprob"], ok["n_logprob_tokens"]) == (12, -0.25, 3)
    assert bad["error"] is not None and "answer" not in bad


def test_g_gen_passes_with_matching_prompt_tokens_and_fails_on_a_mismatch_or_missing() -> None:
    n = GATE_SAMPLE
    rows = [saved_row(f"q{i}", ["Answer0", "A1", "A2"]) for i in range(n)]
    plan = [plan_entry(f"q{i}", 0, True) for i in range(n)]
    records = {
        f"base|q{i}|0": {**record("base", f"q{i}", 0, "Answer: Answer0"), "prompt_tokens": 100}
        for i in range(n)
    }

    ok = g_gen(plan, records, rows)
    records["base|q3|0"]["prompt_tokens"] = 101
    mismatch = g_gen(plan, records, rows)
    del records["base|q4|0"]
    missing = g_gen(plan, records, rows)

    assert ok["passed"] is True and ok["n_answer_identical"] == n
    assert mismatch["passed"] is False and mismatch["n_prompt_tokens_match"] == n - 1
    assert missing["passed"] is False and missing["n_missing"] == 1


# ── verify-features の複数の送り先 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_verify_run_jobs_round_robins_candidates_over_the_urls(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, cfg: Any
) -> None:
    from atrium import answer_verify
    from atrium.answer_verify import verify_llm_config

    seen: list[tuple[str, int]] = []

    async def fake_verify_job(
        client: Any, url: str, model: str, llm_cfg: Any, job: VerifyJob
    ) -> dict[str, Any]:
        seen.append((url, job.index))
        return {"qid": job.qid, "index": job.index, "v": 0.0, "error": None}

    monkeypatch.setattr(answer_verify, "verify_job", fake_verify_job)
    jobs = [VerifyJob("q", i, "s", [], False) for i in range(5)]

    failed = await run_jobs(
        jobs, ["http://u0", "http://u1"], "m", verify_llm_config(cfg.llm), tmp_path / "out.jsonl"
    )

    assert failed == 0
    assert sorted(seen) == [
        ("http://u0", 0),
        ("http://u0", 2),
        ("http://u0", 4),
        ("http://u1", 1),
        ("http://u1", 3),
    ]
