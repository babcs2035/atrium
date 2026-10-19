"""routing=central_local（p0007 I4）の仕様：1 つの索引の上位 5 通・ノードに問い合わせない・起動の費用の記録・C1 との一致の判定．"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from atrium.arrays import F32Array
from atrium.benchmarks import Question
from atrium.central_eval import dense_top_k, judge_equivalence
from atrium.config import AtriumConfig, ExperimentConfig
from atrium.manifest import Placement
from atrium.paths import dataset_paths
from atrium.requester import Requester
from atrium.routing import make_router
from atrium.routing.central import CentralIndex, CentralLocalRouter, inbox_of_doc
from tests.conftest import ollama_mock, with_experiment

DIM = 6
K = 5
INBOXES = ("alice", "bob", "carol")
DOCS_PER_INBOX = 8


def write_corpus(root: Path) -> tuple[dict[str, np.ndarray], list[str]]:
    """3 受信箱の chunk と埋め込み（fp16）を atrium.paths の構成で書く．（受信箱 → fp32 の埋め込み，全文書 ID の連結）を返す．"""
    paths = dataset_paths(root, "enronqa")
    rng = np.random.default_rng(3)
    embs: dict[str, np.ndarray] = {}
    doc_ids: list[str] = []
    for name in INBOXES:
        (paths.corpus(name) / "chunk").mkdir(parents=True)
        (paths.corpus(name) / "emb").mkdir()
        emb = rng.normal(size=(DOCS_PER_INBOX, DIM)).astype(np.float16)
        np.save(paths.corpus(name) / "emb" / f"{name}.f16.npy", emb)
        ids = [f"{name}/inbox/{i}." for i in range(DOCS_PER_INBOX)]
        with (paths.corpus(name) / "chunk" / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for doc_id in ids:
                f.write(
                    json.dumps({"id": doc_id, "title": "t", "content": f"body of {doc_id}"}) + "\n"
                )
        embs[name] = emb.astype(np.float32)
        doc_ids += ids
    return embs, doc_ids


def central_cfg(cfg: AtriumConfig) -> AtriumConfig:
    """central_local・snippet_return の設定（受信箱は 3 つ，k_context は 5）．"""
    enron = cfg.data.require_enronqa()
    data = cfg.data.model_copy(
        update={"enronqa": enron.model_copy(update={"sources": list(INBOXES), "k_context": K})}
    )
    changed = with_experiment(
        cfg, dataset="enronqa", routing="central_local", answer_mode="snippet_return"
    )
    return changed.model_copy(update={"data": data})


class StubEmbedder:
    """全ての質問に同じ埋め込みを返す．"""

    def __init__(self, query: F32Array, encoder: str) -> None:
        """質問の埋め込みと検索器名．"""
        self._query = query
        self._encoder = encoder

    def embed(self, question: Question) -> dict[str, F32Array]:
        """検索器名 → 埋め込み．"""
        return {self._encoder: self._query}


def test_index_returns_the_top_five_documents_over_all_inboxes_like_central_eval(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    embs, doc_ids = write_corpus(tmp_path)
    index = CentralIndex.from_corpus(central_cfg(cfg), dataset_paths(tmp_path, "enronqa"))
    queries = np.random.default_rng(4).normal(size=(10, DIM)).astype(np.float32)

    expected = dense_top_k(queries, np.concatenate(list(embs.values())), K)

    for query, rows in zip(queries, expected.tolist(), strict=True):
        found = index.search(query, K)
        assert [d.doc_id for d in found] == [doc_ids[i] for i in rows]
        assert [d.score for d in found] == sorted((d.score for d in found), reverse=True)
        # 本文は索引が持つ（ノードを介さずに質問者が答えに使う）
        assert found[0].content == f"body of {found[0].doc_id}"


def test_index_records_how_long_building_it_took(cfg: AtriumConfig, tmp_path: Path) -> None:
    write_corpus(tmp_path)
    load = CentralIndex.from_corpus(central_cfg(cfg), dataset_paths(tmp_path, "enronqa")).load
    assert (load.n_docs, load.n_inboxes) == (len(INBOXES) * DOCS_PER_INBOX, len(INBOXES))
    assert load.total_s >= load.read_docs_s + load.read_emb_s > 0


def test_ties_are_broken_by_the_smaller_row(cfg: AtriumConfig, tmp_path: Path) -> None:
    write_corpus(tmp_path)
    index = CentralIndex.from_corpus(central_cfg(cfg), dataset_paths(tmp_path, "enronqa"))
    index.emb[:] = 0.0  # 全文書が同点
    assert [d.doc_id for d in index.search(np.ones(DIM, np.float32), K)] == index.doc_ids[:K]


async def test_central_local_answers_from_the_top_five_without_contacting_any_node(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    embs, doc_ids = write_corpus(tmp_path)
    config = central_cfg(cfg)
    index = CentralIndex.from_corpus(config, dataset_paths(tmp_path, "enronqa"))
    query = np.random.default_rng(5).normal(size=DIM).astype(np.float32)
    prompts: list[str] = []

    def reply(body: dict[str, object]) -> str:
        prompts.append(json.dumps(body))
        return "Answer: central"

    # ノードへの要求は，宛先が無いのでどれも失敗する（問い合わせれば error になる）
    requester = Requester(
        cfg=config,
        placement=Placement(dataset="enronqa", nodes=[]),
        sources=[],
        router=CentralLocalRouter(),
        embedder=StubEmbedder(query, config.data.require_enronqa().encoder),
        client=httpx.AsyncClient(transport=ollama_mock(reply)),
        ollama_url="http://ollama",
        central_index=index,
    )

    record = await requester.process(Question("enronqa/test/0-0", "alice", "who?", {}, "central"))

    top = dense_top_k(query[None, :], np.concatenate(list(embs.values())), K)[0]
    assert record["error"] is None
    assert record["top_doc_ids"] == [doc_ids[i] for i in top]
    assert record["final_answer"] == "central"
    # 質問者の LLM に渡した断片は，索引の上位 5 通
    assert len(prompts) == 1 and all(f"body of {doc_ids[i]}" in prompts[0] for i in top)
    assert (record["n_shards_queried"], record["query_recipients"], record["snippets_exposed"]) == (
        0,
        0,
        0,
    )
    assert record["selected_sources"] == []


def test_inbox_is_the_first_component_of_the_document_id() -> None:
    assert inbox_of_doc("kean-s/sent/12.") == "kean-s"


def test_make_router_builds_the_central_local_router_without_any_summary(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    router = make_router(central_cfg(cfg), tmp_path)
    assert isinstance(router, CentralLocalRouter) and router.select(None, []) == []  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"answer_mode": "retrieval_only"},
        {"requester_procs": 2, "parallel": 2},
        {"dataset": "medrag"},
    ],
)
def test_central_local_is_limited_to_the_b1_equivalent_single_process_condition(
    cfg: AtriumConfig, changes: dict[str, object]
) -> None:
    base = {**cfg.experiment.model_dump(), "routing": "central_local", "dataset": "enronqa"}
    base["answer_mode"] = "snippet_return"
    with pytest.raises(ValueError, match="central_local"):
        ExperimentConfig.model_validate({**base, **changes})


def test_owner_m_replaces_top_m_only_for_owner_answer(cfg: AtriumConfig, tmp_path: Path) -> None:
    owner = with_experiment(
        cfg, dataset="enronqa", routing="flood_score", answer_mode="owner_answer"
    )
    owner = owner.model_copy(update={"answer": cfg.answer.model_copy(update={"owner_m": 3})})
    plain = with_experiment(
        cfg, dataset="enronqa", routing="flood_score", answer_mode="snippet_return"
    )
    assert make_router(owner, tmp_path).m == 3  # type: ignore[attr-defined]
    assert make_router(plain, tmp_path).m == cfg.routing.top_m["enronqa"]  # type: ignore[attr-defined]


# ── C1 との一致の判定（D9） ───────────────────────────────────────────────


def test_exact_agreement_at_the_threshold_is_equivalent() -> None:
    verdict = judge_equivalence(0.999, 1.0)
    assert (verdict["verdict"], verdict["switched_to_tie_aware"]) == ("equivalent", False)


def test_strict_failure_caused_only_by_ties_switches_to_the_tie_aware_judgement() -> None:
    # p0006 P1 の C1 の実測（厳密 0.9942・同点を許して 1.0000）と同じ形
    verdict = judge_equivalence(0.9942, 1.0)
    assert (verdict["verdict"], verdict["switched_to_tie_aware"]) == ("equivalent_tie_aware", True)


def test_failure_that_ties_do_not_explain_is_not_equivalent() -> None:
    verdict = judge_equivalence(0.99, 0.995)
    assert (verdict["verdict"], verdict["switched_to_tie_aware"]) == ("not_equivalent", False)
