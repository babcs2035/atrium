"""d0009 B3 Iteration 28 の回答の検証（atrium.answer_verify・llm.chat の top_logprobs・V1・V2）の仕様：プロンプトの固定・v の計算・得点の無い候補・同点の扱い．"""

from __future__ import annotations

import json
import math
from pathlib import Path

import httpx
import numpy as np
import pytest

from atrium.answer_select_eval import (
    FEATURE_NAMES,
    QuestionCandidates,
    choose_for,
    load_questions,
    select_verification_rule,
)
from atrium.answer_verify import (
    NO_YES_NO_ERROR,
    OVERFLOW_ERROR,
    VERIFY_PROMPT,
    build_jobs,
    build_verify_messages,
    is_final_row,
    prompt_sha256,
    verification_score,
    verify_job,
    verify_llm_config,
)
from atrium.config import AtriumConfig
from atrium.llm import chat
from atrium.store import RetrievedDoc

# 事前登録（journal の Iteration 28）の検証のプロンプトの sha256．文面を変えたらこの値が変わる
PROMPT_SHA256 = "26ec41b634b6a15b2d0abbeff9e4e67c03840f305bae8f71a76577bfda182688"


def question(
    qid: str, sources: list[str], v: list[float], ce_max: list[float], bank: str = "a"
) -> QuestionCandidates:
    """v と ce_max だけを持つ 1 問（v の NaN は得点なし）．"""
    raw = np.full((len(sources), len(FEATURE_NAMES)), np.nan)
    raw[:, FEATURE_NAMES.index("v")] = v
    raw[:, FEATURE_NAMES.index("ce_max")] = ce_max
    return QuestionCandidates(qid, bank, tuple(sources), raw, (False,) * len(sources), 0)


def test_verify_prompt_text_is_fixed() -> None:
    assert prompt_sha256() == PROMPT_SHA256


def test_verify_messages_are_one_user_turn_without_system() -> None:
    messages = build_verify_messages("Document [0] (Title: t) body", "Who?", "Bob")

    assert [m["role"] for m in messages] == ["user"]
    assert messages[0]["content"] == VERIFY_PROMPT.format(
        context="Document [0] (Title: t) body", question="Who?", answer="Bob"
    )
    assert messages[0]["content"].endswith("Answer with Yes or No only.")


# ── v の計算 ──────────────────────────────────────────────────────────────────


def test_v_is_logsumexp_of_yes_minus_logsumexp_of_no_ignoring_case_and_spaces() -> None:
    top = [("Yes", -0.5), (" yes", -2.0), ("NO", -1.0), ("maybe", -3.0)]

    score = verification_score(top)

    expected_yes = math.log(math.exp(-0.5) + math.exp(-2.0))
    assert score.l_yes == pytest.approx(expected_yes)
    assert score.l_no == pytest.approx(-1.0)
    assert score.v == pytest.approx(expected_yes + 1.0)
    assert score.first_token == "Yes"
    assert score.first_token_other is False


def test_missing_no_token_is_replaced_by_the_smallest_logprob_in_the_top_list() -> None:
    score = verification_score([("Yes", -0.01), ("The", -9.0), ("Sure", -20.0)])

    assert score.l_no == -20.0
    assert score.v == pytest.approx(-0.01 + 20.0)


def test_missing_yes_token_is_replaced_by_the_smallest_logprob_in_the_top_list() -> None:
    score = verification_score([("No", -2e-6), ("The", -15.0)])

    assert score.l_yes == -15.0
    assert score.v == pytest.approx(-15.0 + 2e-6)


def test_neither_yes_nor_no_in_the_top_list_has_no_score_and_flags_the_first_token() -> None:
    score = verification_score([("The", -0.1), ("I", -3.0)])

    assert score.v is None
    assert score.first_token == "The"
    assert score.first_token_other is True


def test_empty_top_list_has_no_score() -> None:
    assert verification_score([]).v is None


def test_first_token_other_is_false_when_the_most_probable_token_is_yes_or_no() -> None:
    assert verification_score([("Maybe", -3.0), ("no", -0.2)]).first_token_other is False


# ── llm.chat の top_logprobs ──────────────────────────────────────────────────


def logprob_reply(
    top: list[tuple[str, float]], seen: list[dict[str, object]]
) -> httpx.MockTransport:
    """最初のトークンの top_logprobs を返す Ollama 0.35 の応答の形のトランスポート．"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        first = {
            "token": top[0][0],
            "logprob": top[0][1],
            "bytes": [],
            "top_logprobs": [{"token": t, "logprob": lp, "bytes": []} for t, lp in top],
        }
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": top[0][0]},
                "logprobs": [first],
                "prompt_eval_count": 1234,
                "total_duration": 2_000_000_000,
            },
        )

    return httpx.MockTransport(handler)


async def test_chat_requests_top_logprobs_and_seed_only_when_asked(cfg: AtriumConfig) -> None:
    seen: list[dict[str, object]] = []
    client = httpx.AsyncClient(transport=logprob_reply([("Yes", -0.1), ("No", -2.5)], seen))

    plain = await chat(client, "http://o", "m", [], cfg.llm)
    scored = await chat(client, "http://o", "m", [], cfg.llm, top_logprobs=20, seed=12)

    assert "logprobs" not in seen[0] and "top_logprobs" not in seen[0]
    assert "seed" not in seen[0]["options"]  # type: ignore[operator]
    assert seen[1]["logprobs"] is True and seen[1]["top_logprobs"] == 20
    assert seen[1]["options"]["seed"] == 12  # type: ignore[index]
    assert plain.first_top_logprobs == ()
    assert scored.first_top_logprobs == (("Yes", -0.1), ("No", -2.5))


async def test_verify_job_scores_one_candidate_and_records_cost(cfg: AtriumConfig) -> None:
    seen: list[dict[str, object]] = []
    client = httpx.AsyncClient(transport=logprob_reply([("Yes", -0.01), ("No", -4.6)], seen))
    docs = {"a/1.": RetrievedDoc("a/1.", "Title", "Body text", 0.0)}
    row = {
        "qid": "q1",
        "owner_answer": {
            "candidates": [
                {"source": "a", "answer": "Bob", "context_doc_ids": ["a/1."]},
                {"source": "b", "answer": " ", "context_doc_ids": ["a/1."]},
            ]
        },
    }
    jobs = build_jobs([row], {"q1": "Who?"}, docs)

    result = await verify_job(client, "http://o", "gemma", verify_llm_config(cfg.llm), jobs[0])

    assert [(j.index, j.source, j.answer_empty) for j in jobs] == [(0, "a", False), (1, "b", True)]
    assert "Document [0] (Title: Title) Body text" in jobs[0].messages[0]["content"]
    assert result["v"] == pytest.approx(-0.01 + 4.6)
    assert (result["prompt_eval_count"], result["total_duration_s"], result["error"]) == (
        1234,
        2.0,
        None,
    )
    assert seen[0]["options"]["num_predict"] == 1  # type: ignore[index]
    assert seen[0]["think"] is False


async def test_verify_job_records_context_overflow_without_retrying(cfg: AtriumConfig) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="input exceeds the available context size")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    job = build_jobs(
        [{"qid": "q", "owner_answer": {"candidates": [{"source": "a", "answer": "x"}]}}],
        {"q": "?"},
        {},
    )[0]

    result = await verify_job(client, "http://o", "m", verify_llm_config(cfg.llm), job)

    assert (result["v"], result["error"], len(calls)) == (None, OVERFLOW_ERROR, 1)
    assert is_final_row(result)


def test_resume_retries_transient_failures_but_not_final_ones() -> None:
    assert is_final_row({"v": 1.0, "error": None})
    assert is_final_row({"v": None, "error": NO_YES_NO_ERROR})
    assert not is_final_row({"v": None, "error": "http_500"})


# ── V1 の選択 ─────────────────────────────────────────────────────────────────


def test_v1_picks_the_largest_v() -> None:
    q = question("q", ["a", "b", "c"], v=[1.0, 5.0, 3.0], ce_max=[0.9, 0.1, 0.5])

    assert choose_for("V1", [q], None) == [1]


def test_v1_tie_within_1e6_goes_to_the_larger_ce_max() -> None:
    q = question("q", ["a", "b", "c"], v=[4.0, 4.0 + 5e-7, 1.0], ce_max=[0.9, 0.2, 0.99])

    assert choose_for("V1", [q], None) == [0]


def test_v1_tie_in_both_v_and_ce_max_goes_to_the_earlier_candidate() -> None:
    q = question("q", ["a", "b"], v=[2.0, 2.0], ce_max=[0.5, 0.5])

    assert choose_for("V1", [q], None) == [0]


def test_v1_treats_a_missing_v_as_minus_infinity() -> None:
    q = question("q", ["a", "b"], v=[np.nan, -30.0], ce_max=[0.99, 0.01])

    assert choose_for("V1", [q], None) == [1]


def test_v1_with_no_scores_at_all_falls_back_to_ce_max() -> None:
    q = question("q", ["a", "b"], v=[np.nan, np.nan], ce_max=[0.2, 0.7])

    assert choose_for("V1", [q], None) == [1]


def test_v1_gives_none_for_a_question_without_candidates() -> None:
    q = question("q", [], v=[], ce_max=[])

    assert choose_for("V1", [q], None) == [None]


# ── G-D の選び方 ──────────────────────────────────────────────────────────────


def test_v2_is_not_eligible_unless_it_beats_s3() -> None:
    chosen = select_verification_rule({"R3p": 0.72, "S3": 0.73, "V1": 0.70, "V2": 0.73})

    assert chosen["chosen"] == "V1" and chosen["eligible"] == ["V1"]


def test_v1_is_preferred_when_within_0_002_of_the_best() -> None:
    chosen = select_verification_rule({"R3p": 0.72, "S3": 0.726, "V1": 0.7300, "V2": 0.7315})

    assert chosen["chosen"] == "V1"


def test_v2_is_chosen_when_it_beats_v1_by_more_than_the_tolerance_and_s3() -> None:
    chosen = select_verification_rule({"R3p": 0.72, "S3": 0.726, "V1": 0.728, "V2": 0.740})

    assert chosen["chosen"] == "V2"


# ── 読み込み ──────────────────────────────────────────────────────────────────


def test_load_questions_reads_v_and_keeps_it_missing_without_a_score(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    row = {
        "qid": "q1",
        "bank": "a",
        "error": None,
        "owner_answer": {
            "candidates": [{"source": "a", "answer": "x"}, {"source": "b", "answer": "y"}]
        },
    }
    (run / "results.jsonl").write_text(json.dumps(row) + "\n")
    (run / "ce_features.jsonl").write_text(
        "".join(
            json.dumps({"qid": "q1", "index": i, "source": s, "ce_max": 0.5, "ce_top1": 0.5}) + "\n"
            for i, s in enumerate("ab")
        )
    )
    verify = tmp_path / "verify_features.jsonl"
    verify.write_text(
        json.dumps({"qid": "q1", "index": 0, "v": 3.5, "error": None})
        + "\n"
        + json.dumps({"qid": "q1", "index": 1, "v": None, "error": NO_YES_NO_ERROR})
        + "\n"
    )

    (q,) = load_questions(
        run, need_labels=False, need_answer_features=False, verify_features=verify
    )

    v = q.raw[:, FEATURE_NAMES.index("v")]
    assert v[0] == 3.5 and np.isnan(v[1])


def test_apply_v1_works_on_a_run_without_ce_answer_features(tmp_path: Path) -> None:
    """H・H2 の実行には ce_answer_features.jsonl が無い（V1・V2 は回答を入れた cross-encoder の得点を使わない）．"""
    from atrium.answer_select_eval import apply_to_runs
    from tests.test_answer_select_eval import make_run

    run = tmp_path / "run"
    make_run(run)
    (run / "ce_answer_features.jsonl").unlink()
    verify = tmp_path / "verify_features.jsonl"
    # q0 は ce_max の最高が 2 番目（正解），v の最高は先頭．q1 は v が全て同じなので ce_max の同点 → 先頭
    scores = {
        ("q0", 0): 5.0,
        ("q0", 1): 1.0,
        ("q0", 2): 0.0,
        ("q1", 0): 2.0,
        ("q1", 1): 2.0,
        ("q1", 2): 0.0,
    }
    verify.write_text(
        "".join(json.dumps({"qid": q, "index": i, "v": v}) + "\n" for (q, i), v in scores.items())
    )
    selector = tmp_path / "answer_verifier_selector.json"
    selector.write_text(json.dumps({"chosen": "V1"}), encoding="utf-8")

    result = apply_to_runs(selector, [run], [], tmp_path / "out", f"{tmp_path}/", [verify])

    derived = [
        json.loads(line)
        for line in (tmp_path / "run_V1" / "results.jsonl").read_text().splitlines()
    ]
    assert [d["chosen_index"] for d in derived] == [0, 0]
    assert result["Q"]["acc_new"] == 0.5
