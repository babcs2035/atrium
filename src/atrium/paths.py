"""データディレクトリの構成（中継点・質問者・操作端末で共通）．

    <data_dir>/<dataset>/
      benchmark/questions.jsonl      質問（atrium.benchmarks の書式）
      corpus/<source>/chunk/*.jsonl  断片の本文（medrag のみ）
      corpus/<source>/emb/*.f16.npy  断片の埋め込み（medrag のみ）
      shards/<shard_id>/             専門家へ配るシャード（atrium.store の構成．medrag は corpus への symlink）
      manifest.json                  シャードの一覧
      queries/<encoder>.npy          全質問のクエリ埋め込み（行は query_ids.json の順）
      queries/query_ids.json
      labels/labels.json             質問 ID → 関連ありのデータ源
      labels/split.json              ルーター学習の train / val / test の質問 ID
      router/                        学習済みルーター（atrium.routing.ragroute）
      qrels/BEIR-QRELS-RM.txt        FeB4RAG の結果統合用ラベル（merge=qrels_oracle で使う）

質問者へは shards/ と corpus/ 以外を，操作端末へは benchmark/ と labels/ を配る．
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def safe_name(encoder: str) -> str:
    """検索器名をファイル名に使える形にする（"ncbi/MedCPT-Query-Encoder" → "ncbi__MedCPT-Query-Encoder"）．"""
    return encoder.replace("/", "__")


@dataclass(frozen=True)
class DatasetPaths:
    """データセット 1 個分のパス．"""

    root: Path

    @property
    def questions(self) -> Path:
        """質問ファイル．"""
        return self.root / "benchmark" / "questions.jsonl"

    def corpus(self, source: str) -> Path:
        """データ源のコーパスのディレクトリ．"""
        return self.root / "corpus" / source

    @property
    def shards(self) -> Path:
        """シャードのディレクトリの親．"""
        return self.root / "shards"

    @property
    def manifest(self) -> Path:
        """manifest.json．"""
        return self.root / "manifest.json"

    @property
    def queries(self) -> Path:
        """クエリ埋め込みのディレクトリ．"""
        return self.root / "queries"

    def query_embeddings(self, encoder: str) -> Path:
        """検索器ごとのクエリ埋め込み．"""
        return self.queries / f"{safe_name(encoder)}.npy"

    @property
    def query_ids(self) -> Path:
        """クエリ埋め込みの行と質問 ID の対応．"""
        return self.queries / "query_ids.json"

    @property
    def labels(self) -> Path:
        """関連ラベル．"""
        return self.root / "labels" / "labels.json"

    @property
    def split(self) -> Path:
        """ルーター学習の分割．"""
        return self.root / "labels" / "split.json"

    @property
    def router(self) -> Path:
        """学習済みルーターのディレクトリ．"""
        return self.root / "router"

    @property
    def rm_qrels(self) -> Path:
        """FeB4RAG の結果統合用ラベル．"""
        return self.root / "qrels" / "BEIR-QRELS-RM.txt"


def dataset_paths(data_dir: Path, dataset: str) -> DatasetPaths:
    """データセットのパスを返す．"""
    return DatasetPaths(data_dir / dataset)
