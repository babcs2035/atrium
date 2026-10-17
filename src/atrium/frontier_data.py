"""frontier-eval の FeB4RAG・MedRAG の入力づくり（p0006 P3）．話題で分けたデータで，EnronQA と同じ枠組みの評価を行う．

- FeB4RAG：13 エンジン（1 エンジン = 1 デバイス）．2 段目の得点は，エンジンの配布済み検索結果の最高の得点（実機の probe と同じ）．
  エンジンごとに検索器が違うので得点の尺度が揃わない．`score_norm` で，なし・エンジンごとの z 値・順位（エンジンの得点の分布の中の割合）を選ぶ．
  統計は train の質問だけから作る．要約は，エンジンが返した文書の本文（title と text）を共通の符号化器（EnronQA と同じ arctic）で埋め込んだもの．
  関連ラベルは LLM 判定で，1 問あたり平均 11.9 エンジンが関連（m = 9 のとき再現率の上限は 9 / 11.9）．
- MedRAG：16 シャード（1 シャード = 1 デバイス）．2 段目の得点は，シャードの検索結果の最高の得点で，保存されていないので全シャードを
  総当たりで検索して求める（上位 15 件．GPU）．関連ラベルは「統合後の上位 15 件に 1 件以上を出したシャード」で，既存のデータ源のラベル
  （labels.json）と同じ規則で作り，データ源の集合が一致することを確かめる．要約は，各シャードから無作為に取った文書（MedCPT の埋め込みと本文）．
- dev は train と val の質問，test は test の質問（方式は学習しない）．
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from atrium.arrays import F32Array
from atrium.benchmarks import load_questions
from atrium.config import AtriumConfig
from atrium.frontier_core import BoolMat, FrontierData, Selection, evaluate_selection
from atrium.manifest import read_manifest
from atrium.paths import DatasetPaths
from atrium.routing.advert import tokenize

logger = logging.getLogger(__name__)

SCORE_NORMS = ("none", "zscore", "rank")
# 全員に聞く方式の再現率に対する目標の比（p0006 §7.3）
TARGET_RATIO = 0.95
FEB4RAG_M = 9
MEDRAG_M = 3
# MedRAG の統合後の上位の件数（config の retrieval.k_rerank と同じ）
MEDRAG_TOP = 15
# MedRAG の要約：シャードごとに取るファイルの数と，1 ファイルから取る文書の数
SUMMARY_FILES_PER_SHARD = 25
SUMMARY_DOCS_PER_FILE = 2000
# 検索のブロック（30 万断片を超えるファイルを 12 GB の GPU で扱うための行数．atrium.labels と同じ）
SCORE_BLOCK_ROWS = 50_000
EMB_CACHE = "emb_cache.npz"
Z_EPSILON = 1e-9


def flood_recall(data: FrontierData, split: str) -> float:
    """全員に probe したときの再現率（目標の基準）．"""
    all_mask = np.ones(data.second[split].shape, dtype=bool)
    return float(evaluate_selection(data, Selection(candidates=all_mask), split).recall_m.mean())


def finalize(data: FrontierData) -> FrontierData:
    """目標の hit（全員に聞く方式の再現率の 0.95 倍．dev の値）を決める．"""
    data.target_hit = TARGET_RATIO * flood_recall(data, "dev")
    data.cache["flood_recall"] = {s: flood_recall(data, s) for s in ("dev", "test")}
    return data


def normalize_scores(
    raw: dict[str, F32Array], train_rows: F32Array, norm: str
) -> dict[str, F32Array]:
    """エンジンごとの得点を，train の質問の得点の分布で正規化する（none・zscore・rank）．"""
    if norm == "none":
        return raw
    if norm == "zscore":
        mu = train_rows.mean(axis=0, keepdims=True)
        sd = train_rows.std(axis=0, keepdims=True) + Z_EPSILON
        return {s: ((v - mu) / sd).astype(np.float32) for s, v in raw.items()}
    ordered = np.sort(train_rows, axis=0)
    n = ordered.shape[0]
    return {
        s: np.stack(
            [np.searchsorted(ordered[:, j], v[:, j], side="right") / n for j in range(v.shape[1])],
            axis=1,
        ).astype(np.float32)
        for s, v in raw.items()
    }


def encode_with_cache(texts: list[str], cache: Path | None, encoder: Any) -> F32Array:
    """文書側の埋め込み（fp16 に丸めて保存）．cache があれば再利用する．"""
    if cache is not None and cache.exists():
        return np.asarray(np.load(cache)["emb"], dtype=np.float32)
    emb = encoder.encode_texts(texts).astype(np.float16)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, emb=emb)
    return np.asarray(emb, dtype=np.float32)


# ── FeB4RAG ───────────────────────────────────────────────────────────────


class Feb4ragBase:
    """配置に依らない FeB4RAG の評価の入力（エンジンの文書・得点・ラベル・質問）．"""

    def __init__(self, cfg: AtriumConfig, paths: DatasetPaths, work_dir: Path | None) -> None:
        """エンジンの検索結果と文書を読み，要約の元（arctic の埋め込みと語）を作る．"""
        from atrium.encoders import ArcticEncoder

        e = cfg.data.require_enronqa()
        self.cfg = cfg
        manifest = read_manifest(paths.manifest)
        self.names = list(manifest.sources)
        questions = load_questions(paths.questions)
        self.qids = [q.qid for q in questions]
        texts = {q.qid: q.question for q in questions}
        labels: dict[str, list[str]] = json.loads(paths.labels.read_text(encoding="utf-8"))
        split = json.loads(paths.split.read_text(encoding="utf-8"))
        self.split_qids = {
            "train": list(split["train"]),
            "dev": list(split["train"]) + list(split["val"]),
            "test": list(split["test"]),
        }
        row = {q: i for i, q in enumerate(self.qids)}
        top = np.full((len(self.qids), len(self.names)), -np.inf, dtype=np.float32)
        docs: list[list[str]] = []
        for j, name in enumerate(self.names):
            shard = paths.shards / name
            with (shard / "results.jsonl").open(encoding="utf-8") as f:
                for line in f:
                    r = json.loads(line)
                    qid = f"feb4rag/{r['qid']}"
                    if qid in row and r["hits"]:
                        top[row[qid], j] = float(r["hits"][0][1])
            with (shard / "docs.jsonl").open(encoding="utf-8") as f:
                docs.append(
                    [
                        f"{d.get('title') or ''}. {d.get('text') or ''}".strip()
                        for d in map(json.loads, f)
                    ]
                )
        self.raw = {s: top[[row[q] for q in self.split_qids[s]]] for s in ("train", "dev", "test")}
        if not np.isfinite(self.raw["dev"]).all():
            raise ValueError("some FeB4RAG queries have no results for an engine")
        rel = np.zeros((len(self.qids), len(self.names)), dtype=bool)
        index = {n: i for i, n in enumerate(self.names)}
        for qid, engines in labels.items():
            rel[row[qid], [index[x] for x in engines]] = True
        self.relevant = {s: rel[[row[q] for q in self.split_qids[s]]] for s in ("dev", "test")}
        encoder = ArcticEncoder(e.encoder, e.embed_batch_size)
        q_all = encoder.encode_queries([texts[q] for q in self.qids])
        self.q_emb = {s: q_all[[row[q] for q in self.split_qids[s]]] for s in ("dev", "test")}
        self.q_tokens = {
            s: [tokenize(texts[q]) for q in self.split_qids[s]] for s in ("dev", "test")
        }
        cache = None if work_dir is None else work_dir / "feb4rag_doc_emb.npz"
        flat = encode_with_cache([t for d in docs for t in d], cache, encoder)
        self.summary_emb = list(np.split(flat, np.cumsum([len(d) for d in docs])[:-1]))
        self.summary_tokens = [[tokenize(t) for t in d] for d in docs]

    def data(self, score_norm: str) -> FrontierData:
        """得点の正規化を与えて，評価の入力にする．"""
        second = normalize_scores(
            {s: self.raw[s] for s in ("dev", "test")}, self.raw["train"], score_norm
        )
        n = len(self.names)
        return finalize(
            FrontierData(
                dataset="feb4rag",
                placement=f"feb4rag_{score_norm}",
                names=self.names,
                device_of_source=np.arange(n, dtype=np.int64),
                m=FEB4RAG_M,
                target_hit=0.0,
                second=second,
                relevant=self.relevant,
                q_emb=self.q_emb,
                q_tokens=self.q_tokens,
                subsets={"dev": {}, "test": {}},
                seed=self.cfg.experiment.seed,
                summary_emb=self.summary_emb,
                summary_tokens=self.summary_tokens,
                exclude_docs=[set() for _ in self.names],
            )
        )


# ── MedRAG ────────────────────────────────────────────────────────────────


def shard_top_scores(
    cfg: AtriumConfig, paths: DatasetPaths, cache: Path | None
) -> tuple[F32Array, list[str]]:
    """全シャードを総当たりで検索し，質問ごと・シャードごとの上位 MEDRAG_TOP 件の内積を返す（(質問, シャード, 件)．GPU があれば使う）．"""
    import torch

    manifest = read_manifest(paths.manifest)
    shard_ids = [s.shard_id for s in manifest.shards]
    if cache is not None and cache.exists():
        return np.load(cache), shard_ids
    device = "cuda" if torch.cuda.is_available() else "cpu"
    ids: list[str] = json.loads(paths.query_ids.read_text(encoding="utf-8"))
    queries = torch.from_numpy(np.load(paths.query_embeddings(cfg.data.medrag.query_encoder))).to(
        device
    )
    out = np.full((len(ids), len(shard_ids), MEDRAG_TOP), -np.inf, dtype=np.float32)
    for j, shard in enumerate(manifest.shards):
        best = torch.full((len(ids), MEDRAG_TOP), float("-inf"), device=device)
        for f in shard.files:
            emb = np.load(
                paths.shards / shard.shard_id / "emb" / f"{f.name}.f16.npy", mmap_mode="r"
            )
            for start in range(0, emb.shape[0], SCORE_BLOCK_ROWS):
                block = torch.from_numpy(np.asarray(emb[start : start + SCORE_BLOCK_ROWS])).to(
                    device
                )
                scores = queries @ block.float().T
                best = torch.topk(torch.cat([best, scores], dim=1), MEDRAG_TOP, dim=1).values
        out[:, j] = best.cpu().numpy()
        logger.info("scored shard %s (%d / %d)", shard.shard_id, j + 1, len(shard_ids))
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, out)
    return out, shard_ids


def contributing_shards(top: F32Array) -> BoolMat:
    """統合後の上位 MEDRAG_TOP 件に 1 件以上を出したシャードの印 (質問, シャード)．"""
    n_q, n_shards, k = top.shape
    stacked = top.reshape(n_q, n_shards * k)
    owner = np.repeat(np.arange(n_shards), k)
    order = np.argsort(-stacked, axis=1, kind="stable")[:, :MEDRAG_TOP]
    mask = np.zeros((n_q, n_shards), dtype=bool)
    rows = np.repeat(np.arange(n_q), MEDRAG_TOP)
    mask[rows, owner[order].ravel()] = True
    return mask


def sample_shard_docs(
    paths: DatasetPaths, shard: Any, rng: np.random.Generator
) -> tuple[F32Array, list[list[str]]]:
    """シャードから無作為に取った文書の（埋め込み，語）．ファイルを無作為に選び，ファイルの中から無作為に取る．"""
    files = list(shard.files)
    picked = [files[int(i)] for i in rng.permutation(len(files))[:SUMMARY_FILES_PER_SHARD]]
    embs: list[F32Array] = []
    tokens: list[list[str]] = []
    for f in picked:
        emb = np.load(paths.shards / shard.shard_id / "emb" / f"{f.name}.f16.npy", mmap_mode="r")
        rows = np.sort(rng.permutation(emb.shape[0])[:SUMMARY_DOCS_PER_FILE])
        wanted = set(rows.tolist())
        texts: dict[int, str] = {}
        with (paths.shards / shard.shard_id / "chunk" / f"{f.name}.jsonl").open(
            encoding="utf-8"
        ) as fh:
            for i, line in enumerate(fh):
                if i in wanted:
                    d = json.loads(line)
                    texts[i] = f"{d.get('title') or ''}. {d.get('content') or ''}"
        keep = [i for i in rows.tolist() if i in texts]
        embs.append(np.asarray(emb[keep], dtype=np.float32))
        tokens += [tokenize(texts[i]) for i in keep]
    return np.concatenate(embs), tokens


class MedragBase:
    """MedRAG の評価の入力（シャードの得点・ラベル・質問・要約の元）．"""

    def __init__(self, cfg: AtriumConfig, paths: DatasetPaths, work_dir: Path | None) -> None:
        """シャードの検索結果（上位 15 件）と，各シャードからの文書の標本を作る．"""
        self.cfg = cfg
        top, shard_ids = shard_top_scores(
            cfg, paths, None if work_dir is None else work_dir / "medrag_shard_top.npy"
        )
        self.names = shard_ids
        manifest = read_manifest(paths.manifest)
        ids: list[str] = json.loads(paths.query_ids.read_text(encoding="utf-8"))
        row = {q: i for i, q in enumerate(ids)}
        split = json.loads(paths.split.read_text(encoding="utf-8"))
        qs = {
            "dev": list(split["train"]) + list(split["val"]),
            "test": list(split["test"]),
        }
        contributing = contributing_shards(top)
        # 既存のデータ源のラベル（labels.json）と，シャードのラベルから求めたデータ源の集合が一致するか
        labels: dict[str, list[str]] = json.loads(paths.labels.read_text(encoding="utf-8"))
        source_of = [s.source for s in manifest.shards]
        agree = float(
            np.mean(
                [
                    sorted({source_of[j] for j in np.flatnonzero(contributing[row[q]])})
                    == sorted(labels[q])
                    for q in ids
                ]
            )
        )
        self.label_agreement = agree
        logger.info("medrag shard labels agree with labels.json on %.4f of queries", agree)
        self.second = {s: top[[row[q] for q in qs[s]]][:, :, 0] for s in ("dev", "test")}
        self.relevant = {s: contributing[[row[q] for q in qs[s]]] for s in ("dev", "test")}
        questions = {q.qid: q.question for q in load_questions(paths.questions)}
        q_all = np.load(paths.query_embeddings(cfg.data.medrag.query_encoder))
        self.q_emb = {s: q_all[[row[q] for q in qs[s]]].astype(np.float32) for s in qs}
        self.q_tokens = {s: [tokenize(questions[q]) for q in qs[s]] for s in qs}
        rng = np.random.default_rng(cfg.experiment.seed)
        sampled = [sample_shard_docs(paths, shard, rng) for shard in manifest.shards]
        self.summary_emb = [e for e, _ in sampled]
        self.summary_tokens = [t for _, t in sampled]

    def data(self) -> FrontierData:
        """評価の入力にする（1 シャード = 1 デバイス）．"""
        n = len(self.names)
        return finalize(
            FrontierData(
                dataset="medrag",
                placement="medrag",
                names=self.names,
                device_of_source=np.arange(n, dtype=np.int64),
                m=MEDRAG_M,
                target_hit=0.0,
                second=self.second,
                relevant=self.relevant,
                q_emb=self.q_emb,
                q_tokens=self.q_tokens,
                subsets={"dev": {}, "test": {}},
                seed=self.cfg.experiment.seed,
                summary_emb=self.summary_emb,
                summary_tokens=self.summary_tokens,
                exclude_docs=[set() for _ in self.names],
            )
        )
