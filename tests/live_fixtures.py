"""freshness-run・failure-run・join-run（p0007 I5〜I7）のテストが使う，小さな EnronQA 型のクラスタのビルダー（ノードは ASGI アプリ）．

受信箱ごとに 1 シャード（シャード ID = 受信箱の名前）で，メールの本文から決まる単位ベクトルを埋め込みとする（HashEmbedder）．
質問 i の埋め込みは正解のメールの埋め込みと等しいので，そのメールが索引にあれば上位 1 位で見つかり，無ければ見つからない．
"""

from __future__ import annotations

import json
import shutil
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

from atrium.arrays import F32Array
from atrium.benchmarks import Question, write_questions
from atrium.config import AtriumConfig
from atrium.manifest import (
    SHARD_SPEC_FILENAME,
    ChunkFile,
    NodeAssignment,
    Placement,
    ShardSpec,
    write_json_model,
)
from atrium.node import create_app
from atrium.paths import DatasetPaths, dataset_paths
from atrium.requester import Requester, discover_shards
from atrium.routing import AllRouter, SourceProfile, build_source_profiles
from atrium.store import load_shard
from tests.conftest import HostDispatchTransport, ollama_mock, with_experiment

DIM = 8
ENCODER = "test-encoder"
INBOXES = ("alice", "bob", "carol", "dave")
DOCS_PER_INBOX = 10


class HashEmbedder:
    """本文から決まる単位ベクトルを返す埋め込み（ノードの DocEmbedder・質問者の中央の埋め込みの代わり）．"""

    name = "hash-embedder"

    def encode_texts(self, texts: Sequence[str]) -> F32Array:
        """本文ごとの埋め込み（同じ本文は同じベクトル）．"""
        rows = [
            np.random.default_rng(zlib.crc32(t.encode("utf-8"))).normal(size=DIM) for t in texts
        ]
        emb = np.asarray(rows, dtype=np.float32)
        out: F32Array = emb / np.linalg.norm(emb, axis=1, keepdims=True)
        return out


class StubEmbedder:
    """質問 ID → 固定のクエリ埋め込み（事前計算した埋め込みの代わり）．"""

    def __init__(self, vectors: dict[str, F32Array]) -> None:
        """質問 ID → 埋め込み．"""
        self._vectors = vectors

    def embed(self, question: Question) -> dict[str, F32Array]:
        """検索器名 → 埋め込み．"""
        return {ENCODER: self._vectors[question.qid]}


@dataclass
class MiniCluster:
    """テスト用のクラスタ（データ・ノード・質問者）．"""

    cfg: AtriumConfig
    paths: DatasetPaths
    placement: Placement
    client: httpx.AsyncClient
    questions: list[Question]
    gold: dict[str, dict[str, str]]
    sources: list[SourceProfile]
    requester: Requester
    embedder: HashEmbedder
    # 受信箱 → そのメールの文書 ID（行の順）
    doc_ids: dict[str, list[str]]
    inboxes: tuple[str, ...]


def enron_cfg(cfg: AtriumConfig, inboxes: Sequence[str], **cluster_changes: object) -> AtriumConfig:
    """EnronQA・retrieval_only・管理 API 有効の設定（受信箱は inboxes，k_context は 5）．"""
    enron = cfg.data.require_enronqa()
    data = cfg.data.model_copy(
        update={
            "enronqa": enron.model_copy(
                update={"sources": list(inboxes), "k_context": 5, "encoder": ENCODER}
            )
        }
    )
    changed = with_experiment(cfg, dataset="enronqa", routing="all", answer_mode="retrieval_only")
    cluster = changed.cluster.model_copy(update={"node_admin": True, **cluster_changes})
    return changed.model_copy(update={"data": data, "cluster": cluster})


def write_inbox(
    root: Path, paths: DatasetPaths, inbox: str, embedder: HashEmbedder, n_docs: int
) -> list[str]:
    """受信箱のコーパス（chunk と埋め込み fp16）と，同じ内容のノードのシャードを書く．文書 ID を返す．"""
    doc_ids = [f"{inbox}/inbox/{i}." for i in range(n_docs)]
    contents = [
        f"mail {i} of {inbox} about topic {i * 7 % 5} item{inbox}{i}" for i in range(n_docs)
    ]
    shard_dir = root / "shards" / inbox
    (shard_dir / "chunk").mkdir(parents=True)
    (shard_dir / "emb").mkdir()
    emb = embedder.encode_texts(contents).astype(np.float16)
    np.save(shard_dir / "emb" / f"{inbox}.f16.npy", emb)
    with (shard_dir / "chunk" / f"{inbox}.jsonl").open("w", encoding="utf-8") as f:
        for doc_id, content in zip(doc_ids, contents, strict=True):
            f.write(json.dumps({"id": doc_id, "title": f"t {doc_id}", "content": content}) + "\n")
    spec = ShardSpec(
        shard_id=inbox,
        source=inbox,
        kind="faiss",
        files=[ChunkFile(name=inbox, n_docs=n_docs)],
        n_docs=n_docs,
        dim=DIM,
        encoder=ENCODER,
        centroid=emb.astype(np.float32).mean(axis=0).tolist(),
        description=inbox,
    )
    write_json_model(shard_dir / SHARD_SPEC_FILENAME, spec)
    corpus = paths.corpus(inbox)
    corpus.mkdir(parents=True)
    shutil.copytree(shard_dir / "chunk", corpus / "chunk")
    shutil.copytree(shard_dir / "emb", corpus / "emb")
    return doc_ids


async def build_cluster(
    cfg: AtriumConfig,
    root: Path,
    inboxes: Sequence[str] = INBOXES,
    n_nodes: int = 2,
    docs_per_inbox: int = DOCS_PER_INBOX,
    tolerate_node_failures: bool = False,
) -> MiniCluster:
    """受信箱を n_nodes 台のノードへ均等に配り，質問（メールごとに 1 問．正解はそのメール）と質問者を作る．"""
    cfg = enron_cfg(cfg, inboxes)
    paths = dataset_paths(root, "enronqa")
    embedder = HashEmbedder()
    doc_ids = {
        inbox: write_inbox(root, paths, inbox, embedder, docs_per_inbox) for inbox in inboxes
    }
    questions: list[Question] = []
    gold: dict[str, dict[str, str]] = {}
    vectors: dict[str, F32Array] = {}
    contents = {
        d: json.loads(line)["content"]
        for inbox in inboxes
        for d, line in zip(
            doc_ids[inbox],
            (root / "shards" / inbox / "chunk" / f"{inbox}.jsonl").read_text("utf-8").splitlines(),
            strict=True,
        )
    }
    for inbox in inboxes:
        for i, doc_id in enumerate(doc_ids[inbox]):
            qid = f"{inbox}/{i}"
            questions.append(Question(qid, inbox, f"question about {doc_id}", {}, "answer"))
            gold[qid] = {"qid": qid, "inbox": inbox, "path": doc_id}
            vectors[qid] = embedder.encode_texts([contents[doc_id]])[0]
    write_questions(paths.questions, questions)
    (paths.questions.parent / "gold.jsonl").write_text(
        "".join(json.dumps(g) + "\n" for g in gold.values()), encoding="utf-8"
    )
    per_node = len(inboxes) // n_nodes
    placement = Placement(
        dataset="enronqa",
        nodes=[
            NodeAssignment(
                host=f"node-{n}",
                url=f"http://node-{n}",
                shard_ids=list(inboxes[n * per_node : (n + 1) * per_node]),
            )
            for n in range(n_nodes)
        ],
    )
    ollama = ollama_mock(lambda _: "answer")
    routes: dict[str, httpx.AsyncBaseTransport] = {"ollama": ollama}
    for node in placement.nodes:
        app = create_app(
            node.host,
            {
                sid: load_shard(root / "shards" / sid, cfg.retrieval.fusion)
                for sid in node.shard_ids
            },
            cfg,
            "http://ollama",
            httpx.AsyncClient(transport=ollama),
            embedder=embedder,
        )
        routes[node.host] = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=HostDispatchTransport(routes))
    sources = build_source_profiles(await discover_shards(client, placement), list(inboxes))
    requester = Requester(
        cfg=cfg,
        placement=placement,
        sources=sources,
        router=AllRouter(),
        embedder=StubEmbedder(vectors),
        client=client,
        ollama_url="http://ollama",
        tolerate_node_failures=tolerate_node_failures,
    )
    return MiniCluster(
        cfg=cfg,
        paths=paths,
        placement=placement,
        client=client,
        questions=questions,
        gold=gold,
        sources=sources,
        requester=requester,
        embedder=embedder,
        doc_ids=doc_ids,
        inboxes=tuple(inboxes),
    )
