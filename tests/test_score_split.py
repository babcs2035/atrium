"""採点を複数の server に分ける仕組み（d0009 B2．RemoteCandidateScorer・atrium score-server・score-bench の閉じた系）の仕様．"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from atrium.protocol import ScoreRequest
from atrium.requester import CrossEncoderReranker, RemoteCandidateScorer, sigmoid_scores
from atrium.score_bench import ClosedLoop, QuestionInput
from atrium.score_server import create_score_app

URLS = ["http://s0", "http://s1", "http://s2"]


class _LengthScorer:
    """文の長さを得点にする偽物（server の中身は CrossEncoderReranker.scores．実機の得点の一致は G-S で確かめる）．"""

    def scores(self, query: str, texts: Sequence[str]) -> list[float]:
        return [float(len(t)) for t in texts]


def _echo_handler(request: httpx.Request) -> httpx.Response:
    body = ScoreRequest.model_validate_json(request.content)
    return httpx.Response(200, json={"scores": [0.5] * len(body.texts), "duration_s": 0.01})


def test_server_returns_scores_in_text_order_and_healthz() -> None:
    app = create_score_app(_LengthScorer(), "m", "rev")
    client = TestClient(app)
    reply = client.post("/v1/score", json={"query": "q", "texts": ["a", "bcd", "ef"]})
    assert reply.status_code == 200
    assert reply.json()["scores"] == [1.0, 3.0, 2.0]
    assert reply.json()["duration_s"] >= 0
    assert client.get("/healthz").json() == {"status": "ok", "model": "m", "revision": "rev"}


def test_server_rejects_a_request_with_an_unknown_field() -> None:
    client = TestClient(create_score_app(_LengthScorer(), "m", "rev"))
    assert client.post("/v1/score", json={"query": "q", "texts": [], "x": 1}).status_code == 422


def test_scorer_through_the_server_matches_the_local_scorer() -> None:
    client = TestClient(create_score_app(_LengthScorer(), "m", "rev"), base_url="http://s0")
    remote = RemoteCandidateScorer(["http://s0"], client=client)
    assert remote.scores("q", ["a", "bcd"]) == _LengthScorer().scores("q", ["a", "bcd"])


def test_empty_texts_are_not_sent() -> None:
    remote = RemoteCandidateScorer(
        URLS, client=httpx.Client(transport=httpx.MockTransport(_echo_handler))
    )
    assert remote.scores("q", []) == []
    assert all(c["sent"] == 0 for c in remote.counts().values())


def test_scorer_requires_at_least_one_url() -> None:
    with pytest.raises(ValueError):
        RemoteCandidateScorer([])


def test_sequential_requests_go_to_the_first_url_when_nothing_is_in_flight() -> None:
    """未完了が全て 0 で同数のときは並びの先（送り先の番号 0）．"""
    remote = RemoteCandidateScorer(
        URLS, client=httpx.Client(transport=httpx.MockTransport(_echo_handler))
    )
    for _ in range(3):
        assert remote.scores_with_target("q", ["t"])[1] == 0
    assert [c["sent"] for c in remote.counts().values()] == [3, 0, 0]


def test_concurrent_requests_go_to_the_url_with_the_fewest_in_flight() -> None:
    """3 件を止めたまま 4・5 件目を送ると，各 server に 1 件ずつ入った後は並びの先から埋まる．"""
    release = threading.Event()
    entered: list[str] = []
    entered_lock = threading.Lock()
    all_entered = threading.Semaphore(0)

    def handler(request: httpx.Request) -> httpx.Response:
        with entered_lock:
            entered.append(f"{request.url.scheme}://{request.url.host}")
        all_entered.release()
        release.wait(timeout=10)
        return _echo_handler(request)

    remote = RemoteCandidateScorer(
        URLS, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(remote.scores_with_target("q", ["t"])[1]))
        for _ in range(5)
    ]
    # 1 件ずつ，前の件がサーバーに届いたのを確かめてから次を送る（送り先の決定の順を固定する）
    for t in threads:
        t.start()
        assert all_entered.acquire(timeout=10)
    assert [c["in_flight"] for c in remote.counts().values()] == [2, 2, 1]
    release.set()
    for t in threads:
        t.join()
    assert sorted(results) == [0, 0, 1, 1, 2]
    assert [c["in_flight"] for c in remote.counts().values()] == [0, 0, 0]


def test_a_failed_request_is_raised_not_redirected_and_releases_its_slot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500) if request.url.host == "s0" else _echo_handler(request)

    remote = RemoteCandidateScorer(
        URLS, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(httpx.HTTPStatusError):
        remote.scores("q", ["t"])
    counts = remote.counts()
    assert counts["http://s0"] == {"sent": 1, "failed": 1, "in_flight": 0}
    assert counts["http://s1"]["sent"] == 0


def test_a_response_with_the_wrong_number_of_scores_is_a_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"scores": [1.0], "duration_s": 0.0})

    remote = RemoteCandidateScorer(
        ["http://s0"], client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ValueError):
        remote.scores("q", ["a", "b"])
    assert remote.counts()["http://s0"]["failed"] == 1


def test_one_pass_scores_every_candidate_of_every_question_once() -> None:
    inputs = [
        QuestionInput("q1", "question one", [[("d1", "aa"), ("d2", "b")], [("d3", "cccc")]]),
        QuestionInput("q2", "question two", [[("d4", "dd")], [("d5", "e")], [("d6", "ff")]]),
    ]
    client = TestClient(create_score_app(_LengthScorer(), "m", "rev"), base_url="http://s0")
    remote = RemoteCandidateScorer(["http://s0"], client=client)
    loop = ClosedLoop(remote, inputs, concurrency=2, one_pass=True)
    loop.run(0.0, None)
    assert len(loop.questions) == 2
    assert all(q.error is None for q in loop.questions)
    assert len(loop.requests) == 5
    rows = sorted(
        (p.qid, p.candidate, p.doc_id, p.score) for pairs in loop.pairs.values() for p in pairs
    )
    assert rows[0] == ("q1", 0, "d1", 2.0)
    assert len(rows) == 6


def test_a_failed_question_is_recorded_with_its_error_and_the_loop_continues() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(500) if body["query"] == "bad" else _echo_handler(request)

    inputs = [
        QuestionInput("q1", "bad", [[("d1", "a")]]),
        QuestionInput("q2", "ok", [[("d2", "b")]]),
    ]
    remote = RemoteCandidateScorer(
        ["http://s0"], client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    loop = ClosedLoop(remote, inputs, concurrency=1, one_pass=True)
    loop.run(0.0, None)
    by_qid = {q.qid: q for q in loop.questions}
    assert by_qid["q1"].error is not None and by_qid["q1"].error.startswith("HTTPStatusError")
    assert by_qid["q2"].error is None


def test_healthz_includes_the_scorer_conditions_when_given() -> None:
    """dtype・attention の実装・tokenizer の最大長（両 dtype で同じ条件かの確認）が /healthz に出る．"""
    details: dict[str, str | int] = {
        "dtype": "float16",
        "attn_implementation": "sdpa",
        "tokenizer_max_length": 8192,
    }
    client = TestClient(create_score_app(_LengthScorer(), "m", "rev", details))
    assert client.get("/healthz").json() == {
        "status": "ok",
        "model": "m",
        "revision": "rev",
        **details,
    }


def test_scores_with_target_returns_the_server_duration() -> None:
    remote = RemoteCandidateScorer(
        ["http://s0"], client=httpx.Client(transport=httpx.MockTransport(_echo_handler))
    )
    assert remote.scores_with_target("q", ["t"]) == ([0.5], 0, 0.01)
    assert remote.scores_with_target("q", []) == ([], -1, 0.0)


def test_sigmoid_scores_keep_resolution_finer_than_fp16() -> None:
    """fp16 の Sigmoid なら [0.5, 1) で 2^-11 の刻みになるが，fp32 以上で計算した得点は刻みに丸まらない．"""
    logits = [2.0, 2.0001, -3.0, 0.0]
    out = sigmoid_scores(np.asarray(logits, dtype=np.float32))
    assert out[3] == 0.5
    assert out[1] - out[0] == pytest.approx(0.1050 * 0.0001, rel=0.05)
    assert out[2] == pytest.approx(0.04742587, abs=1e-7)
    assert sigmoid_scores([]) == []


class _FakeCrossEncoder:
    """CrossEncoder の差し替え（読み込みの引数と predict の引数だけを記録する）．"""

    last: _FakeCrossEncoder | None = None

    def __init__(self, model: str, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.predict_kwargs: dict[str, object] = {}
        _FakeCrossEncoder.last = self

    def predict(self, pairs: object, **kwargs: object) -> np.ndarray:
        self.predict_kwargs = kwargs
        activation = kwargs.get("activation_fct")
        logits = np.zeros(len(pairs), dtype=np.float32)  # type: ignore[arg-type]
        return logits if activation is not None else 1 / (1 + np.exp(-logits))


def test_float32_reranker_loads_and_predicts_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """fp32（既定）は automodel_args が空（既定と同じ）で，predict に活性化の指定も足さない（今までの呼び出しと同じ）．"""
    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "CrossEncoder", _FakeCrossEncoder)
    reranker = CrossEncoderReranker()
    fake = _FakeCrossEncoder.last
    assert fake is not None
    assert fake.kwargs["automodel_args"] == {}
    assert reranker.scores("q", ["a", "b"]) == [0.5, 0.5]
    assert "activation_fct" not in fake.predict_kwargs
    assert fake.predict_kwargs["batch_size"] == CrossEncoderReranker.BATCH


def test_float16_reranker_loads_fp16_and_applies_sigmoid_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fp16 は重みを fp16 で読み，predict には Identity を渡して Sigmoid を質問者の側で計算する．"""
    import sentence_transformers
    import torch

    monkeypatch.setattr(sentence_transformers, "CrossEncoder", _FakeCrossEncoder)
    reranker = CrossEncoderReranker("float16")
    fake = _FakeCrossEncoder.last
    assert fake is not None
    assert fake.kwargs["automodel_args"] == {"torch_dtype": torch.float16}
    assert reranker.scores("q", ["a", "b", "c"]) == [0.5, 0.5, 0.5]
    assert isinstance(fake.predict_kwargs["activation_fct"], torch.nn.Identity)
    assert reranker.scores("q", []) == []


class _LengthFakeCrossEncoder(_FakeCrossEncoder):
    """max_length と tokenizer の上限だけを持つ CrossEncoder の差し替え（describe の確認用）．"""

    def __init__(self, model: str, **kwargs: object) -> None:
        super().__init__(model, **kwargs)
        self.max_length = kwargs.get("max_length")

        class _Tokenizer:
            model_max_length = 8192

        class _Model:
            class config:  # noqa: N801 (transformers の config の属性の形に合わせた差し替え)
                _attn_implementation = "sdpa"

        self.tokenizer = _Tokenizer()
        self.model = _Model()


def test_max_length_is_not_passed_to_cross_encoder_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_length を省略したときは CrossEncoder に渡さず（今までと同じ），describe は tokenizer の上限 8,192 を返す．"""
    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "CrossEncoder", _LengthFakeCrossEncoder)
    reranker = CrossEncoderReranker("float16")
    fake = _FakeCrossEncoder.last
    assert fake is not None
    assert "max_length" not in fake.kwargs
    assert reranker.describe()["max_length"] == 8192


def test_max_length_is_passed_to_cross_encoder_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """max_length=1024 は CrossEncoder に渡り，describe が実際に効いている 1,024 を返す（tokenizer の上限は残す）．"""
    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "CrossEncoder", _LengthFakeCrossEncoder)
    reranker = CrossEncoderReranker("float16", max_length=1024)
    fake = _FakeCrossEncoder.last
    assert fake is not None
    assert fake.kwargs["max_length"] == 1024
    described = reranker.describe()
    assert described["max_length"] == 1024
    assert described["tokenizer_max_length"] == 8192
