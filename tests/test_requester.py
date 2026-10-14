"""質問者の end-to-end の仕様（専門家ノード 2 台を ASGI アプリとして直接呼ぶ）．

構成: pubmed を 2 シャードに分けて node-a / node-b に置き，textbooks を node-b に重ねて置く．
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from atrium.arrays import F32Array
from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.labels import contributing_sources_from_topk
from atrium.manifest import NodeAssignment, Placement
from atrium.node import create_app
from atrium.requester import RETRIEVE_RETRIES, Requester, discover_shards, run_questions
from atrium.routing import AllRouter, NoneRouter, Router, build_source_profiles
from atrium.store import load_shard
from tests.conftest import HostDispatchTransport, ollama_mock, with_experiment, write_faiss_shard

DIM = 8
K_RET = 3
K_RERANK = 4


class StubEmbedder:
    """質問 ID → 固定のクエリ埋め込み．"""

    def __init__(self, vectors: dict[str, F32Array]) -> None:
        self._vectors = vectors

    def embed(self, question: Question) -> dict[str, F32Array]:
        return {"enc": self._vectors[question.qid]}


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, F32Array]:
    rng = np.random.default_rng(1)
    mats = {
        name: rng.normal(size=(n, DIM)).astype(np.float32)
        for name, n in (("p0", 6), ("p1", 5), ("t0", 7))
    }
    write_faiss_shard(tmp_path, "pubmed-00", "pubmed", {"p0": mats["p0"]})
    write_faiss_shard(tmp_path, "pubmed-01", "pubmed", {"p1": mats["p1"]})
    write_faiss_shard(tmp_path, "textbooks-00", "textbooks", {"t0": mats["t0"]})
    return mats


def _questions(n: int) -> list[Question]:
    return [
        Question(f"medqa/{i:04d}", "medqa", f"question {i}", {"A": "x", "B": "y"}, "B")
        for i in range(n)
    ]


def _cfg(cfg: AtriumConfig, mode: str) -> AtriumConfig:
    cfg = with_experiment(cfg, answer_mode=mode, dataset="medrag")
    return cfg.model_copy(
        update={
            "retrieval": cfg.retrieval.model_copy(update={"k_ret": K_RET, "k_rerank": K_RERANK})
        }
    )


async def _requester(
    cfg: AtriumConfig, root: Path, router: Router, vectors: dict[str, F32Array]
) -> Requester:
    ollama = ollama_mock(lambda _: '{"answer_choice": "B"}')
    # ノードから外への要求（Ollama と，B4 の委託先のノード）．ノードのアプリは作った後に routes へ足す
    node_routes: dict[str, httpx.AsyncBaseTransport] = {"ollama": ollama}
    llm_client = httpx.AsyncClient(transport=HostDispatchTransport(node_routes))
    apps = {
        "node-a": create_app(
            "node-a",
            {"pubmed-00": load_shard(root / "pubmed-00")},
            cfg,
            "http://ollama",
            llm_client,
        ),
        "node-b": create_app(
            "node-b",
            {
                "pubmed-01": load_shard(root / "pubmed-01"),
                "textbooks-00": load_shard(root / "textbooks-00"),
            },
            cfg,
            "http://ollama",
            llm_client,
        ),
    }
    for name, app in apps.items():
        node_routes[name] = httpx.ASGITransport(app=app)
    transport = HostDispatchTransport(
        {
            "node-a": httpx.ASGITransport(app=apps["node-a"]),
            "node-b": httpx.ASGITransport(app=apps["node-b"]),
            "ollama": ollama,
        }
    )
    client = httpx.AsyncClient(transport=transport)
    placement = Placement(
        dataset="medrag",
        nodes=[
            NodeAssignment(host="node-a", url="http://node-a", shard_ids=["pubmed-00"]),
            NodeAssignment(
                host="node-b", url="http://node-b", shard_ids=["pubmed-01", "textbooks-00"]
            ),
        ],
    )
    sources = build_source_profiles(
        await discover_shards(client, placement), ["pubmed", "textbooks"]
    )
    return Requester(
        cfg=cfg,
        placement=placement,
        sources=sources,
        router=router,
        embedder=StubEmbedder(vectors),
        client=client,
        ollama_url="http://ollama",
    )


async def test_runtime_merge_matches_hub_labels(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    rng = np.random.default_rng(2)
    questions = _questions(12)
    vectors = {q.qid: rng.normal(size=DIM).astype(np.float32) for q in questions}
    requester = await _requester(_cfg(cfg, "retrieval_only"), tmp_path, AllRouter(), vectors)

    # 中継点と同じ計算：データ源ごとの全文書との内積から上位 K_RET を取り，統合後の上位 K_RERANK を見る
    def f16(m: F32Array) -> F32Array:
        return m.astype(np.float16).astype(np.float32)

    q = np.stack([vectors[x.qid] for x in questions])
    top = {
        "pubmed": -np.sort(-(q @ np.concatenate([f16(corpus["p0"]), f16(corpus["p1"])]).T), axis=1)[
            :, :K_RET
        ],
        "textbooks": -np.sort(-(q @ f16(corpus["t0"]).T), axis=1)[:, :K_RET],
    }
    expected = contributing_sources_from_topk(top, K_RERANK)

    records = [await requester.process(x) for x in questions]
    assert [r["contributing_sources"] for r in records] == expected
    assert all(r["n_shards_queried"] == 3 for r in records)
    assert all(r["snippets_exposed"] == 3 * K_RET for r in records)


async def test_snippet_return_grades_the_requester_llm_answer(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    questions = _questions(2)
    vectors = {q.qid: np.ones(DIM, dtype=np.float32) for q in questions}
    requester = await _requester(_cfg(cfg, "snippet_return"), tmp_path, AllRouter(), vectors)
    out = tmp_path / "results.jsonl"
    failures = await run_questions(requester, questions, out, parallel=2)
    rows = [__import__("json").loads(line) for line in out.read_text().splitlines()]
    assert failures == 0
    assert all(r["correct"] is True and r["prompt_tokens"] == 100 for r in rows)


async def test_local_answer_returns_no_snippets_and_votes(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "local_answer"), tmp_path, AllRouter(), {question.qid: np.ones(DIM, np.float32)}
    )
    record = await requester.process(question)
    assert record["snippets_exposed"] == 0
    assert {a["node_id"] for a in record["node_answers"]} == {"node-a", "node-b"}
    assert record["choice"] == "B" and record["correct"] is True


async def test_none_routing_answers_without_retrieval(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "snippet_return"),
        tmp_path,
        NoneRouter(),
        {question.qid: np.ones(DIM, np.float32)},
    )
    record = await requester.process(question)
    assert record["selected_sources"] == []
    assert record["bytes_received"] == 0
    assert record["correct"] is True


class FailingEmbedder:
    """埋め込みで想定外の例外を投げる（GPU のメモリ不足などを模す）．"""

    def embed(self, question: Question) -> dict[str, F32Array]:
        raise RuntimeError("CUDA out of memory")


async def test_unexpected_exception_is_recorded_not_raised(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(_cfg(cfg, "retrieval_only"), tmp_path, AllRouter(), {})
    requester.embedder = FailingEmbedder()
    record = await requester.process(question)
    assert record["error"] == "RuntimeError: CUDA out of memory"


async def test_failing_shard_is_recorded_as_error_for_that_question(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"), tmp_path, AllRouter(), {question.qid: np.ones(DIM, np.float32)}
    )
    # node-a は pubmed-01 を持たないので 404 を返す（配置の誤りを模す）
    requester._url_of_shard["pubmed-01"] = "http://node-a"
    record = await requester.process(question)
    assert record["error"] is not None and record["error"].startswith("HTTPStatusError")


async def test_retrieve_is_retried_when_the_node_closed_an_idle_connection(
    cfg: AtriumConfig,
    tmp_path: Path,
    corpus: dict[str, F32Array],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"),
        tmp_path,
        AllRouter(),
        {question.qid: np.ones(DIM, np.float32)},
    )
    real_post = requester.client.post
    failures_left = {"n": RETRIEVE_RETRIES}

    async def flaky_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/v1/retrieve") and failures_left["n"] > 0:
            failures_left["n"] -= 1
            raise httpx.ReadError("connection closed by the node")
        return await real_post(url, **kwargs)

    monkeypatch.setattr(requester.client, "post", flaky_post)
    record = await requester.process(question)
    assert record["error"] is None
    assert failures_left["n"] == 0
    assert record["n_shards_queried"] == 3


async def test_retrieve_gives_up_after_the_retry_limit(
    cfg: AtriumConfig,
    tmp_path: Path,
    corpus: dict[str, F32Array],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"),
        tmp_path,
        AllRouter(),
        {question.qid: np.ones(DIM, np.float32)},
    )

    async def always_fails(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ReadError("connection closed by the node")

    monkeypatch.setattr(requester.client, "post", always_fails)
    record = await requester.process(question)
    assert "ReadError" in record["error"]


async def test_flood_score_asks_every_node_for_scores_then_selects_the_best(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    from atrium.routing.advert import FloodScoreRouter

    question = _questions(1)[0]
    # textbooks の 1 件目の文書と同じ向きのクエリなら，textbooks の最高スコアが最大になる
    vec = corpus["t0"][0].astype(np.float16).astype(np.float32)
    requester = await _requester(
        _cfg(cfg, "retrieval_only"), tmp_path, FloodScoreRouter(1), {question.qid: vec}
    )
    record = await requester.process(question)
    assert record["error"] is None
    assert record["probe_recipients"] == 2
    assert record["selected_sources"] == ["textbooks"]


async def test_delegate_gpu_sends_the_retrieved_docs_to_the_paired_gpu_node(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "delegate_gpu"),
        tmp_path,
        AllRouter(),
        {question.qid: np.ones(DIM, np.float32)},
    )
    requester.placement = requester.placement.model_copy(
        update={"delegate_of": {"node-a": "node-b"}}
    )
    record = await requester.process(question)
    assert record["error"] is None
    by_node = {a["node_id"]: a for a in record["node_answers"]}
    # node-a は GPU の node-b に委託し，node-b は自分で答える
    assert by_node["node-a"]["delegated_to"] == "node-b"
    assert by_node["node-b"]["delegated_to"] is None
    # 委託で外へ出るのは node-a の上位 k 件（pubmed-00 は 6 件しか持たない）
    n_docs_a = len(corpus["p0"])
    k = cfg.local_answer.k_context
    assert record["docs_exposed_by_source"] == {"pubmed": min(k, n_docs_a)}


async def test_snippet_return_records_exposure_per_source_and_query_recipients(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"), tmp_path, AllRouter(), {question.qid: np.ones(DIM, np.float32)}
    )
    record = await requester.process(question)
    assert record["docs_exposed_by_source"] == {"pubmed": 2 * K_RET, "textbooks": K_RET}
    assert record["query_recipients"] == 2


async def test_probe_is_retried_when_the_node_closed_an_idle_connection(
    cfg: AtriumConfig,
    tmp_path: Path,
    corpus: dict[str, F32Array],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atrium.routing.advert import FloodScoreRouter

    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"),
        tmp_path,
        FloodScoreRouter(1),
        {question.qid: np.ones(DIM, np.float32)},
    )
    real_post = requester.client.post
    failures_left = {"n": 1}

    async def flaky_post(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/v1/probe") and failures_left["n"] > 0:
            failures_left["n"] -= 1
            raise httpx.ReadError("connection closed by the node")
        return await real_post(url, **kwargs)

    monkeypatch.setattr(requester.client, "post", flaky_post)
    record = await requester.process(question)
    assert record["error"] is None
    assert failures_left["n"] == 0


def _churn_requester_cfg(cfg: AtriumConfig) -> AtriumConfig:
    from atrium.config import ChurnConfig

    return with_experiment(_cfg(cfg, "retrieval_only"), churn=ChurnConfig(availability=0.7))


async def _set_up(requester: Requester, node: str, up: bool) -> None:
    response = await requester.client.post(f"http://{node}/v1/admin/availability", json={"up": up})
    assert response.status_code == 200


async def test_churn_continues_with_the_replies_of_the_nodes_that_are_up(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _churn_requester_cfg(cfg), tmp_path, AllRouter(), {question.qid: np.ones(DIM, np.float32)}
    )
    await _set_up(requester, "node-b", False)
    record = await requester.process(question)
    assert record["error"] is None
    assert record["contacted_nodes"] == 2
    assert record["failed_nodes"] == 1
    # node-b の断片は届かず，node-a の pubmed-00 の分だけが残る
    assert record["docs_exposed_by_source"] == {"pubmed": K_RET}


async def test_node_failure_still_fails_the_question_without_churn(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"), tmp_path, AllRouter(), {question.qid: np.ones(DIM, np.float32)}
    )
    # 配置の誤り（404）は churn の有無にかかわらず問の失敗として記録される．churn の許容は 503 などの離脱だけ
    requester._url_of_shard["pubmed-01"] = "http://node-a"
    record = await requester.process(question)
    assert record["error"] is not None
    assert "failed_nodes" not in record


async def test_churn_does_not_hide_a_misconfigured_shard(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _churn_requester_cfg(cfg), tmp_path, AllRouter(), {question.qid: np.ones(DIM, np.float32)}
    )

    # 404 は httpx.HTTPStatusError（HTTPError）なので離脱と同じに扱われる．別種の例外は投げ直す
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug")

    requester._retrieve = boom  # type: ignore[method-assign]
    record = await requester.process(question)
    assert record["error"] == "RuntimeError: bug"


async def test_flood_score_under_churn_selects_from_the_nodes_that_replied(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    from atrium.routing.advert import FloodScoreRouter

    question = _questions(1)[0]
    # textbooks（node-b）の文書と同じ向きのクエリでも，node-b が離脱していれば選べない
    vec = corpus["t0"][0].astype(np.float16).astype(np.float32)
    requester = await _requester(
        _churn_requester_cfg(cfg), tmp_path, FloodScoreRouter(1), {question.qid: vec}
    )
    await _set_up(requester, "node-b", False)
    record = await requester.process(question)
    assert record["error"] is None
    assert record["probe_recipients"] == 2
    assert record["probe_failed"] == 1
    assert record["selected_sources"] == ["pubmed"]


async def test_stale_member_table_hides_new_nodes_and_keeps_offline_ones(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    question = _questions(1)[0]
    requester = await _requester(
        _churn_requester_cfg(cfg), tmp_path, AllRouter(), {question.qid: np.ones(DIM, np.float32)}
    )
    # 表には node-b だけがある（node-a は表の更新の後に参加した）．見えないノードには問い合わせない．
    # pubmed は node-a のシャードも持つので，全てのシャードのノードが見えている textbooks だけが選ばれる
    requester.visible_urls = frozenset({"http://node-b"})
    record = await requester.process(question)
    assert record["selected_sources"] == ["textbooks"]
    assert record["contacted_nodes"] == 1
    assert record["failed_nodes"] == 0
    # 表に残った node-b が離脱すると，問い合わせは無駄になる
    await _set_up(requester, "node-b", False)
    record = await requester.process(question)
    assert record["contacted_nodes"] == 1
    assert record["failed_nodes"] == 1
    assert record["docs_exposed_by_source"] == {}


def _with_adverts(requester: Requester, centroids: dict[str, list[float]]) -> None:
    """データ源ごとに 1 個の中心だけを持つ公開情報を付ける（centroid_probe の 1 段目の入力）．"""
    from dataclasses import replace

    from atrium.manifest import Advert

    requester.sources = [
        replace(
            s,
            advert=Advert(
                terms=[], term_weights=[], centroids=[centroids[s.source]], card_embedding=[0.0]
            ),
        )
        for s in requester.sources
    ]


def _spy_probes(
    requester: Requester, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, list[str]]]:
    """/v1/probe に送られた（URL，shard_ids）を記録する．"""
    calls: list[tuple[str, list[str]]] = []
    original = requester.client.post

    async def spy(url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/v1/probe"):
            calls.append((url, kwargs["json"]["shard_ids"]))
        return await original(url, **kwargs)

    monkeypatch.setattr(requester.client, "post", spy)
    return calls


async def test_flood_score_still_probes_every_node_with_all_its_shards(
    cfg: AtriumConfig,
    tmp_path: Path,
    corpus: dict[str, F32Array],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atrium.routing.advert import FloodScoreRouter

    question = _questions(1)[0]
    requester = await _requester(
        _cfg(cfg, "retrieval_only"),
        tmp_path,
        FloodScoreRouter(1),
        {question.qid: np.ones(DIM, np.float32)},
    )
    calls = _spy_probes(requester, monkeypatch)
    record = await requester.process(question)
    assert sorted(calls) == [
        ("http://node-a/v1/probe", ["pubmed-00"]),
        ("http://node-b/v1/probe", ["pubmed-01", "textbooks-00"]),
    ]
    assert record["probe_shards"] == 3
    assert sorted(record["probe_shards_per_device"]) == [1, 2]
    assert record["probe_node_s_max"] >= 0.0


async def test_centroid_probe_probes_only_the_nodes_holding_the_candidates(
    cfg: AtriumConfig,
    tmp_path: Path,
    corpus: dict[str, F32Array],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atrium.routing.advert import CentroidProbeRouter

    question = _questions(1)[0]
    vec = corpus["t0"][0].astype(np.float16).astype(np.float32)
    requester = await _requester(
        _cfg(cfg, "retrieval_only"),
        tmp_path,
        CentroidProbeRouter(m=1, probe_k=1),
        {question.qid: vec},
    )
    # 1 段目は textbooks だけを候補にする（クエリと同じ向きの中心を持たせる）
    _with_adverts(requester, {"pubmed": (-vec).tolist(), "textbooks": vec.tolist()})
    calls = _spy_probes(requester, monkeypatch)
    record = await requester.process(question)
    assert record["error"] is None
    # node-a は候補のシャードを持たないので probe を受けず，node-b には候補のシャードだけを送る
    assert calls == [("http://node-b/v1/probe", ["textbooks-00"])]
    assert record["probe_recipients"] == 1
    assert record["probe_shards"] == 1
    assert record["probe_shards_per_device"] == [1]
    assert record["selected_sources"] == ["textbooks"]
    assert record["query_recipients"] >= 1


async def test_centroid_probe_scores_the_first_stage_once_per_question(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    from atrium.routing.advert import CentroidProbeRouter

    class CountingRouter(CentroidProbeRouter):
        """1 段目の採点と，選択の呼び出し回数を数える．"""

        first_stage_calls = 0

        def candidates(self, query: Any, sources: Any) -> list[str]:
            self.first_stage_calls += 1
            return super().candidates(query, sources)

    question = _questions(1)[0]
    vec = corpus["t0"][0].astype(np.float16).astype(np.float32)
    router = CountingRouter(m=1, probe_k=2)
    requester = await _requester(_cfg(cfg, "retrieval_only"), tmp_path, router, {question.qid: vec})
    _with_adverts(requester, {"pubmed": (-vec).tolist(), "textbooks": vec.tolist()})
    record = await requester.process(question)
    assert record["error"] is None
    assert router.first_stage_calls == 1
    assert record["probe_shards"] == 3


async def test_device_probe_probes_all_candidate_shards_on_the_top_devices_only(
    cfg: AtriumConfig,
    tmp_path: Path,
    corpus: dict[str, F32Array],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atrium.routing.advert import DeviceProbeRouter

    question = _questions(1)[0]
    vec = corpus["t0"][0].astype(np.float16).astype(np.float32)
    requester = await _requester(
        _cfg(cfg, "retrieval_only"),
        tmp_path,
        DeviceProbeRouter(m=1, probe_d=1),
        {question.qid: vec},
    )
    _with_adverts(requester, {"pubmed": (-vec).tolist(), "textbooks": vec.tolist()})
    # device_probe は 1 データ源 1 シャード（EnronQA の受信箱）を前提にするので，単一シャードの textbooks だけを残す
    requester.sources = [s for s in requester.sources if s.source == "textbooks"]
    calls = _spy_probes(requester, monkeypatch)
    record = await requester.process(question)
    assert record["error"] is None
    assert calls == [("http://node-b/v1/probe", ["textbooks-00"])]
    assert record["probe_recipients"] == 1
    assert record["selected_sources"] == ["textbooks"]


async def test_device_probe_fails_the_question_for_a_source_split_over_devices(
    cfg: AtriumConfig, tmp_path: Path, corpus: dict[str, F32Array]
) -> None:
    from atrium.routing.advert import DeviceProbeRouter

    question = _questions(1)[0]
    vec = corpus["t0"][0].astype(np.float16).astype(np.float32)
    requester = await _requester(
        _cfg(cfg, "retrieval_only"),
        tmp_path,
        DeviceProbeRouter(m=1, probe_d=1),
        {question.qid: vec},
    )
    _with_adverts(requester, {"pubmed": (-vec).tolist(), "textbooks": vec.tolist()})
    record = await requester.process(question)
    # pubmed は 2 台に分かれているので，デバイスを単位に選べない
    assert record["error"] is not None
    assert "single-shard" in record["error"]
