"""MIRAGE ＋ MedRAG コーパスの準備（データ中継点で実行する）．

各段は冪等であり，成果物があれば飛ばす（数日かかる埋め込みを途中から再開できるようにするため）．

    benchmark  MIRAGE の質問を取得して questions.jsonl にする
    corpus     MedRAG の断片を取得する（StatPearls は NCBI から取得して MedRAG のスクリプトで断片化する）
    embed      断片を MedCPT-Article-Encoder で埋め込み，fp16 で保存する
    shards     断片ファイルを約 shard_budget_gb ごとに束ね，manifest.json を作る
    queries    全質問のクエリ埋め込みを計算する

MedRAG の配布していた事前計算済みの埋め込みは 2026-10-05 時点で取得できない（403）ため，
MedRAG の embed() と同じ手順で全て再計算する．
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

import numpy as np

from atrium.arrays import F32Array
from atrium.benchmarks import load_questions, mirage_to_questions, write_questions
from atrium.config import AtriumConfig
from atrium.manifest import (
    GIB,
    SHARD_SPEC_FILENAME,
    ChunkFile,
    Manifest,
    ShardSpec,
    group_files_into_shards,
    shard_id_of,
    write_json_model,
)
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

MEDCPT_DIM = 768
STAMP = ".complete"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    logger.info("downloading %s", url)
    urllib.request.urlretrieve(url, tmp)  # noqa: S310 (URL は config.yaml の固定値)
    tmp.replace(dest)


def _read_jsonl_docs(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def fetch_benchmark(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """MIRAGE の benchmark.json を取得し，questions.jsonl に変換する．"""
    if paths.questions.exists():
        return
    raw_path = paths.root / "benchmark" / "MIRAGE.json"
    _download(cfg.data.medrag.mirage_url, raw_path)
    questions = mirage_to_questions(json.loads(raw_path.read_text(encoding="utf-8")))
    write_questions(paths.questions, questions)
    logger.info("wrote %d questions", len(questions))


def _fetch_statpearls(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    corpus = paths.corpus("statpearls")
    tarball = corpus / "statpearls_NBK430685.tar.gz"
    if not tarball.exists():
        _download(cfg.data.medrag.statpearls_url, tarball)
    with tarfile.open(tarball) as tar:
        tar.extractall(corpus, filter="data")
    # 断片化は MedRAG の src/data/statpearls.py（コミット固定）をそのまま使う．
    # スクリプトは "corpus/statpearls/..." を相対パスで読むので，データセットの root で実行する
    script = corpus / "statpearls_chunker.py"
    commit = cfg.data.medrag.medrag_commit
    _download(
        f"https://raw.githubusercontent.com/Teddy-XiongGZ/MedRAG/{commit}/src/data/statpearls.py",
        script,
    )
    subprocess.run([sys.executable, str(script.resolve())], cwd=paths.root, check=True)


def fetch_corpus(cfg: AtriumConfig, paths: DatasetPaths, source: str) -> None:
    """データ源 1 個の断片（chunk/*.jsonl）を取得する．"""
    corpus = paths.corpus(source)
    if (corpus / "chunk" / STAMP).exists():
        return
    if source == "statpearls":
        _fetch_statpearls(cfg, paths)
    else:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=f"MedRAG/{source}",
            repo_type="dataset",
            allow_patterns=["chunk/*"],
            local_dir=corpus,
        )
    (corpus / "chunk" / STAMP).touch()


def _has_content(path: Path) -> bool:
    # 最初の空でない行が見つかった時点で止める（PubMed は 1 ファイル数十 MB あるため全体を読まない）
    with path.open(encoding="utf-8") as f:
        return any(line.strip() for line in f)


def chunk_names(paths: DatasetPaths, source: str) -> list[str]:
    """データ源の断片ファイル名（拡張子なし）を名前順に返す．空のファイルは除く（MedRAG と同じ）．"""
    chunk_dir = paths.corpus(source) / "chunk"
    return sorted(p.stem for p in chunk_dir.glob("*.jsonl") if _has_content(p))


def embed_key(source: str, name: str) -> str:
    """埋め込みの分担表で断片ファイルを表すキー（"pubmed/pubmed23n0001"）．"""
    return f"{source}/{name}"


def balance_by_size(sizes: dict[str, int], workers: list[str]) -> dict[str, list[str]]:
    """大きいものから順に，その時点で負荷（合計サイズ）が最小の担当者へ割り当てる（LPT 法）．"""
    load = dict.fromkeys(workers, 0)
    assigned: dict[str, list[str]] = {w: [] for w in workers}
    for key in sorted(sizes, key=lambda k: (-sizes[k], k)):
        worker = min(workers, key=lambda w: (load[w], workers.index(w)))
        assigned[worker].append(key)
        load[worker] += sizes[key]
    return {w: sorted(keys) for w, keys in assigned.items()}


def plan_embedding(
    cfg: AtriumConfig, paths: DatasetPaths, workers: list[str]
) -> dict[str, list[str]]:
    """まだ埋め込んでいない断片ファイルを，ファイルのバイト数が均等になるよう担当者へ振り分ける．

    埋め込みの時間は断片の総トークン数にほぼ比例し，それはファイルのバイト数にほぼ比例する．
    """
    sizes: dict[str, int] = {}
    for source in cfg.data.medrag.sources:
        corpus = paths.corpus(source)
        for name in chunk_names(paths, source):
            if not (corpus / "emb" / f"{name}.f16.npy").exists():
                sizes[embed_key(source, name)] = (corpus / "chunk" / f"{name}.jsonl").stat().st_size
    return balance_by_size(sizes, workers)


def embed_corpus(
    cfg: AtriumConfig, paths: DatasetPaths, source: str, only: set[str] | None = None
) -> None:
    """断片ファイルごとに MedCPT-Article-Encoder で埋め込み，emb/<name>.f16.npy に保存する．

    only を与えると，その中にある embed_key の断片ファイルだけを埋め込む（複数の GPU で分担するとき）．
    """
    from atrium.encoders import MedcptEncoder

    corpus = paths.corpus(source)
    emb_dir = corpus / "emb"
    emb_dir.mkdir(parents=True, exist_ok=True)
    pending = [
        n
        for n in chunk_names(paths, source)
        if not (emb_dir / f"{n}.f16.npy").exists()
        and (only is None or embed_key(source, n) in only)
    ]
    if not pending:
        return
    m = cfg.data.medrag
    encoder = MedcptEncoder(m.query_encoder, m.article_encoder, batch_size=m.embed_batch_size)
    for i, name in enumerate(pending):
        docs = _read_jsonl_docs(corpus / "chunk" / f"{name}.jsonl")
        emb = encoder.encode_docs([(d["title"], d["content"]) for d in docs]).astype(np.float16)
        tmp = emb_dir / f"{name}.f16.tmp.npy"
        np.save(tmp, emb)
        tmp.replace(emb_dir / f"{name}.f16.npy")
        logger.info(
            "[%s] embedded %s (%d docs, %d/%d files)", source, name, len(docs), i + 1, len(pending)
        )


def _centroid_of_files(emb_dir: Path, names: list[str]) -> F32Array:
    total = np.zeros(MEDCPT_DIM, dtype=np.float64)
    count = 0
    for name in names:
        emb = np.load(emb_dir / f"{name}.f16.npy", mmap_mode="r")
        total += emb.astype(np.float64).sum(axis=0)
        count += emb.shape[0]
    centroid: F32Array = (total / count).astype(np.float32)
    return centroid


def _link(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(os.path.relpath(target, link.parent))


def build_shards(cfg: AtriumConfig, paths: DatasetPaths) -> Manifest:
    """断片ファイルを束ねてシャードを作り，manifest.json を書く．

    シャードのディレクトリには shard.json と，corpus/ の chunk・emb への相対 symlink を置く
    （配布時に tar -h で実体として送る．中継点の容量を二重に使わないため）．
    """
    budget = cfg.cluster.shard_budget_gb * GIB
    m = cfg.data.medrag
    shards: list[ShardSpec] = []
    for source in m.sources:
        corpus = paths.corpus(source)
        files = []
        for name in chunk_names(paths, source):
            emb = np.load(corpus / "emb" / f"{name}.f16.npy", mmap_mode="r")
            files.append(ChunkFile(name=name, n_docs=int(emb.shape[0])))
        for i, group in enumerate(group_files_into_shards(files, MEDCPT_DIM, budget)):
            spec = ShardSpec(
                shard_id=shard_id_of(source, i),
                source=source,
                kind="faiss",
                files=group,
                n_docs=sum(f.n_docs for f in group),
                dim=MEDCPT_DIM,
                encoder=m.query_encoder,
                centroid=_centroid_of_files(corpus / "emb", [f.name for f in group]).tolist(),
                description=m.descriptions[source],
            )
            shard_dir = paths.shards / spec.shard_id
            write_json_model(shard_dir / SHARD_SPEC_FILENAME, spec)
            for f in group:
                _link(corpus / "chunk" / f"{f.name}.jsonl", shard_dir / "chunk" / f"{f.name}.jsonl")
                _link(corpus / "emb" / f"{f.name}.f16.npy", shard_dir / "emb" / f"{f.name}.f16.npy")
            shards.append(spec)
            logger.info("shard %s: %d files, %d docs", spec.shard_id, len(group), spec.n_docs)
    manifest = Manifest(dataset="medrag", sources=list(m.sources), shards=shards)
    write_json_model(paths.manifest, manifest)
    return manifest


def embed_queries(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """全質問のクエリ埋め込みを計算する（ラベル計算とルーター学習で使う）．"""
    from atrium.encoders import MedcptEncoder

    m = cfg.data.medrag
    out = paths.query_embeddings(m.query_encoder)
    if out.exists():
        return
    questions = load_questions(paths.questions)
    encoder = MedcptEncoder(m.query_encoder, m.article_encoder, load_article=False)
    emb = encoder.encode_queries([q.question for q in questions])
    paths.queries.mkdir(parents=True, exist_ok=True)
    np.save(out, emb.astype(np.float32))
    paths.query_ids.write_text(json.dumps([q.qid for q in questions]), encoding="utf-8")
