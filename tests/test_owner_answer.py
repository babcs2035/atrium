"""answer_mode=owner_answer（p0007 I2）の仕様：統合の規則 4 種・UNKNOWN の扱い・同票・候補ごとの記録．

統合の規則（純粋関数）は直接，要求の流れ（受信箱ごとの要求・委託・llm_select）は専門家ノードを ASGI で直接呼んで確かめる．
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from atrium.arrays import F32Array
from atrium.benchmarks import Question
from atrium.config import AtriumConfig, ExperimentConfig
from atrium.manifest import NodeAssignment, Placement
from atrium.merge import (
    OwnerCandidate,
    choose_abstain,
    choose_agree,
    choose_max_score,
    normalize_answer,
)
from atrium.node import create_app
from atrium.prompts import (
    build_messages,
    build_select_messages,
    extract_selected_index,
    is_unknown_answer,
)
from atrium.requester import Requester, discover_shards
from atrium.routing import AllRouter, build_source_profiles
from atrium.store import RetrievedDoc, load_shard
from tests.conftest import HostDispatchTransport, ollama_mock, with_experiment, write_faiss_shard

DIM = 4
QUESTION = Question("enronqa/test/0-0", "a", "Who sent the memo?", {}, "ALPHA")


def candidate(
    source: str, answer: str, score: float | None, unknown: bool = False
) -> OwnerCandidate:
    """統合の規則の入力（受信箱・回答・検索の最高得点・UNKNOWN か）．"""
    return OwnerCandidate(source, answer, score, unknown)


# ── 統合の規則（純粋関数） ───────────────────────────────────────────────


def test_max_score_takes_the_candidate_with_the_highest_retrieval_score() -> None:
    cands = [candidate("a", "x", 0.4), candidate("b", "y", 0.9), candidate("c", "z", 0.5)]
    assert choose_max_score(cands).index == 1


def test_max_score_tie_goes_to_the_earlier_candidate_and_missing_score_is_lowest() -> None:
    assert choose_max_score([candidate("a", "x", 0.5), candidate("b", "y", 0.5)]).index == 0
    assert choose_max_score([candidate("a", "x", None), candidate("b", "y", -1.0)]).index == 1


def test_abstain_skips_unknown_answers_even_when_they_have_the_best_score() -> None:
    cands = [
        candidate("a", "UNKNOWN", 0.9, unknown=True),
        candidate("b", "Bob", 0.5),
        candidate("c", "Carol", 0.7),
    ]
    choice = choose_abstain(cands)
    assert (choice.index, choice.fell_back) == (2, False)


def test_abstain_falls_back_to_max_score_when_every_answer_is_unknown() -> None:
    cands = [candidate("a", "UNKNOWN", 0.3, True), candidate("b", "unknown.", 0.8, True)]
    choice = choose_abstain(cands)
    assert (choice.index, choice.fell_back) == (1, True)


def test_agree_takes_the_majority_answer_over_a_single_higher_scored_one() -> None:
    cands = [
        candidate("a", "Bob.", 0.95),
        candidate("b", "The memo was sent by Alice", 0.5),
        candidate("c", "the memo was sent by  ALICE!", 0.6),
    ]
    choice = choose_agree(cands)
    # 多数の組（b・c）から，その中で得点が高い c を採る
    assert choice.index == 2
    assert choice.vote_groups == ((1, 2), (0,))


def test_agree_breaks_a_tie_between_groups_by_the_best_score() -> None:
    cands = [candidate("a", "Bob", 0.4), candidate("b", "Alice", 0.8)]
    assert choose_agree(cands).index == 1


def test_agree_does_not_merge_answers_that_normalize_to_nothing() -> None:
    cands = [candidate("a", "...", 0.9), candidate("b", "!!!", 0.1), candidate("c", "x", 0.5)]
    assert choose_agree(cands).vote_groups == ((0,), (2,), (1,))


def test_normalize_answer_ignores_case_punctuation_width_and_spacing() -> None:
    assert normalize_answer("  Ｍr. Smith,  the CFO! ") == normalize_answer("mr smith the cfo")


@pytest.mark.parametrize("choose", [choose_max_score, choose_agree])
def test_choosing_from_no_candidates_is_an_error(choose: object) -> None:
    with pytest.raises(ValueError):
        choose([])  # type: ignore[operator]


# ── プロンプトの読み書き ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("UNKNOWN", True),
        ('"unknown."', True),
        ("Unknown. The emails do not say.", True),
        ("The sender is unknown", False),
        ("unknowns in the budget", False),
        ("", False),
    ],
)
def test_unknown_answer_detection(answer: str, expected: bool) -> None:
    assert is_unknown_answer(answer) is expected


def test_abstain_prompt_adds_one_sentence_and_the_default_prompt_is_unchanged() -> None:
    docs = [RetrievedDoc("a/1", "t", "body", 0.5)]
    plain = build_messages("enronqa", "q?", docs, {})[1]["content"]
    abstain = build_messages("enronqa", "q?", docs, {}, allow_unknown=True)[1]["content"]
    assert "UNKNOWN" not in plain
    assert (
        abstain.replace(
            " If the emails do not contain the answer, write UNKNOWN as the answer.", ""
        )
        == plain
    )


def test_abstain_prompt_is_only_defined_for_enronqa() -> None:
    with pytest.raises(ValueError):
        build_messages("medrag", "q?", [], {"A": "x"}, allow_unknown=True)


def test_select_prompt_numbers_the_answers_from_one_and_excludes_email_bodies() -> None:
    user = build_select_messages("q?", ["Bob", "Carol"])[1]["content"]
    assert "Answer 1: Bob" in user and "Answer 2: Carol" in user


@pytest.mark.parametrize(
    ("output", "expected"),
    [("2", 1), ("Answer 1 is best", 0), ("3", None), ("0", None), ("none", None)],
)
def test_selected_index_is_zero_based_and_rejects_out_of_range(
    output: str, expected: int | None
) -> None:
    assert extract_selected_index(output, 2) == expected


# ── 要求の流れ（質問者と専門家ノード） ────────────────────────────────────


class Harness:
    """3 台の専門家（node-a・b・c．それぞれ受信箱 a・b・c）と質問者．回答は node ごとに指定する．"""

    def __init__(
        self,
        cfg: AtriumConfig,
        root: Path,
        replies: dict[str, str],
        logprobs: dict[str, list[float]] | None = None,
    ) -> None:
        """root に各受信箱のシャードを作る．replies は node 名 → その node の LLM の出力，logprobs はその対数確率の列．"""
        self.logprobs = logprobs or {}
        self.node_prompts: dict[str, list[str]] = {n: [] for n in replies}
        self.requester_prompts: list[str] = []
        self.requester_reply = "1"
        self.cfg = cfg
        self.root = root
        self.replies = replies
        # 質問の向きに近い順（a が最高得点）に埋め込みを置く
        for rank, name in enumerate("abc"):
            vec = np.zeros((1, DIM), np.float32)
            vec[0, 0], vec[0, 1] = 1.0 - 0.3 * rank, 0.3 * rank
            write_faiss_shard(root, f"{name}-00", name, {"f": vec})

    def requester(self) -> Requester:
        """質問者を作る（候補の選択は AllRouter で全受信箱）．"""
        routes: dict[str, httpx.AsyncBaseTransport] = {}
        for node, text in self.replies.items():

            def reply(body: dict[str, object], node: str = node, text: str = text) -> str:
                self.node_prompts[node].append(json.dumps(body))
                return text

            routes[f"ollama-{node}"] = ollama_mock(reply, self.logprobs.get(node, ()))
        llm_client = httpx.AsyncClient(transport=HostDispatchTransport(routes))
        apps = {
            node: create_app(
                node,
                {f"{node[-1]}-00": load_shard(self.root / f"{node[-1]}-00")},
                self.cfg,
                f"http://ollama-{node}",
                llm_client,
            )
            for node in self.replies
        }

        def requester_reply(body: dict[str, object]) -> str:
            self.requester_prompts.append(json.dumps(body))
            return self.requester_reply

        transport = HostDispatchTransport(
            {
                **{n: httpx.ASGITransport(app=a) for n, a in apps.items()},
                "ollama": ollama_mock(requester_reply),
            }
        )
        for node, app in apps.items():
            routes[node] = httpx.ASGITransport(app=app)  # 委託先の /v1/generate へ届くようにする
        self.client = httpx.AsyncClient(transport=transport)
        self.placement = Placement(
            dataset="enronqa",
            nodes=[
                NodeAssignment(host=n, url=f"http://{n}", shard_ids=[f"{n[-1]}-00"])
                for n in self.replies
            ],
        )
        self._apps = apps
        return Requester(
            cfg=self.cfg,
            placement=self.placement,
            sources=[],
            router=AllRouter(),
            embedder=StubEmbedder(),
            client=self.client,
            ollama_url="http://ollama",
        )

    async def discover(self, requester: Requester) -> None:
        """質問者のデータ源の一覧を，ノードの /v1/profile から作る．"""
        shards = await discover_shards(self.client, self.placement)
        requester.sources = build_source_profiles(shards, [n[-1] for n in self.replies])
        requester.__post_init__()


class StubEmbedder:
    """全ての質問に同じ埋め込み（受信箱 a に最も近い向き）を返す．"""

    def embed(self, question: Question) -> dict[str, F32Array]:
        """検索器 enc の埋め込み．"""
        return {"enc": np.array([1.0, 0.0, 0.0, 0.0], np.float32)}


def owner_cfg(
    cfg: AtriumConfig, aggregate: str, owner_m: int = 3, save_candidates: bool = False
) -> AtriumConfig:
    """owner_answer・EnronQA の設定（ノードの回答は 1 文で，選択の LLM は requester_model）．"""
    changed = with_experiment(cfg, dataset="enronqa", answer_mode="owner_answer", routing="all")
    return changed.model_copy(
        update={
            "answer": cfg.answer.model_copy(
                update={
                    "aggregate": aggregate,
                    "owner_m": owner_m,
                    "save_candidates": save_candidates,
                }
            )
        }
    )


async def run_one(
    cfg: AtriumConfig, root: Path, replies: dict[str, str], **kw: str
) -> tuple[dict[str, object], Harness]:
    """1 問を流して（記録，ハーネス）を返す．"""
    harness = Harness(cfg, root, replies)
    requester = harness.requester()
    await harness.discover(requester)
    if "requester_reply" in kw:
        harness.requester_reply = kw["requester_reply"]
    return await requester.process(QUESTION), harness


async def test_max_score_answers_with_the_best_scored_inbox_and_records_the_costs(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: ALPHA", "node-b": "Answer: BETA", "node-c": "Answer: GAMMA"}
    record, harness = await run_one(owner_cfg(cfg, "max_score"), tmp_path, replies)

    assert record["error"] is None
    assert record["final_answer"] == "ALPHA"
    info = record["owner_answer"]
    assert isinstance(info, dict)
    assert (info["n_requests"], info["n_generations"], info["n_unknown"]) == (3, 3, 0)
    assert info["n_requests_total"] == 3  # AllRouter は probe を送らない
    assert (info["chosen_source"], info["chosen_index"]) == ("a", 0)
    # 各ノードは自分の受信箱の文脈だけで 1 回ずつ答える
    assert all(len(p) == 1 for p in harness.node_prompts.values())
    assert harness.requester_prompts == []
    # 外へ出る文書は無い（GPU のノードが自分で答える）
    assert record["snippets_exposed"] == 0 and record["docs_exposed_by_source"] == {}
    assert record["query_recipients"] == 3


async def test_stored_record_keeps_only_the_chosen_answer_text(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: ALPHA", "node-b": "Answer: LEAK-B", "node-c": "Answer: LEAK-C"}
    record, _ = await run_one(owner_cfg(cfg, "max_score"), tmp_path, replies)
    stored = json.dumps(record)
    # 採らなかった候補の回答（メールの引用を含みうる）は，記録に残らない
    assert "LEAK-B" not in stored and "LEAK-C" not in stored
    assert "ALPHA" in stored


async def test_abstain_asks_with_the_unknown_prompt_and_skips_unknown_answers(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: UNKNOWN", "node-b": "Answer: BETA", "node-c": "Answer: GAMMA"}
    record, harness = await run_one(owner_cfg(cfg, "abstain"), tmp_path, replies)

    assert record["final_answer"] == "BETA"
    info = record["owner_answer"]
    assert isinstance(info, dict)
    assert (info["n_unknown"], info["chosen_source"], info["fell_back_to_max_score"]) == (
        1,
        "b",
        False,
    )
    assert all("write UNKNOWN as the answer" in p[0] for p in harness.node_prompts.values())


async def test_abstain_with_only_unknown_answers_falls_back_to_the_best_score(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = dict.fromkeys(("node-a", "node-b", "node-c"), "Answer: UNKNOWN")
    record, _ = await run_one(owner_cfg(cfg, "abstain"), tmp_path, replies)
    info = record["owner_answer"]
    assert isinstance(info, dict)
    assert (info["n_unknown"], info["chosen_source"], info["fell_back_to_max_score"]) == (
        3,
        "a",
        True,
    )


async def test_the_other_rules_do_not_allow_unknown_in_the_prompt(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: ALPHA", "node-b": "Answer: BETA"}
    _, harness = await run_one(owner_cfg(cfg, "agree", 2), tmp_path, replies)
    assert all("write UNKNOWN" not in p[0] for p in harness.node_prompts.values())


async def test_agree_takes_the_answer_two_inboxes_share_even_if_the_best_inbox_differs(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: ALPHA", "node-b": "Answer: beta!", "node-c": "Answer: BETA"}
    record, _ = await run_one(owner_cfg(cfg, "agree"), tmp_path, replies)
    info = record["owner_answer"]
    assert isinstance(info, dict)
    assert record["final_answer"] == "beta!"  # b・c が一致し，その中で b の得点が高い
    assert info["vote_groups"] == [[1, 2], [0]]


async def test_agree_tie_is_decided_by_the_retrieval_score(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: ALPHA", "node-b": "Answer: BETA"}
    record, _ = await run_one(owner_cfg(cfg, "agree", 2), tmp_path, replies)
    assert record["final_answer"] == "ALPHA"


async def test_llm_select_lets_the_requester_pick_one_candidate_and_counts_the_extra_generation(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: ALPHA", "node-b": "Answer: BETA"}
    record, harness = await run_one(
        owner_cfg(cfg, "llm_select", 2), tmp_path, replies, requester_reply="2"
    )
    info = record["owner_answer"]
    assert isinstance(info, dict)
    assert record["final_answer"] == "BETA"
    assert (info["n_requests"], info["n_generations"]) == (2, 3)
    # 質問者の LLM には，候補の回答だけを渡す
    assert len(harness.requester_prompts) == 1 and "Answer 2: BETA" in harness.requester_prompts[0]


async def test_llm_select_falls_back_to_max_score_when_the_choice_is_unreadable(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: ALPHA", "node-b": "Answer: BETA"}
    record, _ = await run_one(
        owner_cfg(cfg, "llm_select", 2), tmp_path, replies, requester_reply="I cannot decide"
    )
    info = record["owner_answer"]
    assert isinstance(info, dict)
    assert record["final_answer"] == "ALPHA" and info["select_parse_failed"] is True


async def test_cpu_node_delegates_the_answer_to_its_gpu_node_with_the_unknown_prompt(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    harness = Harness(
        owner_cfg(cfg, "abstain", 2),
        tmp_path,
        {"node-a": "Answer: CPU-MODEL-MUST-NOT-ANSWER", "node-b": "Answer: GPU-ANSWER"},
    )
    requester = harness.requester()
    await harness.discover(requester)
    requester.placement = requester.placement.model_copy(
        update={"delegate_of": {"node-a": "node-b"}}
    )

    record = await requester.process(QUESTION)

    assert record["error"] is None
    by_source = {a["source"]: a for a in record["node_answers"]}  # type: ignore[attr-defined]
    assert by_source["a"]["delegated_to"] == "node-b"
    assert by_source["b"]["delegated_to"] is None
    # 受信箱 a の上位 5 通（このデータでは 1 通）が外に出る．答えたのは GPU のノードだけ
    assert record["docs_exposed_by_source"] == {"a": 1}
    assert harness.node_prompts["node-a"] == []
    assert len(harness.node_prompts["node-b"]) == 2
    assert all("write UNKNOWN as the answer" in p for p in harness.node_prompts["node-b"])
    assert record["query_recipients"] == 2


def test_owner_answer_requires_enronqa(cfg: AtriumConfig) -> None:
    changed = {**cfg.experiment.model_dump(), "dataset": "medrag", "answer_mode": "owner_answer"}
    with pytest.raises(ValueError, match="owner_answer requires dataset=enronqa"):
        ExperimentConfig.model_validate(changed)


def test_default_answer_settings_keep_the_existing_behavior(cfg: AtriumConfig) -> None:
    assert (cfg.answer.owner_m, cfg.answer.aggregate) == (2, "max_score")
    assert cfg.answer.save_candidates is False


# ── answer.save_candidates（p0007 A5）：全候補の回答と選び方の特徴量 ──────────────────────


async def test_save_candidates_keeps_every_candidate_answer_with_its_features(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: UNKNOWN", "node-b": "Answer: BETA", "node-c": "Answer: GAMMA"}
    harness = Harness(
        owner_cfg(cfg, "abstain", save_candidates=True),
        tmp_path,
        replies,
        logprobs={"node-a": [-0.1, -0.3], "node-b": [-1.0, -2.0, -3.0], "node-c": [-0.5]},
    )
    requester = harness.requester()
    await harness.discover(requester)

    record = await requester.process(QUESTION)

    info = record["owner_answer"]
    assert isinstance(info, dict)
    cands = info["candidates"]
    assert [c["source"] for c in cands] == ["a", "b", "c"]
    assert [c["answer"] for c in cands] == ["UNKNOWN", "BETA", "GAMMA"]
    assert [c["unknown"] for c in cands] == [True, False, False]
    assert [c["avg_logprob"] for c in cands] == pytest.approx([-0.2, -2.0, -0.5])
    assert [c["n_logprob_tokens"] for c in cands] == [2, 3, 1]
    # 文脈の文書 ID（本文ではない）と，密ベクトルの最高得点（実機の abstain が使う得点）
    assert cands[0]["context_doc_ids"] == ["a-00/f/0"]
    assert [c["top_score"] for c in cands] == [a["top_score"] for a in record["node_answers"]]  # type: ignore[index]
    # fusion=none の受信箱は BM25・RRF の得点を持たず，AllRouter は probe を送らない
    assert all(c["bm25_max"] is None and c["rrf_max"] is None for c in cands)
    assert all(c["probe_fused"] is None for c in cands)
    # 統合の結果は save_candidates に依らない
    assert info["chosen_source"] == "b"


async def test_save_candidates_reads_the_log_probability_from_the_delegate_gpu_node(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    harness = Harness(
        owner_cfg(cfg, "abstain", 2, save_candidates=True),
        tmp_path,
        {"node-a": "Answer: CPU", "node-b": "Answer: GPU"},
        logprobs={"node-a": [-9.0], "node-b": [-0.25, -0.75]},
    )
    requester = harness.requester()
    await harness.discover(requester)
    requester.placement = requester.placement.model_copy(
        update={"delegate_of": {"node-a": "node-b"}}
    )

    record = await requester.process(QUESTION)

    cands = record["owner_answer"]["candidates"]  # type: ignore[index]
    # 受信箱 a の回答は委託先（node-b の GPU）が作るので，その対数確率になる
    assert [c["avg_logprob"] for c in cands] == pytest.approx([-0.5, -0.5])
    assert harness.node_prompts["node-a"] == []


async def test_max_score_with_save_candidates_keeps_every_candidate_under_the_normal_prompt(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    """p0007 A6 の構成：通常のプロンプト（UNKNOWN を許さない）で全候補に答えさせ，選び方はオフラインの規則に任せる．"""
    replies = {"node-a": "Answer: ALPHA", "node-b": "Answer: BETA", "node-c": "Answer: GAMMA"}
    record, harness = await run_one(
        owner_cfg(cfg, "max_score", save_candidates=True), tmp_path, replies
    )
    info = record["owner_answer"]
    assert isinstance(info, dict)
    assert [c["answer"] for c in info["candidates"]] == ["ALPHA", "BETA", "GAMMA"]
    assert all("write UNKNOWN" not in p for ps in harness.node_prompts.values() for p in ps)
    # 実機で採るのは検索の最高得点の候補（オフラインの R0′ と同じ）
    best = max(range(3), key=lambda i: (info["candidates"][i]["top_score"], -i))
    assert info["chosen_index"] == best


async def test_without_save_candidates_nodes_are_not_asked_for_features(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    replies = {"node-a": "Answer: ALPHA", "node-b": "Answer: BETA"}
    record, harness = await run_one(owner_cfg(cfg, "abstain", 2), tmp_path, replies)
    assert "candidates" not in record["owner_answer"]  # type: ignore[operator]
    assert all('"logprobs"' not in p for prompts in harness.node_prompts.values() for p in prompts)


async def test_answer_response_without_features_is_unchanged(
    cfg: AtriumConfig, tmp_path: Path
) -> None:
    """with_features の無い要求の応答は，追加の欄（features・avg_logprob）を含まない（従来の応答と同じ）．"""
    harness = Harness(owner_cfg(cfg, "abstain", 2), tmp_path, {"node-a": "Answer: A"})
    harness.requester()
    app = harness._apps["node-a"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://n") as c:
        answer = await c.post(
            "/v1/answer",
            json={
                "dataset": "enronqa",
                "question": "q?",
                "options": {},
                "shard_ids": ["a-00"],
                "k": 5,
                "embedding": [1.0, 0.0, 0.0, 0.0],
            },
        )
        generated = await c.post(
            "/v1/generate",
            json={"dataset": "enronqa", "question": "q?", "options": {}, "docs": []},
        )
    assert answer.status_code == 200 and "features" not in answer.json()
    assert generated.status_code == 200
    assert "avg_logprob" not in generated.json() and "n_logprob_tokens" not in generated.json()
