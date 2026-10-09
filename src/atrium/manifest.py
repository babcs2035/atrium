"""データ源・シャード・配置の共有データ構造と，それを決める純粋関数．

中継点が `manifest.json`（どのシャードに何が入っているか）を作り，deploy が `placement.json`
（どのホストがどのシャードを持つか）を作る．専門家ノードと質問者はこの 2 つだけを見て動く．
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

from atrium.arrays import F32Array

FP16_BYTES = 2
GIB = 1024**3
SHARD_SPEC_FILENAME = "shard.json"

ShardKind = Literal["faiss", "search_results"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChunkFile(_Model):
    """シャードを構成する断片ファイル（MedRAG の chunk/*.jsonl 1 個）．"""

    name: str
    n_docs: int


class Advert(_Model):
    """データ源が見つけてもらうために事前に公開する情報（p0004 §6.1 の RQ-A）．EnronQA のシャードだけが持つ．

    terms は受信箱の中のメールで多く使われる語と，その語を含むメールの割合（語のスケッチ）．
    centroids は k-means の中心．card_embedding は Agent Card の説明文の埋め込み．
    いずれも受信箱の持ち主が自分のデータだけから計算でき，他の受信箱の統計は使わない．
    """

    terms: list[str]
    term_weights: list[float]
    centroids: list[list[float]]
    card_embedding: list[float]


class ShardSpec(_Model):
    """1 個のシャード．専門家ノードはこの単位で索引を持つ．

    kind="faiss" は MedRAG のように埋め込みと FAISS で検索するもの，
    kind="search_results" は FeB4RAG のように配布済みの検索結果を返すもの．
    """

    shard_id: str
    source: str
    kind: ShardKind
    files: list[ChunkFile]
    n_docs: int
    dim: int
    encoder: str
    centroid: list[float]
    description: str
    advert: Advert | None = None


class Manifest(_Model):
    """データセット 1 個分のシャードの一覧．"""

    dataset: str
    sources: list[str]
    shards: list[ShardSpec]

    def shards_of(self, source: str) -> list[ShardSpec]:
        """データ源に属するシャードを返す．"""
        return [s for s in self.shards if s.source == source]


class NodeAssignment(_Model):
    """専門家ノード 1 台に割り当てたシャード．"""

    host: str
    url: str
    shard_ids: list[str]


class Placement(_Model):
    """データセット 1 個分の配置．"""

    dataset: str
    nodes: list[NodeAssignment]
    # B4（delegate_gpu．p0004）：GPU の無い専門家のホスト → 回答を委託する GPU の専門家のホスト
    delegate_of: dict[str, str] = {}

    def url_of_shard(self) -> dict[str, str]:
        """シャード ID からそのシャードを持つノードの URL を引く表を返す．"""
        return {sid: node.url for node in self.nodes for sid in node.shard_ids}


def group_files_into_shards(
    files: Sequence[ChunkFile], dim: int, budget_bytes: float
) -> list[list[ChunkFile]]:
    """断片ファイルを名前順のまま連続した塊に分け，各塊の fp16 埋め込みが予算に収まるようにする．

    1 ファイルが単独で予算を超える場合は，そのファイルだけで 1 シャードにする（ファイルは分割しない．
    MedRAG の埋め込みはファイル単位で計算・保存するため）．
    """
    groups: list[list[ChunkFile]] = []
    current: list[ChunkFile] = []
    current_bytes = 0
    for f in files:
        size = f.n_docs * dim * FP16_BYTES
        if current and current_bytes + size > budget_bytes:
            groups.append(current)
            current, current_bytes = [], 0
        current.append(f)
        current_bytes += size
    if current:
        groups.append(current)
    return groups


def shard_id_of(source: str, index: int) -> str:
    """シャード ID を作る（例: pubmed-03）．"""
    return f"{source}-{index:02d}"


def combine_centroids(n_docs: Sequence[int], centroids: Sequence[Sequence[float]]) -> F32Array:
    """シャードの重心を文書数で重み付けして平均し，データ源全体の重心を求める．

    全文書の埋め込みの平均と一致する（重心は各シャードの文書の平均であるため）．
    """
    weights = np.array(n_docs, dtype=np.float64)
    vectors = np.array(centroids, dtype=np.float64)
    if weights.sum() <= 0:
        raise ValueError("cannot combine centroids of empty shards")
    combined: F32Array = (vectors * weights[:, None]).sum(axis=0) / weights.sum()
    return combined.astype(np.float32)


def plan_placement(manifest: Manifest, hosts: Sequence[str], port: int) -> Placement:
    """シャードを専門家ホストへ先頭から 1 個ずつ割り当てる．ホストが足りなければ巡回して重ねる．"""
    if not hosts:
        raise ValueError("no expert hosts are configured")
    assigned: dict[str, list[str]] = {}
    for i, shard in enumerate(manifest.shards):
        host = hosts[i % len(hosts)]
        assigned.setdefault(host, []).append(shard.shard_id)
    nodes = [
        NodeAssignment(host=host, url=f"http://{host}:{port}", shard_ids=shard_ids)
        for host, shard_ids in assigned.items()
    ]
    return Placement(dataset=manifest.dataset, nodes=nodes)


def plan_placement_balanced(
    manifest: Manifest, hosts: Sequence[str], port: int, seed: int, gpu_hosts: Sequence[str]
) -> Placement:
    """シャードを全ホストに同じ数ずつ，文書数がおおよそ均等になるよう配る（EnronQA の 150 受信箱．p0004 §8.1）．

    種で並べ替えたシャードを，文書数の多い順に，割り当て数が上限（シャード数 ÷ ホスト数の切り上げ）に
    達していないホストのうち文書数の合計が最小のものへ割り当てる（LPT）．種で並べ替えるので，
    GPU のホストに置かれる受信箱は無作為になる．
    GPU の無いホストは，config.yaml の順に GPU のホストへ同じ数ずつ対応づける（B4 の委託先）．
    """
    if not hosts:
        raise ValueError("no expert hosts are configured")
    rng = np.random.default_rng(seed)
    shuffled = [manifest.shards[int(i)] for i in rng.permutation(len(manifest.shards))]
    ordered = sorted(shuffled, key=lambda s: -s.n_docs)
    cap = -(-len(ordered) // len(hosts))
    assigned: dict[str, list[str]] = {h: [] for h in hosts}
    load = dict.fromkeys(hosts, 0)
    for shard in ordered:
        host = min(
            (h for h in hosts if len(assigned[h]) < cap), key=lambda h: (load[h], hosts.index(h))
        )
        assigned[host].append(shard.shard_id)
        load[host] += shard.n_docs
    gpus = [h for h in hosts if h in set(gpu_hosts)]
    cpus = [h for h in hosts if h not in set(gpu_hosts)]
    delegate_of: dict[str, str] = {}
    if gpus:
        per_gpu = -(-len(cpus) // len(gpus))
        delegate_of = {cpu: gpus[i // per_gpu] for i, cpu in enumerate(cpus)}
    nodes = [
        NodeAssignment(host=h, url=f"http://{h}:{port}", shard_ids=sorted(assigned[h]))
        for h in hosts
        if assigned[h]
    ]
    return Placement(dataset=manifest.dataset, nodes=nodes, delegate_of=delegate_of)


def read_manifest(path: Path) -> Manifest:
    """manifest.json を読む．"""
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))


def read_placement(path: Path) -> Placement:
    """placement.json を読む．"""
    return Placement.model_validate_json(path.read_text(encoding="utf-8"))


def write_json_model(path: Path, model: BaseModel) -> None:
    """pydantic モデルを整形した JSON で書く（途中で落ちても壊れたファイルを残さない）．"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(model.model_dump(mode="json"), indent=1), encoding="utf-8")
    tmp.replace(path)
