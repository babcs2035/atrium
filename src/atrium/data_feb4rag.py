"""FeB4RAG の準備（データ中継点で実行する）．

各段は冪等であり，成果物があれば飛ばす．

    fetch    FeB4RAG のリポジトリ（コミット固定）から dataset/ を取得し，質問・ラベル・qrels を作る
    beir     各エンジンの BEIR コーパス（corpus.jsonl）を取得する
    shards   エンジンごとに「配布の検索結果＋そこに現れる文書の本文」のシャードを作る
    queries  エンジンの検索器ごとに全質問のクエリ埋め込みを計算する

関連ラベルは resource selection 用の qrels（BEIR-QRELS-RS.txt）でスコアが 0 より大きいエンジンとする
（16 エンジンで集計すると要求あたり平均 11.94 個になり，研究計画書 §7.2 の 11.93 個とほぼ一致する）．
重心はコーパス全体ではなく無作為抽出した文書の平均で近似する（全文書の埋め込みは数週間規模のため）．
"""

from __future__ import annotations

import csv
import json
import logging
import random
import shutil
import tarfile
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np

from atrium.benchmarks import feb4rag_to_questions, load_questions, write_questions
from atrium.config import AtriumConfig
from atrium.download import download
from atrium.manifest import SHARD_SPEC_FILENAME, Manifest, ShardSpec, write_json_model
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

# シャードに残す検索結果の件数（k_ret は既定 50．100 まで振れるように余裕を持たせる）
RESULTS_KEEP = 100
REPO_DIRNAME = "repo"


def _repo_dataset(paths: DatasetPaths) -> Path:
    return paths.root / REPO_DIRNAME / "dataset"


def fetch_repo(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """FeB4RAG の dataset/ を取得し，質問・関連ラベル・結果統合用 qrels を作る．"""
    dataset_dir = _repo_dataset(paths)
    if not dataset_dir.exists():
        commit = cfg.data.feb4rag.feb4rag_commit
        tarball = paths.root / "repo.tar.gz"
        download(f"https://codeload.github.com/ielab/FeB4RAG/tar.gz/{commit}", tarball)
        # 展開の途中で止まっても不完全な dataset/ が残らないよう，一時ディレクトリへ展開してから移す
        staging = paths.root / f"{REPO_DIRNAME}.tmp"
        shutil.rmtree(staging, ignore_errors=True)
        prefix = f"FeB4RAG-{commit}/dataset/"
        with tarfile.open(tarball, mode="r:gz") as tar:
            members = [m for m in tar.getmembers() if m.name.startswith(prefix)]
            for m in members:
                m.name = "dataset/" + m.name[len(prefix) :]
            tar.extractall(staging, members=members, filter="data")
        staging.replace(paths.root / REPO_DIRNAME)
        # dataset/ だけを使う．tarball はこの段で作った一時ファイルなので消してよい
        tarball.unlink()

    if not paths.questions.exists():
        lines = (
            (dataset_dir / "queries" / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        )
        write_questions(paths.questions, feb4rag_to_questions(lines))

    sources = set(cfg.data.feb4rag.sources)
    labels: dict[str, list[str]] = defaultdict(list)
    with (dataset_dir / "qrels" / "BEIR-QRELS-RS.txt").open(encoding="utf-8") as f:
        for line in f:
            qid, _, engine, score = line.split()
            if engine in sources and int(score) > 0:
                labels[f"feb4rag/{qid}"].append(engine)
    paths.labels.parent.mkdir(parents=True, exist_ok=True)
    paths.labels.write_text(json.dumps({q: sorted(v) for q, v in labels.items()}), encoding="utf-8")

    paths.rm_qrels.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(dataset_dir / "qrels" / "BEIR-QRELS-RM.txt", paths.rm_qrels)


def read_engines(paths: DatasetPaths) -> dict[str, tuple[str, str]]:
    """engines.csv から，エンジン名 → (検索器名, 説明文) を読む．"""
    out: dict[str, tuple[str, str]] = {}
    with (_repo_dataset(paths) / "engines" / "engines.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["name"]] = (row["model"], row["Description"].strip())
    return out


def _beir_corpus(paths: DatasetPaths, source: str) -> Path:
    return paths.root / "beir" / source / "corpus.jsonl"


def fetch_beir(cfg: AtriumConfig, paths: DatasetPaths, source: str) -> None:
    """BEIR のコーパス（corpus.jsonl だけ）を取得する．"""
    target = _beir_corpus(paths, source)
    if target.exists():
        return
    zip_path = target.parent / f"{source}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    download(cfg.data.feb4rag.beir_url.format(name=source), zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        member = next(n for n in zf.namelist() if n.endswith("corpus.jsonl"))
        tmp = target.with_suffix(".jsonl.part")
        with zf.open(member) as src, tmp.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        tmp.replace(target)
    # 展開後は corpus.jsonl だけを使う．zip はこの段で作った一時ファイルなので消してよい
    zip_path.unlink()


def _read_search_results(paths: DatasetPaths, source: str) -> dict[str, list[tuple[str, float]]]:
    files = sorted((_repo_dataset(paths) / "search_results" / source).glob("sch_*.txt"))
    if len(files) != 1:
        raise FileNotFoundError(f"expected one search result file for {source}, got {files}")
    hits: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
    with files[0].open(encoding="utf-8") as f:
        for line in f:
            qid, _, doc_id, rank, score, _ = line.split()
            hits[qid].append((doc_id, int(rank), float(score)))
    return {
        qid: [(d, s) for d, _, s in sorted(rows, key=lambda r: r[1])[:RESULTS_KEEP]]
        for qid, rows in hits.items()
    }


def _sample_and_collect(
    corpus: Path, needed: set[str], n_sample: int, seed: str
) -> tuple[dict[str, dict[str, str]], list[tuple[str, str]], int]:
    """コーパスを 1 回走査し，必要な文書の本文・重心用の無作為抽出（reservoir sampling）・文書数を得る．"""
    rng = random.Random(seed)
    kept: dict[str, dict[str, str]] = {}
    sample: list[tuple[str, str]] = []
    n_docs = 0
    with corpus.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            n_docs += 1
            row = json.loads(line)
            doc = (str(row.get("title") or ""), str(row.get("text") or ""))
            if str(row["_id"]) in needed:
                kept[str(row["_id"])] = {"_id": str(row["_id"]), "title": doc[0], "text": doc[1]}
            if len(sample) < n_sample:
                sample.append(doc)
            else:
                j = rng.randint(0, i)
                if j < n_sample:
                    sample[j] = doc
    return kept, sample, n_docs


def build_shards(cfg: AtriumConfig, paths: DatasetPaths) -> Manifest:
    """エンジンごとにシャードを作り，manifest.json を書く（1 エンジン = 1 シャード）．"""
    from atrium.encoders import HfEncoder

    engines = read_engines(paths)
    f = cfg.data.feb4rag
    shards: list[ShardSpec] = []
    for source in f.sources:
        shard_dir = paths.shards / source
        spec_path = shard_dir / SHARD_SPEC_FILENAME
        if spec_path.exists():
            shards.append(ShardSpec.model_validate_json(spec_path.read_text(encoding="utf-8")))
            continue
        encoder_name, description = engines[source]
        hits = _read_search_results(paths, source)
        needed = {d for rows in hits.values() for d, _ in rows}
        n_sample = f.centroid_sample_overrides.get(source, f.centroid_sample)
        docs, sample, n_docs = _sample_and_collect(
            _beir_corpus(paths, source), needed, n_sample, f"{source}:{cfg.experiment.seed}"
        )
        emb = HfEncoder(encoder_name).encode_docs(sample)
        shard_dir.mkdir(parents=True, exist_ok=True)
        with (shard_dir / "results.jsonl").open("w", encoding="utf-8") as out:
            for qid, rows in hits.items():
                out.write(json.dumps({"qid": qid, "hits": rows}) + "\n")
        with (shard_dir / "docs.jsonl").open("w", encoding="utf-8") as out:
            for doc in docs.values():
                out.write(json.dumps(doc, ensure_ascii=False) + "\n")
        spec = ShardSpec(
            shard_id=source,
            source=source,
            kind="search_results",
            files=[],
            n_docs=n_docs,
            dim=int(emb.shape[1]),
            encoder=encoder_name,
            centroid=emb.astype(np.float64).mean(axis=0).astype(np.float32).tolist(),
            description=description,
        )
        write_json_model(spec_path, spec)
        shards.append(spec)
        logger.info("shard %s: %d queries, %d docs kept", source, len(hits), len(docs))
    manifest = Manifest(dataset="feb4rag", sources=list(f.sources), shards=shards)
    write_json_model(paths.manifest, manifest)
    return manifest


def embed_queries(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """エンジンの検索器ごとに全質問を埋め込む（質問者は cached モードでこれを使う）．"""
    from atrium.encoders import HfEncoder

    questions = load_questions(paths.questions)
    paths.queries.mkdir(parents=True, exist_ok=True)
    paths.query_ids.write_text(json.dumps([q.qid for q in questions]), encoding="utf-8")
    engines = read_engines(paths)
    for name in sorted({engines[s][0] for s in cfg.data.feb4rag.sources}):
        out = paths.query_embeddings(name)
        if out.exists():
            continue
        emb = HfEncoder(name).encode_queries([q.question for q in questions])
        np.save(out, emb.astype(np.float32))
        logger.info("query embeddings for %s: %s", name, emb.shape)
