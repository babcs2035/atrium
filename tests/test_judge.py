"""EnronQA の回答の採点（atrium.judge）と，採点の結果の取り込みの仕様．"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from atrium.analysis import accuracy_metrics, merge_judgements
from atrium.config import AtriumConfig
from atrium.judge import JUDGE_PROMPT, JudgeItem, cohen_kappa, judge_items, lcs_ratio, parse_verdict
from tests.conftest import HostDispatchTransport, ollama_mock


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Reasoning: same fact.\nAnswer: Correct", True),
        ("Reasoning: ...\nAnswer: Yes, the answer matches.", True),
        ("Reasoning: ...\nAnswer: Incorrect", False),
        ("Reasoning: ...\nAnswer: The answer is not correct.", False),
        ("Reasoning: ...\nAnswer: False", False),
        ("Reasoning: nothing to conclude", None),
    ],
)
def test_verdict_reads_the_last_answer_line(output: str, expected: bool | None) -> None:
    assert parse_verdict(output) is expected


def test_judge_prompt_follows_the_paper_format() -> None:
    text = JUDGE_PROMPT.format(email="E", question="Q", gold="G", answer="A")
    assert "Correct Answer: G\nStudent Answer: A\nReasoning: Let's think step by step" in text
    assert "${produce the correct}" in text


def test_lcs_ratio_is_the_share_of_answer_words_in_order_in_the_email() -> None:
    assert lcs_ratio("Ameren sent a notice", "Hi. Ameren then sent us a termination notice.") == 1.0
    assert lcs_ratio("notice Ameren", "Ameren notice") == 0.5
    assert lcs_ratio("", "anything") == 0.0


def test_cohen_kappa_is_one_for_identical_and_zero_for_chance_level_agreement() -> None:
    assert cohen_kappa([True, False, True], [True, False, True]) == 1.0
    assert cohen_kappa([True, True, False, False], [True, False, True, False]) == 0.0


def test_merged_judgements_set_correctness_and_count_unparsed(tmp_path: Path) -> None:
    path = tmp_path / "judgements.jsonl"
    lines = [
        {"qid": "q1", "correct": True, "answer_overlap_lcs": 0.5},
        {"qid": "q2", "correct": None, "answer_overlap_lcs": 0.0},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    rows = [{"qid": "q1", "bank": "a", "choice": None}, {"qid": "q2", "bank": "a", "choice": None}]
    merged = merge_judgements(rows, path)
    acc = accuracy_metrics(merged)
    assert acc is not None
    assert acc["overall"]["accuracy"] == 0.5
    assert acc["unparsed_choice_rate"] == 0.5


async def test_judge_items_retries_a_failed_request_on_another_ollama(cfg: AtriumConfig) -> None:
    # 1 台が 500 を返しても，その要求は別の Ollama へ振り替えられ，全件が採点される（2026-10-10 の κ の中断）
    broken = httpx.MockTransport(lambda _: httpx.Response(500, text="llama runner crashed"))
    healthy = ollama_mock(lambda _: "Reasoning: same.\nAnswer: Correct")
    transport = HostDispatchTransport({"broken": broken, "healthy": healthy})
    items = [JudgeItem(f"k{i}", "E", "Q", "G", "A") for i in range(4)]
    out = await judge_items(
        items, ["http://broken:11434", "http://healthy:11434"], "m", cfg, transport=transport
    )
    assert {k: v[0] for k, v in out.items()} == {f"k{i}": True for i in range(4)}


async def test_judge_items_raises_when_every_ollama_fails(cfg: AtriumConfig) -> None:
    broken = httpx.MockTransport(lambda _: httpx.Response(500, text="down"))
    with pytest.raises(httpx.HTTPStatusError):
        await judge_items(
            [JudgeItem("k", "E", "Q", "G", "A")],
            ["http://broken:11434"],
            "m",
            cfg,
            transport=broken,
        )


async def test_judge_items_marks_repeat_limit_aborts_as_unparsed_without_retrying(
    cfg: AtriumConfig,
) -> None:
    # 温度 0 の繰り返しの打ち切りはどの Ollama でも同じなので，送り直さずに判定できなかったとする
    calls: list[str] = []

    def aborted(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(500, json={"error": "prediction aborted, token repeat limit reached"})

    transport = HostDispatchTransport(
        {"a": httpx.MockTransport(aborted), "b": httpx.MockTransport(aborted)}
    )
    out = await judge_items(
        [JudgeItem("k", "E", "Q", "G", "A")],
        ["http://a:11434", "http://b:11434"],
        "m",
        cfg,
        transport=transport,
    )
    assert out == {"k": (None, "")}
    assert calls == ["a"]


# ── 候補ごとの採点（p0007 A5．atrium judge --with-candidates） ─────────────────────────────


def _enron_data(root: Path) -> None:
    """受信箱 a（メール 2 通）・質問 2 問（test）の最小のデータ（gold・質問のファイル・コーパス）．"""
    from atrium.benchmarks import Question, write_questions
    from atrium.paths import dataset_paths

    paths = dataset_paths(root, "enronqa")
    chunk = paths.corpus("a") / "chunk"
    chunk.mkdir(parents=True)
    (chunk / "a.jsonl").write_text(
        "".join(
            json.dumps({"id": f"a/{i}", "title": "t", "content": f"mail {i}"}) + "\n"
            for i in range(2)
        )
    )
    bench = paths.questions.parent
    write_questions(paths.questions, [Question(f"q{i}", "a", f"Q{i}?", {}, None) for i in range(2)])
    for name in ("dev_questions.jsonl", "train_questions.jsonl"):
        write_questions(bench / name, [])
    (bench / "gold.jsonl").write_text(
        "".join(
            json.dumps({"qid": f"q{i}", "path": f"a/{i}", "gold_answer": f"G{i}"}) + "\n"
            for i in range(2)
        )
    )


def test_judge_run_with_candidates_scores_each_distinct_answer_once(
    cfg: AtriumConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atrium import judge

    _enron_data(tmp_path / "data")
    enron = cfg.data.require_enronqa().model_copy(update={"sources": ["a"]})
    cfg = cfg.model_copy(update={"data": cfg.data.model_copy(update={"enronqa": enron})})
    run = tmp_path / "run"
    run.mkdir()
    rows = [
        {
            "qid": "q0",
            "error": None,
            "final_answer": "Bob",
            "owner_answer": {
                "candidates": [
                    {"source": "a", "answer": "Bob"},
                    {"source": "b", "answer": "UNKNOWN"},
                    {"source": "c", "answer": "UNKNOWN"},
                ]
            },
        },
        {"qid": "q1", "error": None, "final_answer": "Ann", "owner_answer": {"candidates": []}},
    ]
    (run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    asked: list[JudgeItem] = []

    async def fake_judge(
        items: list[JudgeItem], *args: object, **kwargs: object
    ) -> dict[str, tuple[bool | None, str]]:
        asked.extend(items)
        return {i.key: (i.answer in ("Bob", "Ann"), "raw") for i in items}

    monkeypatch.setattr(judge, "judge_items", fake_judge)
    from atrium.paths import dataset_paths

    summary = judge.judge_run(
        cfg, dataset_paths(tmp_path / "data", "enronqa"), run, ["u"], "m", True
    )

    # 採った回答 "Bob" と同じ候補，2 つの UNKNOWN はそれぞれ 1 回だけ採点する
    assert sorted((i.key, i.answer) for i in asked) == [
        ("q0", "Bob"),
        ("q0#1", "UNKNOWN"),
        ("q1", "Ann"),
    ]
    cands = [
        json.loads(line) for line in (run / "candidate_judgements.jsonl").read_text().splitlines()
    ]
    assert [(c["qid"], c["index"], c["source"], c["correct"]) for c in cands] == [
        ("q0", 0, "a", True),
        ("q0", 1, "b", False),
        ("q0", 2, "c", False),
    ]
    finals = [json.loads(line) for line in (run / "judgements.jsonl").read_text().splitlines()]
    assert [(j["qid"], j["correct"]) for j in finals] == [("q0", True), ("q1", True)]
    assert (summary["n"], summary["n_candidates"], summary["n_judge_calls"]) == (2, 3, 3)


def test_judge_run_without_candidates_writes_only_the_main_judgements(
    cfg: AtriumConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atrium import judge
    from atrium.paths import dataset_paths

    _enron_data(tmp_path / "data")
    enron = cfg.data.require_enronqa().model_copy(update={"sources": ["a"]})
    cfg = cfg.model_copy(update={"data": cfg.data.model_copy(update={"enronqa": enron})})
    run = tmp_path / "run"
    run.mkdir()
    row = {
        "qid": "q0",
        "error": None,
        "final_answer": "Bob",
        "owner_answer": {"candidates": [{"source": "b", "answer": "Zed"}]},
    }
    (run / "results.jsonl").write_text(json.dumps(row) + "\n")

    async def fake_judge(
        items: list[JudgeItem], *args: object, **kwargs: object
    ) -> dict[str, tuple[bool | None, str]]:
        return {i.key: (True, "raw") for i in items}

    monkeypatch.setattr(judge, "judge_items", fake_judge)
    summary = judge.judge_run(cfg, dataset_paths(tmp_path / "data", "enronqa"), run, ["u"], "m")

    assert not (run / "candidate_judgements.jsonl").exists()
    assert summary == {"n": 1, "correct": 1, "unparsed": 0}


def test_sanitize_also_replaces_candidate_answers_that_echo_email_bodies(tmp_path: Path) -> None:
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "tasks" / "sanitize_results.py"
    spec = importlib.util.spec_from_file_location("sanitize_results", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = {
        "qid": "q",
        "answer": "Answer: ok",
        "final_answer": "ok",
        "owner_answer": {
            "candidates": [
                {"source": "a", "answer": "ok"},
                {"source": "b", "answer": "Emails: Subject: x\n=====\nbody"},
            ]
        },
    }
    path = tmp_path / "results.jsonl"
    path.write_text(json.dumps(row) + "\n")

    changed = module.sanitize_file(path)

    cands = json.loads(path.read_text())["owner_answer"]["candidates"]
    assert changed == 1
    assert cands[0]["answer"] == "ok"
    assert "=====" not in cands[1]["answer"] and "body" not in cands[1]["answer"]


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([{"token": "a", "logprob": -1.0}, {"token": "b", "logprob": -3.0}], (-2.0, 2)),
        ([], (None, 0)),
        (None, (None, 0)),
    ],
)
def test_mean_logprob_averages_the_token_log_probabilities(
    entries: object, expected: tuple[float | None, int]
) -> None:
    from atrium.llm import mean_logprob

    assert mean_logprob(entries) == expected


async def test_chat_asks_for_logprobs_only_when_requested(cfg: AtriumConfig) -> None:
    from atrium.llm import chat

    seen: list[dict[str, object]] = []

    def reply(body: dict[str, object]) -> str:
        seen.append(body)
        return "Answer: x"

    client = httpx.AsyncClient(transport=ollama_mock(reply, [-0.5, -1.5]))
    plain = await chat(client, "http://o", "m", [], cfg.llm)
    scored = await chat(client, "http://o", "m", [], cfg.llm, logprobs=True)

    assert "logprobs" not in seen[0] and seen[1]["logprobs"] is True
    assert (plain.avg_logprob, plain.n_logprob_tokens) == (None, 0)
    assert (scored.avg_logprob, scored.n_logprob_tokens) == (-1.0, 2)
