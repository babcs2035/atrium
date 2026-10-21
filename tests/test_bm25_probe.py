"""bm25_probe（p0007 A4）の仕様：外部の IDF での BM25，本文だけの probe 用の索引，probe の要求・応答，Router の選択がオフラインと一致すること．"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from atrium.arrays import F32Array
from atrium.benchmarks import Question
from atrium.config import AtriumConfig
from atrium.data_enronqa import HEADER_SEPARATOR
from atrium.frontier_core import FrontierData
from atrium.frontier_methods import (
    bm25_index,
    build_bm25_probe,
    cori_weights,
    source_centers,
    term_index,
)
from atrium.hybrid import BM25_B, BM25_K1, Bm25Index
from atrium.manifest import NodeAssignment, Placement
from atrium.node import create_app
from atrium.protocol import Bm25Query
from atrium.requester import Requester, discover_shards
from atrium.routing import RoutingQuery, build_source_profiles, make_router
from atrium.routing.advert import top_m
from atrium.routing.summary import (
    Bm25ProbeRouter,
    Bm25Stats,
    SummaryArtifact,
    SummaryProbeRouter,
    load_summary,
    write_summary,
)
from atrium.store import FaissShardStore, load_shard
from tests.conftest import HostDispatchTransport, ollama_mock, with_experiment, write_faiss_shard
from tests.test_frontier import DIM, _data

ENCODER = "enc"
BM25_WEIGHT = 2.0


# ── Bm25Index：外部の IDF・長さの平均で採点する経路 ─────────────────────────────────


def _local_idf(index_tokens: list[list[str]], terms: set[str]) -> dict[str, float]:
    """受信箱だけの統計の IDF（Bm25Index の式）．外部の統計に同じ値を渡すと `scores` と一致するはずの値．"""
    n = len(index_tokens)
    out: dict[str, float] = {}
    for term in terms:
        df = sum(term in set(doc) for doc in index_tokens)
        out[term] = float(np.log(1 + (n - df + 0.5) / (df + 0.5)))
    return out


def test_external_statistics_equal_to_the_local_ones_give_the_local_scores() -> None:
    docs = [["a", "b", "b"], ["b", "c"], ["c", "c", "d", "e"], ["a"]]
    index = Bm25Index(docs, keep_counts=True)
    avg = sum(len(d) for d in docs) / len(docs)
    query = ["b", "c", "zzz"]
    got = index.scores_with_idf(_local_idf(docs, {"b", "c"}), avg)
    assert got == pytest.approx(index.scores(query))


def test_external_idf_scales_the_score_of_a_term_linearly() -> None:
    docs = [["rare", "x"], ["x", "y"]]
    index = Bm25Index(docs, keep_counts=True)
    one = index.scores_with_idf({"rare": 1.0}, 2.0)
    three = index.scores_with_idf({"rare": 3.0}, 2.0)
    assert three == pytest.approx(3 * one)
    # 1 語 1 回・長さ 2・平均 2 の文書の得点 = idf × (k1 + 1) / (1 + k1)（長さの正規化が 1）
    assert one[0] == pytest.approx((BM25_K1 + 1) / (1 + BM25_K1 * (1 - BM25_B + BM25_B)))
    assert one[1] == 0.0


def test_the_average_length_changes_the_length_normalisation() -> None:
    index = Bm25Index([["t", "u", "v", "w"]], keep_counts=True)
    short_avg = index.scores_with_idf({"t": 1.0}, 2.0)[0]
    long_avg = index.scores_with_idf({"t": 1.0}, 8.0)[0]
    assert long_avg > short_avg


def test_external_statistics_need_an_index_built_with_counts() -> None:
    with pytest.raises(ValueError, match="keep_counts"):
        Bm25Index([["a"]]).scores_with_idf({"a": 1.0}, 1.0)
    with pytest.raises(ValueError, match="positive"):
        Bm25Index([["a"]], keep_counts=True).scores_with_idf({"a": 1.0}, 0.0)


# ── ストア：本文だけの probe 用の索引 ─────────────────────────────────────────────


def _mail(subject_word: str, body: str) -> str:
    """EnronQA の content と同じ形（ヘッダー，区切り線，本文）．"""
    return f"Subject: {subject_word}\nSender: {subject_word}@x.com\n\n{HEADER_SEPARATOR}\n{body}"


def _unit(rng: np.random.Generator, n: int) -> F32Array:
    x = rng.normal(size=(n, DIM)).astype(np.float32)
    out: F32Array = x / np.linalg.norm(x, axis=1, keepdims=True)
    return out


def _write_mails(root: Path, shard_id: str, contents: list[str]) -> Path:
    """content を差し替えた faiss のシャードを作る（行の数 = len(contents)）．"""
    write_faiss_shard(
        root, shard_id, shard_id, {"f0": _unit(np.random.default_rng(0), len(contents))}
    )
    chunk = root / shard_id / "chunk" / "f0.jsonl"
    with chunk.open("w", encoding="utf-8") as f:
        for i, content in enumerate(contents):
            f.write(json.dumps({"id": f"{shard_id}/{i}", "title": "t", "content": content}) + "\n")
    return root / shard_id


def _probe_store(shard_dir: Path, flag: bool = True) -> FaissShardStore:
    store = load_shard(shard_dir, "none", 50, flag)
    assert isinstance(store, FaissShardStore)
    return store


def test_probe_bm25_uses_the_body_only_and_ignores_the_header(tmp_path: Path) -> None:
    shard_dir = _write_mails(
        tmp_path,
        "inbox",
        [_mail("secretword", "plain words here"), _mail("other", "budget meeting notes")],
    )
    store = _probe_store(shard_dir)
    avg = 3.0
    # ヘッダーにしかない語（件名・差出人）は，本文だけの索引には無い
    assert store.probe_bm25({"secretword": 5.0}, avg) == 0.0
    assert store.probe_bm25({"budget": 5.0}, avg) > 0.0


def test_probe_bm25_returns_the_maximum_over_visible_documents(tmp_path: Path) -> None:
    shard_dir = _write_mails(
        tmp_path, "inbox", [_mail("a", "alpha alpha beta"), _mail("b", "alpha gamma delta")]
    )
    store = _probe_store(shard_dir)
    both = store.probe_bm25({"alpha": 1.0}, 3.0)
    store.hide(["inbox/0"])
    only_second = store.probe_bm25({"alpha": 1.0}, 3.0)
    assert both > only_second > 0.0
    store.hide(["inbox/1"])
    assert store.probe_bm25({"alpha": 1.0}, 3.0) == 0.0


def test_probe_bm25_is_rebuilt_when_a_mail_is_added(tmp_path: Path) -> None:
    from atrium.store import MailDoc

    shard_dir = _write_mails(tmp_path, "inbox", [_mail("a", "alpha beta")])
    store = _probe_store(shard_dir)
    assert store.probe_bm25({"zebra": 1.0}, 2.0) == 0.0
    timing = store.add(
        [MailDoc("new", "t", _mail("n", "zebra crossing"))], _unit(np.random.default_rng(3), 1)
    )
    assert store.probe_bm25({"zebra": 1.0}, 2.0) > 0.0
    assert timing.bm25_s > 0.0


def test_probe_bm25_is_rejected_when_the_shard_has_no_probe_index(tmp_path: Path) -> None:
    store = _probe_store(_write_mails(tmp_path, "inbox", [_mail("a", "alpha")]), flag=False)
    with pytest.raises(ValueError, match="without the body BM25 probe index"):
        store.probe_bm25({"alpha": 1.0}, 1.0)


def test_the_fusion_index_keeps_using_the_whole_content_with_the_header(tmp_path: Path) -> None:
    """A2 の受信箱の中の RRF は従来どおり content 全体（A2・C1-RRF と同じ条件のまま）で，probe 用の索引だけが本文だけ．"""
    shard_dir = _write_mails(tmp_path, "inbox", [_mail("secretword", "plain"), _mail("b", "other")])
    store = load_shard(shard_dir, "rrf", 50, True)
    assert isinstance(store, FaissShardStore)
    emb = np.load(shard_dir / "emb" / "f0.f16.npy")[0].astype(np.float32)
    top = store.search(k=1, embedding=emb.tolist(), query_id=None, query_text="secretword")
    assert top[0].doc_id == "inbox/0"
    assert store.probe_bm25({"secretword": 5.0}, 3.0) == 0.0


# ── ノードの /v1/probe ───────────────────────────────────────────────────────────


def _node_client(cfg: AtriumConfig, shard_dir: Path, flag: bool) -> httpx.AsyncClient:
    app = create_app(
        "node-1",
        {"inbox": load_shard(shard_dir, "none", 50, flag)},
        cfg,
        "http://ollama",
        httpx.AsyncClient(transport=ollama_mock(lambda _: "x")),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://node-1")


async def test_probe_returns_bm25_only_when_the_request_carries_term_statistics(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    shard_dir = _write_mails(tmp_path, "inbox", [_mail("a", "alpha beta"), _mail("b", "gamma")])
    embedding = [0.1] * DIM
    async with _node_client(cfg, shard_dir, True) as client:
        plain = (
            await client.post("/v1/probe", json={"shard_ids": ["inbox"], "embedding": embedding})
        ).json()
        with_bm25 = (
            await client.post(
                "/v1/probe",
                json={
                    "shard_ids": ["inbox"],
                    "embedding": embedding,
                    "bm25": Bm25Query(idf={"alpha": 2.0}, avg_doc_len=2.0).model_dump(),
                },
            )
        ).json()
    assert plain["bm25"] is None
    assert with_bm25["scores"] == plain["scores"]
    assert with_bm25["bm25"]["inbox"] > 0.0


async def test_probe_with_term_statistics_is_a_problem_for_a_node_without_the_index(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    shard_dir = _write_mails(tmp_path, "inbox", [_mail("a", "alpha")])
    async with _node_client(cfg, shard_dir, False) as client:
        response = await client.post(
            "/v1/probe",
            json={
                "shard_ids": ["inbox"],
                "embedding": [0.1] * DIM,
                "bm25": {"idf": {"alpha": 1.0}, "avg_doc_len": 1.0},
            },
        )
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"


# ── 要約の成果物と Router ────────────────────────────────────────────────────────


def _artifact(
    summary_dir: Path, data: FrontierData | None = None
) -> tuple[FrontierData, SummaryArtifact]:
    """合成データ（6 受信箱・3 デバイス）の要約を，build-summary と同じ手順で書いて読み直す．"""
    data = data or _data()
    weights, cf = cori_weights(data, 64000)
    idx = bm25_index(data)
    stats = Bm25Stats(
        published_df=np.asarray(data.cache["published_df_64000"].sum(axis=0)).ravel(),
        n_docs=int(idx["n_docs"]),
        avg_doc_len=float(idx["avg_doc_len"]),
    )
    groups = [
        [n for n, d in zip(data.names, data.device_of_source.tolist(), strict=True) if d == dev]
        for dev in range(data.n_devices)
    ]
    write_summary(
        summary_dir,
        data.names,
        groups,
        term_index(data)["vocab"],
        weights,
        cf,
        source_centers(data, "body"),
        64000,
        stats,
    )
    return data, load_summary(summary_dir)


def test_summary_statistics_survive_a_write_and_load(tmp_path: Path) -> None:
    data, artifact = _artifact(tmp_path)
    assert artifact.bm25 is not None
    idx = bm25_index(data)
    assert artifact.bm25.n_docs == idx["n_docs"]
    assert artifact.bm25.avg_doc_len == pytest.approx(idx["avg_doc_len"])


def test_a_summary_without_bm25_statistics_is_still_loadable_but_bm25_probe_rejects_it(
    tmp_path: Path,
) -> None:
    data = _data()
    weights, cf = cori_weights(data, 100)
    groups = [[n] for n in data.names]
    write_summary(
        tmp_path,
        data.names,
        groups,
        term_index(data)["vocab"],
        weights,
        cf,
        source_centers(data, "body"),
        100,
    )
    artifact = load_summary(tmp_path)
    assert artifact.bm25 is None
    with pytest.raises(ValueError, match="bm25_stats.npz"):
        Bm25ProbeRouter(2, 2, artifact, ENCODER, 0.5, BM25_WEIGHT)


def test_query_idf_uses_the_published_document_frequency_and_n_for_unpublished_terms(
    tmp_path: Path,
) -> None:
    data, artifact = _artifact(tmp_path)
    router = Bm25ProbeRouter(2, 2, artifact, ENCODER, 0.5, BM25_WEIGHT)
    query = RoutingQuery("q", "", {}, tokens=("word0", "word0", "of", "common"))
    request = router.bm25_query(query)
    assert request.avg_doc_len == pytest.approx(artifact.bm25.avg_doc_len)  # type: ignore[union-attr]
    # 重複を除く．機能語 "of" は要約の語彙に無いので df = N（IDF ≈ 0）
    assert sorted(request.idf) == ["common", "of", "word0"]
    n = artifact.bm25.n_docs  # type: ignore[union-attr]
    assert request.idf["of"] == pytest.approx(np.log(1 + 0.5 / (n + 0.5)))
    # 全文書にある語は IDF が小さく，1 受信箱にしか無い語は大きい
    assert request.idf["word0"] > request.idf["common"]


def test_select_among_standardises_both_scores_among_the_answered_candidates() -> None:
    artifact = _artifact_without_files()
    router = Bm25ProbeRouter(1, 2, artifact, ENCODER, 0.5, BM25_WEIGHT)
    dense = {"a": 0.9, "b": 0.5, "c": 0.4, "silent": float("-inf")}
    bm25 = {"a": 1.0, "b": 9.0, "c": 2.0}
    # 密ベクトルだけなら a だが，BM25 の重み 2 を足すと b が勝つ
    assert router.select_among(["a", "b", "c", "silent"], dense, bm25) == ["b"]
    # 答えなかった候補は標準化に入らず，選ばれない（m が大きくても得点は −inf で最後）
    wide = Bm25ProbeRouter(3, 2, artifact, ENCODER, 0.5, BM25_WEIGHT)
    assert wide.select_among(["a", "b", "c", "silent"], dense, bm25) == ["a", "b", "c"]


def test_select_among_needs_the_bm25_scores() -> None:
    router = Bm25ProbeRouter(1, 2, _artifact_without_files(), ENCODER, 0.5, BM25_WEIGHT)
    with pytest.raises(ValueError, match="BM25 scores"):
        router.select_among(["a"], {"a": 1.0})


def _artifact_without_files() -> SummaryArtifact:
    """select_among だけを試す最小の要約（1 段目は使わない）．"""
    from scipy import sparse

    return SummaryArtifact(
        groups=[["a"]],
        vocab={"x": 0},
        weights=sparse.csr_matrix((1, 1)),
        cf=np.zeros(1),
        centers=[np.zeros((1, DIM), dtype=np.float32)],
        term_size=1,
        names=["a"],
        bm25=Bm25Stats(published_df=np.zeros(1), n_docs=10, avg_doc_len=5.0),
    )


def test_bm25_probe_is_a_summary_probe_router_with_hybrid_first_stage(tmp_path: Path) -> None:
    _, artifact = _artifact(tmp_path)
    router = Bm25ProbeRouter(2, 2, artifact, ENCODER, 0.5, BM25_WEIGHT)
    assert isinstance(router, SummaryProbeRouter)
    assert router.name == "bm25_probe"
    assert router.mode == "hybrid"


def test_make_router_builds_bm25_probe_from_the_config_and_the_summary_directory(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    _artifact(tmp_path / "summary")
    owner = with_experiment(
        cfg, dataset="enronqa", routing="bm25_probe", answer_mode="owner_answer"
    )
    owner = owner.model_copy(update={"answer": owner.answer.model_copy(update={"owner_m": 3})})
    router = make_router(owner, tmp_path / "router")
    assert isinstance(router, Bm25ProbeRouter)
    assert (router.m, router.probe_d) == (3, 24)
    retrieval_only = with_experiment(cfg, dataset="enronqa", routing="bm25_probe")
    assert make_router(retrieval_only, tmp_path / "router").m == cfg.routing.top_m["enronqa"]  # type: ignore[attr-defined]


def test_the_default_config_keeps_the_existing_routers_and_adds_the_bm25_probe_keys(
    cfg: AtriumConfig,
) -> None:
    assert cfg.routing.bm25_probe_d == {"enronqa": 24}
    assert cfg.routing.bm25_weight == 2.0
    assert cfg.routing.hybrid_probe_d == {"enronqa": 16}
    assert cfg.experiment.routing == "all"


# ── オフライン（frontier_methods の bm25_fuse・published）との一致 ──────────────────────


async def _requester_with_one_node_per_device(
    cfg: AtriumConfig,
    root: Path,
    data: FrontierData,
    router: Bm25ProbeRouter,
    vectors: dict[str, F32Array],
) -> Requester:
    """合成データの受信箱を，デバイスごとに 1 ノード（そのデバイスの受信箱を全て）へ置き（content はヘッダー付き），質問者を作る．"""
    nodes: dict[str, httpx.AsyncBaseTransport] = {"ollama": ollama_mock(lambda _: "x")}
    llm_client = httpx.AsyncClient(transport=HostDispatchTransport(nodes))
    assignments = []
    for device in range(data.n_devices):
        stores = {}
        for i, name in enumerate(data.names):
            if int(data.device_of_source[i]) != device:
                continue
            bodies = [" ".join(toks) for toks in (data.summary_tokens or [])[i]]
            shard_dir = _write_mails(
                root, name, [_mail(f"hdr{i}x{j}", body) for j, body in enumerate(bodies)]
            )
            # 埋め込みは合成データのものに差し替える（行の数は同じ）
            np.save(shard_dir / "emb" / "f0.f16.npy", data.summary_emb[i].astype(np.float16))
            stores[name] = load_shard(shard_dir, "none", 50, True)
        nodes[f"node-{device}"] = httpx.ASGITransport(
            app=create_app(f"node-{device}", stores, cfg, "http://ollama", llm_client)
        )
        assignments.append(
            NodeAssignment(
                host=f"node-{device}", url=f"http://node-{device}", shard_ids=list(stores)
            )
        )
    client = httpx.AsyncClient(transport=HostDispatchTransport(nodes))
    placement = Placement(dataset="enronqa", nodes=assignments)
    sources = build_source_profiles(await discover_shards(client, placement), data.names)

    class StubEmbedder:
        def embed(self, question: Question) -> dict[str, F32Array]:
            return {ENCODER: vectors[question.qid]}

    return Requester(
        cfg=cfg,
        placement=placement,
        sources=sources,
        router=router,
        embedder=StubEmbedder(),
        client=client,
        ollama_url="http://ollama",
    )


@pytest.mark.parametrize("m", [1, 2])
async def test_live_selection_equals_the_offline_bm25_fuse_selection(
    cfg: AtriumConfig, tmp_path: Path, m: int
) -> None:
    data = _data()
    # オフラインの密ベクトルの得点を，ノードが返す値（受信箱の中の最大の内積）と同じ定義にする
    q_emb = (
        np.random.default_rng(7).normal(size=(len(data.q_tokens["test"]), DIM)).astype(np.float32)
    )
    second = np.stack(
        [(q_emb @ e.astype(np.float16).astype(np.float32).T).max(axis=1) for e in data.summary_emb],
        axis=1,
    )
    data = dataclasses.replace(
        data, q_emb={"dev": q_emb, "test": q_emb}, second={"dev": second, "test": second}
    )
    probe_devices = 2
    (offline,) = [
        c
        for c in build_bm25_probe(data)
        if c.params == {"V": 64000, "w": 0.5, "D": probe_devices, "idf": "published", "wb": 2.0}
    ]
    selection = offline.select("test")
    fused = np.where(selection.candidates, selection.second, -np.inf)
    expected = [top_m(data.names, row.tolist(), m) for row in fused]

    _, artifact = _artifact(tmp_path / "summary", data)
    router = Bm25ProbeRouter(m, probe_devices, artifact, ENCODER, 0.5, BM25_WEIGHT)
    questions = [
        Question(f"enron/q{q}", "enron", " ".join(data.q_tokens["test"][q]), {}, None)
        for q in range(len(expected))
    ]
    requester = await _requester_with_one_node_per_device(
        with_experiment(cfg, dataset="enronqa", answer_mode="retrieval_only"),
        tmp_path / "shards",
        data,
        router,
        {q.qid: q_emb[i] for i, q in enumerate(questions)},
    )
    for i, question in enumerate(questions):
        record = await requester.process(question)
        assert record["error"] is None
        assert sorted(record["selected_sources"]) == sorted(expected[i]), f"question {i}"
        # 本照会の前の probe は，上位 D 台のデバイスにだけ送る
        assert record["probe_recipients"] == probe_devices
        # answer.save_candidates が既定（false）なら，probe の得点は記録に足さない（従来の記録のまま）
        assert "probe_selected_scores" not in record


async def test_save_candidates_records_the_fused_probe_scores_of_the_selected_inboxes(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    """answer.save_candidates（p0007 A5）では，選んだ受信箱の probe の得点（密ベクトル・BM25・融合得点）を残す．
    融合得点はオフラインの bm25_fuse の得点と同じ（候補の選び方の特徴量 R1 の照合）．"""
    data = _data()
    q_emb = (
        np.random.default_rng(7).normal(size=(len(data.q_tokens["test"]), DIM)).astype(np.float32)
    )
    second = np.stack(
        [(q_emb @ e.astype(np.float16).astype(np.float32).T).max(axis=1) for e in data.summary_emb],
        axis=1,
    )
    data = dataclasses.replace(
        data, q_emb={"dev": q_emb, "test": q_emb}, second={"dev": second, "test": second}
    )
    (offline,) = [
        c
        for c in build_bm25_probe(data)
        if c.params == {"V": 64000, "w": 0.5, "D": 2, "idf": "published", "wb": 2.0}
    ]
    selection = offline.select("test")
    _, artifact = _artifact(tmp_path / "summary", data)
    router = Bm25ProbeRouter(2, 2, artifact, ENCODER, 0.5, BM25_WEIGHT)
    base = with_experiment(cfg, dataset="enronqa", answer_mode="retrieval_only")
    saving = base.model_copy(
        update={"answer": base.answer.model_copy(update={"save_candidates": True})}
    )
    questions = [
        Question(f"enron/q{q}", "enron", " ".join(data.q_tokens["test"][q]), {}, None)
        for q in range(len(data.q_tokens["test"]))
    ]
    requester = await _requester_with_one_node_per_device(
        saving,
        tmp_path / "shards",
        data,
        router,
        {q.qid: q_emb[i] for i, q in enumerate(questions)},
    )
    column = {name: i for i, name in enumerate(data.names)}
    for i, question in enumerate(questions):
        record = await requester.process(question)
        scores = record["probe_selected_scores"]
        assert sorted(scores) == sorted(record["selected_sources"])
        for source, s in scores.items():
            assert s["fused"] == pytest.approx(float(selection.second[i, column[source]]), abs=1e-6)
