"""EnronQA（Enron の 150 人の受信箱．p0004）のデータ準備（制御点のコンテナで `atrium data enronqa <step>` から実行する）．

段（各段は冪等で，成果物があれば飛ばす）:

    fetch      Hugging Face から parquet を取得する（raw/．リビジョンは data.enronqa.hf_revision に固定）
    corpus     受信箱ごとにメールを corpus/<inbox>/chunk/<inbox>.jsonl へ書く（MedRAG の chunk と同じ書式）
    embed      メールを arctic-embed で埋め込み，corpus/<inbox>/emb/<inbox>.f16.npy に fp16 で保存する
    questions  評価（test）・公開情報の大きさの選択（dev）・中央の分類器の学習（train）の質問を抜き出す
    queries    抜き出した全質問のクエリ埋め込み
    labels     関連ラベル（質問 → 正解のメールの受信箱）と分割（train / val / test）
    shards     受信箱ごとのシャード（本文と埋め込みは corpus への symlink）と，公開情報（Advert）

データの扱い:
- 3 つの分割は同じ 73,772 通のメールを持ち，質問だけを分けている（results/20261009_eq0_data_check）．
  検索の対象は全メールで，質問は分割ごとに抜き出す．
- 他の受信箱に Jaccard 類似度 duplicate_jaccard 以上のメールがある質問は除く（ラベルの雑音．p0004 §7.2 の V3）．
- メールの本文の "File:" の行（受信箱のファイルのパス）はデータセットの付属情報であり，実際のメールには無い．
  受信箱の名前がそのまま書かれているので，検索・回答・公開情報の全てから除く．
- 本文は制御点のデータディレクトリにだけ置き，results/ と git には入れない（p0004 §2.3）．
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from atrium.arrays import F32Array
from atrium.benchmarks import Question, write_questions
from atrium.config import AtriumConfig, EnronqaDataConfig
from atrium.data_medrag import STAMP
from atrium.manifest import (
    SHARD_SPEC_FILENAME,
    Advert,
    ChunkFile,
    Manifest,
    ShardSpec,
    write_json_model,
)
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

RAW_DIRNAME = "raw"
SPLITS = ("train", "dev", "test")
# 分割ごとの質問の置き場所（test は既存の質問者・分析が読む questions.jsonl）
QUESTION_FILES = {
    "test": "questions.jsonl",
    "dev": "dev_questions.jsonl",
    "train": "train_questions.jsonl",
}
GOLD_FILE = "gold.jsonl"
# 分割の名前 → labels/split.json のキー（既存の train_router・分析と同じ名前）
SPLIT_KEYS = {"train": "train", "dev": "val", "test": "test"}
HEADER_SEPARATOR = "====================================="
FILE_LINE = re.compile(r"^File: .*$", re.MULTILINE)
TOKEN = re.compile(r"[a-z0-9]+")
SUBJECT_PREFIX = re.compile(r"^\s*((re|fw|fwd)\s*:\s*)+", re.IGNORECASE)
# 重複の検出（MinHash の LSH で候補を出し，正確な Jaccard で確かめる）
MINHASH_PERMS = 128
LSH_BANDS = 32
MERSENNE_PRIME = (1 << 61) - 1
# 語のスケッチから除く短い語の長さ（2 文字以下は意味が薄い）
MIN_TERM_LENGTH = 3
KMEANS_ITERATIONS = 20


@dataclass(frozen=True)
class Email:
    """受信箱のメール 1 通（本文は "File:" の行を除いたもの）．"""

    path: str
    inbox: str
    subject: str
    text: str


def _raw_files(paths: DatasetPaths, split: str) -> list[Path]:
    return sorted((paths.root / RAW_DIRNAME / "data").glob(f"{split}-*.parquet"))


def _read_split(paths: DatasetPaths, split: str, columns: list[str]) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = []
    for f in _raw_files(paths, split):
        rows.extend(pq.read_table(f, columns=columns).to_pylist())
    return rows


def clean_email(raw: str) -> tuple[str, str]:
    """データセットのメールの文字列から（件名，"File:" の行を除いた本文）を返す．"""
    text = FILE_LINE.sub("", raw)
    first = raw.split("\n", 1)[0]
    subject = first[len("Subject:") :].strip() if first.startswith("Subject:") else ""
    return subject, text


def tokens(text: str) -> list[str]:
    """小文字の英数字の語に分ける（語のスケッチ・BM25・重複の検出で共通）．"""
    return TOKEN.findall(text.lower())


def fetch(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """parquet を raw/ へ取得する（固定のリビジョン．匿名で取得する）．"""
    e = cfg.data.require_enronqa()
    if all(_raw_files(paths, s) for s in SPLITS):
        return
    from huggingface_hub import snapshot_download

    snapshot_download(
        e.hf_repo,
        repo_type="dataset",
        revision=e.hf_revision,
        local_dir=paths.root / RAW_DIRNAME,
        token=False,
    )


def read_emails(paths: DatasetPaths) -> list[Email]:
    """全メールを path の順に読む（3 つの分割は同じメールを持つので test から読む）．"""
    rows = _read_split(paths, "test", ["email", "path", "user"])
    out = []
    for r in sorted(rows, key=lambda r: r["path"]):
        subject, text = clean_email(r["email"])
        out.append(Email(path=r["path"], inbox=r["user"], subject=subject, text=text))
    return out


def write_corpus(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """受信箱ごとのメールを chunk/<inbox>.jsonl に書く（{"id": path, "title": 件名, "content": 本文}）．"""
    e = cfg.data.require_enronqa()
    by_inbox: dict[str, list[Email]] = defaultdict(list)
    for mail in read_emails(paths):
        by_inbox[mail.inbox].append(mail)
    unknown = sorted(set(by_inbox) - set(e.sources))
    if unknown:
        raise ValueError(f"inboxes not in data.enronqa.sources: {unknown}")
    for inbox in e.sources:
        chunk_dir = paths.corpus(inbox) / "chunk"
        if (chunk_dir / STAMP).exists():
            continue
        chunk_dir.mkdir(parents=True, exist_ok=True)
        with (chunk_dir / f"{inbox}.jsonl").open("w", encoding="utf-8") as f:
            for mail in by_inbox[inbox]:
                f.write(
                    json.dumps(
                        {"id": mail.path, "title": mail.subject, "content": mail.text},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        (chunk_dir / STAMP).touch()


def read_inbox(paths: DatasetPaths, inbox: str) -> list[dict[str, str]]:
    """受信箱の chunk を読む（行の順は埋め込みの行と同じ）．"""
    with (paths.corpus(inbox) / "chunk" / f"{inbox}.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def emb_path(paths: DatasetPaths, inbox: str) -> Path:
    """受信箱の埋め込み（fp16）のパス．"""
    return paths.corpus(inbox) / "emb" / f"{inbox}.f16.npy"


def embed_corpus(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """未埋め込みの受信箱のメールを埋め込む．"""
    from atrium.encoders import ArcticEncoder

    e = cfg.data.require_enronqa()
    todo = [i for i in e.sources if not emb_path(paths, i).exists()]
    if not todo:
        return
    encoder = ArcticEncoder(e.encoder, e.embed_batch_size)
    for n, inbox in enumerate(todo, 1):
        docs = read_inbox(paths, inbox)
        emb = encoder.encode_texts([d["content"] for d in docs]).astype(np.float16)
        out = emb_path(paths, inbox)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.npy")
        np.save(tmp, emb)
        tmp.replace(out)
        logger.info("embedded %s (%d emails, %d/%d)", inbox, len(docs), n, len(todo))


def find_cross_inbox_duplicates(
    token_sets: Sequence[set[str]], inboxes: Sequence[str], threshold: float, seed: int
) -> set[int]:
    """他の受信箱に Jaccard 類似度 threshold 以上のメールがあるメールの番号を返す．

    MinHash（MINHASH_PERMS 個）と LSH（LSH_BANDS 帯）で候補の組を出し，正確な Jaccard で確かめる．
    """
    rng = np.random.default_rng(seed)
    prime = np.uint64(MERSENNE_PRIME)
    a = rng.integers(1, MERSENNE_PRIME, MINHASH_PERMS, dtype=np.uint64)
    b = rng.integers(0, MERSENNE_PRIME, MINHASH_PERMS, dtype=np.uint64)
    vocab: dict[str, int] = {}
    sigs = np.full((len(token_sets), MINHASH_PERMS), np.iinfo(np.uint64).max, dtype=np.uint64)
    for i, ts in enumerate(token_sets):
        if ts:
            ids = np.array([vocab.setdefault(t, len(vocab)) for t in ts], dtype=np.uint64)
            sigs[i] = ((np.outer(ids, a) + b) % prime).min(axis=0)
    rows = MINHASH_PERMS // LSH_BANDS
    dup: set[int] = set()
    for band in range(LSH_BANDS):
        buckets: dict[bytes, list[int]] = defaultdict(list)
        part = sigs[:, band * rows : (band + 1) * rows]
        for i, ts in enumerate(token_sets):
            if ts:
                buckets[part[i].tobytes()].append(i)
        for members in buckets.values():
            if len({inboxes[m] for m in members}) < 2:
                continue
            for x in members:
                if x in dup:
                    continue
                for y in members:
                    if inboxes[y] == inboxes[x]:
                        continue
                    union = len(token_sets[x] | token_sets[y])
                    if union and len(token_sets[x] & token_sets[y]) / union >= threshold:
                        dup.add(x)
                        break
    return dup


def _per_inbox_limit(e: EnronqaDataConfig, split: str) -> int:
    return {"test": e.test_per_inbox, "dev": e.dev_per_inbox, "train": e.train_per_inbox}[split]


def write_questions_files(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """分割ごとに受信箱あたり最大 N 問を無作為に抜き出す（種は experiment.seed．重複のメールの質問は除く）．"""
    e = cfg.data.require_enronqa()
    bench = paths.questions.parent
    if (bench / GOLD_FILE).exists():
        return
    emails = read_emails(paths)
    dup_idx = find_cross_inbox_duplicates(
        [set(tokens(m.text.split(HEADER_SEPARATOR, 1)[-1])) for m in emails],
        [m.inbox for m in emails],
        e.duplicate_jaccard,
        cfg.experiment.seed,
    )
    dup_paths = {emails[i].path for i in dup_idx}
    logger.info("excluding questions on %d near-duplicate emails", len(dup_paths))
    gold: list[dict[str, Any]] = []
    for split in SPLITS:
        rows = _read_split(
            paths,
            split,
            ["path", "user", "questions", "gold_answers", "alternate_answers", "incorrect_answers"],
        )
        candidates: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for ri, r in enumerate(rows):
            if r["path"] in dup_paths:
                continue
            for qi in range(len(r["questions"])):
                candidates[r["user"]].append((ri, qi))
        rng = np.random.default_rng(cfg.experiment.seed)
        limit = _per_inbox_limit(e, split)
        questions: list[Question] = []
        for inbox in e.sources:
            pool = candidates.get(inbox, [])
            picked = sorted(
                pool[int(i)] for i in rng.permutation(len(pool))[: min(limit, len(pool))]
            )
            for ri, qi in picked:
                r = rows[ri]
                qid = f"enronqa/{split}/{ri}-{qi}"
                questions.append(
                    Question(
                        qid=qid,
                        bank=inbox,
                        question=r["questions"][qi],
                        options={},
                        answer=r["gold_answers"][qi],
                    )
                )
                gold.append(
                    {
                        "qid": qid,
                        "split": split,
                        "inbox": inbox,
                        "path": r["path"],
                        "gold_answer": r["gold_answers"][qi],
                        "alternate_answers": list(r["alternate_answers"][qi]),
                        "incorrect_answers": list(r["incorrect_answers"][qi]),
                    }
                )
        write_questions(bench / QUESTION_FILES[split], questions)
        logger.info("%s: %d questions", split, len(questions))
    with (bench / GOLD_FILE).open("w", encoding="utf-8") as f:
        for g in gold:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")


def read_gold(paths: DatasetPaths) -> dict[str, dict[str, Any]]:
    """質問 ID → 正解の情報（受信箱・メールの path・正解・別解・誤答）．"""
    with (paths.questions.parent / GOLD_FILE).open(encoding="utf-8") as f:
        return {g["qid"]: g for g in map(json.loads, f)}


def embed_queries(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """test・dev・train の全ての抜き出した質問を埋め込む（query_ids.json の順）．"""
    from atrium.benchmarks import load_questions
    from atrium.encoders import ArcticEncoder

    e = cfg.data.require_enronqa()
    out = paths.query_embeddings(e.encoder)
    if out.exists():
        return
    questions = [
        q for s in SPLITS for q in load_questions(paths.questions.parent / QUESTION_FILES[s])
    ]
    emb = ArcticEncoder(e.encoder, e.embed_batch_size).encode_queries(
        [q.question for q in questions]
    )
    paths.queries.mkdir(parents=True, exist_ok=True)
    paths.query_ids.write_text(json.dumps([q.qid for q in questions]), encoding="utf-8")
    np.save(out, emb)


def write_labels(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """関連ラベル（正解のメールの受信箱 1 個）と分割を書く．"""
    from atrium.labels import write_labels_meta

    gold = read_gold(paths)
    paths.labels.parent.mkdir(parents=True, exist_ok=True)
    paths.labels.write_text(
        json.dumps({qid: [g["inbox"]] for qid, g in gold.items()}), encoding="utf-8"
    )
    split: dict[str, list[str]] = {key: [] for key in SPLIT_KEYS.values()}
    for qid, g in gold.items():
        split[SPLIT_KEYS[g["split"]]].append(qid)
    paths.split.write_text(json.dumps(split), encoding="utf-8")
    write_labels_meta(cfg, "enronqa", paths)


# ── 公開情報（Advert）────────────────────────────────────────────────────────


def card_text(subjects: Sequence[str], n: int) -> str:
    """Agent Card の説明文：受信箱で多い件名（RE:・FW: を除く）の上位 n 個を並べる．"""
    counts = Counter(s for s in (SUBJECT_PREFIX.sub("", x).strip() for x in subjects) if s)
    top = [s for s, _ in counts.most_common(n)]
    return "Email inbox. Frequent subjects: " + "; ".join(top)


def term_sketch(texts: Sequence[str], size: int) -> tuple[list[str], list[float]]:
    """語のスケッチ：受信箱の中で多くのメールに現れる語（英語の機能語を除く）と，その語を含むメールの割合．

    他の受信箱の統計（全体の IDF など）を使わず，持ち主が自分のメールだけから計算できる．
    """
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    df: Counter[str] = Counter()
    for text in texts:
        df.update(
            {
                t
                for t in tokens(text)
                if len(t) >= MIN_TERM_LENGTH and not t.isdigit() and t not in ENGLISH_STOP_WORDS
            }
        )
    top = df.most_common(size)
    n = max(1, len(texts))
    return [t for t, _ in top], [c / n for _, c in top]


def kmeans_centroids(emb: F32Array, n: int, seed: int) -> F32Array:
    """埋め込みの k-means の中心（メールが n 通より少なければメールの数だけ）．"""
    import faiss

    k = min(n, emb.shape[0])
    if k == emb.shape[0]:
        return emb.astype(np.float32)
    km = faiss.Kmeans(emb.shape[1], k, niter=KMEANS_ITERATIONS, seed=seed, spherical=False)
    km.train(np.ascontiguousarray(emb, dtype=np.float32))
    if km.centroids is None:
        raise RuntimeError("k-means did not produce centroids")
    centroids: F32Array = km.centroids
    return centroids


def build_advert(
    cfg: AtriumConfig, docs: Sequence[dict[str, str]], emb: F32Array, card_emb: F32Array
) -> Advert:
    """受信箱 1 個の公開情報を作る（大きさは data.enronqa の card_subjects・term_sketch_size・n_centroids）．"""
    e = cfg.data.require_enronqa()
    terms, weights = term_sketch([d["content"] for d in docs], e.term_sketch_size)
    return Advert(
        terms=terms,
        term_weights=weights,
        centroids=kmeans_centroids(emb, e.n_centroids, cfg.experiment.seed).tolist(),
        card_embedding=card_emb.tolist(),
    )


def build_shards(cfg: AtriumConfig, paths: DatasetPaths) -> Manifest:
    """受信箱ごとに 1 個のシャードを作り，manifest.json を書く（本文と埋め込みは corpus への symlink）．"""
    from atrium.data_medrag import _link
    from atrium.encoders import ArcticEncoder

    e = cfg.data.require_enronqa()
    docs_of = {inbox: read_inbox(paths, inbox) for inbox in e.sources}
    cards = {
        inbox: card_text([d["title"] for d in docs_of[inbox]], e.card_subjects)
        for inbox in e.sources
    }
    card_emb = ArcticEncoder(e.encoder, e.embed_batch_size).encode_texts(
        [cards[i] for i in e.sources]
    )
    shards: list[ShardSpec] = []
    for n, inbox in enumerate(e.sources):
        emb = np.load(emb_path(paths, inbox)).astype(np.float32)
        shard_dir = paths.shards / inbox
        (shard_dir / "chunk").mkdir(parents=True, exist_ok=True)
        (shard_dir / "emb").mkdir(parents=True, exist_ok=True)
        _link(
            paths.corpus(inbox) / "chunk" / f"{inbox}.jsonl", shard_dir / "chunk" / f"{inbox}.jsonl"
        )
        _link(emb_path(paths, inbox), shard_dir / "emb" / f"{inbox}.f16.npy")
        spec = ShardSpec(
            shard_id=inbox,
            source=inbox,
            kind="faiss",
            files=[ChunkFile(name=inbox, n_docs=emb.shape[0])],
            n_docs=emb.shape[0],
            dim=emb.shape[1],
            encoder=e.encoder,
            centroid=emb.mean(axis=0).tolist(),
            description=cards[inbox],
            advert=build_advert(cfg, docs_of[inbox], emb, card_emb[n]),
        )
        write_json_model(shard_dir / SHARD_SPEC_FILENAME, spec)
        shards.append(spec)
    manifest = Manifest(dataset="enronqa", sources=list(e.sources), shards=shards)
    write_json_model(paths.manifest, manifest)
    return manifest
