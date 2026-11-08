"""d0009 A6 障害（Iteration 30）：全 150 受信箱の上位 5 通の cross-encoder の得点を表に足す（gpu4．運用ルール 14）．

使い方（リポジトリの直下．gpu4 の GPU が空いているときに．前後の nvidia-smi は手で記録する．設定は config.yaml の retrieval.fusion_depth・
data.enronqa の k_context・encoder・sources を読む）:
    uv run python scripts/d0009/a6f_owner_table_all.py --data-dir artifacts --out-dir results/d0009_a6_failure/table
- 止めた台の受信箱を避けて別の受信箱が選ばれると，Iteration 29 の表（`ce_table/holdout_owner.jsonl`．選ばれた 3 受信箱の文脈だけ）に無い
  組が出る．H の受信箱ごとの先頭 2 問（288 問）× 全受信箱について，ノードの `/v1/answer` と同じ検索（受信箱ごとの `FaissShardStore`・
  fusion=rrf・k_context 通・同じクエリ埋め込み）で上位 k_context 通を求め，`CrossEncoderReranker.scores`（候補ごとに別の呼び出し）で採点する．
- ゲート G-TB：(1) `results/d0009_a3_p1` の全候補（864）の `context_doc_ids` がオフラインの検索と順序まで一致する，(2) 既存の表の全ての組が
  新しい表にあり，得点の差が TOLERANCE 以下．満たさなければ表を置き換えずに終わる（終了コード 1）．
- 満たせば，元の表を `holdout_owner.iter29.jsonl` へ複製（既にあれば触らない）し，新しい表を `holdout_owner.jsonl` に置く（既存の組を全て含む上位集合）．
- 途中経過は `<out-dir>/partial.jsonl`・`done_qids.txt` に残し，再実行すると続きから採点する．結果は `<out-dir>/table_check.json`．
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import time
from pathlib import Path

import numpy as np

from atrium.analysis import read_results
from atrium.benchmarks import Question, load_questions
from atrium.config import load_config
from atrium.data_enronqa import QUESTION_FILES
from atrium.manifest import ChunkFile, ShardSpec
from atrium.paths import DatasetPaths, dataset_paths
from atrium.requester import CachedQueryEmbedder, CrossEncoderReranker
from atrium.store import FaissShardStore

logger = logging.getLogger(__name__)

QUESTIONS_PER_INBOX = 2
TOLERANCE = 1e-6
P_RUN = Path("results/d0009_a3_p1")
BACKUP_NAME = "holdout_owner.iter29.jsonl"
LOG_EVERY = 10


def build_stores(
    paths: DatasetPaths, sources: list[str], encoder: str, fusion_depth: int
) -> dict[str, FaissShardStore]:
    """全受信箱の検索器をノードと同じ構成（fp16 の FAISS・fusion=rrf・受信箱だけの BM25）で作る．"""
    stores: dict[str, FaissShardStore] = {}
    for inbox in sources:
        shard_dir = paths.corpus(inbox)
        n_docs, dim = np.load(shard_dir / "emb" / f"{inbox}.f16.npy", mmap_mode="r").shape
        spec = ShardSpec(
            shard_id=inbox,
            source=inbox,
            kind="faiss",
            files=[ChunkFile(name=inbox, n_docs=n_docs)],
            n_docs=n_docs,
            dim=dim,
            encoder=encoder,
            centroid=[],
            description="",
        )
        stores[inbox] = FaissShardStore(shard_dir, spec, "rrf", fusion_depth)
    return stores


def read_table(path: Path) -> dict[tuple[str, str], float]:
    """表（1 行 1 組：query・doc_id・score）を（質問文，文書 ID）→ 得点で読む．"""
    out: dict[tuple[str, str], float] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            out[(row["query"], row["doc_id"])] = float(row["score"])
    return out


def score_all(
    questions: list[Question],
    stores: dict[str, FaissShardStore],
    embedder: CachedQueryEmbedder,
    encoder: str,
    k_context: int,
    out_dir: Path,
) -> dict[tuple[str, str, str], list[str]]:
    """質問 × 全受信箱の上位 k_context 通を採点して partial.jsonl に足す．（質問 ID，受信箱）→ 上位の文書 ID の順を返す．"""
    done_path = out_dir / "done_qids.txt"
    partial_path = out_dir / "partial.jsonl"
    done = set(done_path.read_text(encoding="utf-8").split()) if done_path.exists() else set()
    scorer = CrossEncoderReranker()
    top_of: dict[tuple[str, str, str], list[str]] = {}
    start = time.perf_counter()
    with (
        partial_path.open("a", encoding="utf-8") as partial,
        done_path.open("a", encoding="utf-8") as done_file,
    ):
        for n, q in enumerate(questions, 1):
            embedding = embedder.embed(q)[encoder].tolist()
            rows = []
            for inbox, store in stores.items():
                docs = store.search(
                    k=k_context, embedding=embedding, query_id=q.source_qid, query_text=q.question
                )
                top_of[(q.qid, q.question, inbox)] = [d.doc_id for d in docs]
                if q.qid in done:
                    continue
                # 候補ごとに別の呼び出し（live の `_candidate_ce_scores` と同じ 4 組ずつの推論）
                values = scorer.scores(
                    q.question, [f"{d.title}\n{d.content}".strip() for d in docs]
                )
                rows += [
                    {"qid": q.qid, "query": q.question, "doc_id": d.doc_id, "score": v}
                    for d, v in zip(docs, values, strict=True)
                ]
            if q.qid not in done:
                partial.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
                partial.flush()
                done_file.write(q.qid + "\n")
                done_file.flush()
            if n % LOG_EVERY == 0:
                logger.info(
                    "%d / %d questions (%.0f s)", n, len(questions), time.perf_counter() - start
                )
    return top_of


def main() -> int:
    """表の作り足しと G-TB の確かめを行い，table_check.json を書く．ゲートを満たさなければ 1 を返す．"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    e = cfg.data.require_enronqa()
    paths = dataset_paths(Path(args.data_dir), "enronqa")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    questions = load_questions(
        paths.questions.parent / QUESTION_FILES["holdout"], QUESTIONS_PER_INBOX
    )
    embedder = CachedQueryEmbedder(
        paths.split_query_ids("holdout"),
        {e.encoder: paths.split_query_embeddings("holdout", e.encoder)},
    )
    stores = build_stores(paths, list(e.sources), e.encoder, cfg.retrieval.fusion_depth)
    top_of = score_all(questions, stores, embedder, e.encoder, e.k_context, out_dir)

    # G-TB (1)：Iteration 29 の P の候補の文脈と，オフラインの受信箱の中の上位 k_context 通が順序まで一致する
    question_of = {q.qid: q.question for q in questions}
    mismatched: list[str] = []
    n_candidates = 0
    for r in read_results(P_RUN / "results.jsonl"):
        if r.get("error") is not None or r["qid"] not in question_of:
            continue
        for c in r["owner_answer"]["candidates"]:
            n_candidates += 1
            if top_of[(r["qid"], question_of[r["qid"]], c["source"])] != c["context_doc_ids"]:
                mismatched.append(f"{r['qid']}/{c['source']}")

    # G-TB (2)：既存の表の全ての組が新しい表にあり，得点が一致する
    new_scores: dict[tuple[str, str], float] = {}
    with (out_dir / "partial.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            new_scores[(row["query"], row["doc_id"])] = float(row["score"])
    table = paths.ce_table_owner("holdout")
    existing = read_table(
        Path(table.parent / BACKUP_NAME) if (table.parent / BACKUP_NAME).exists() else table
    )
    missing_pairs = [p for p in existing if p not in new_scores]
    max_diff = max(
        (abs(new_scores[p] - s) for p, s in existing.items() if p in new_scores), default=0.0
    )
    passed = (
        not mismatched
        and not missing_pairs
        and max_diff <= TOLERANCE
        and len(done_qids(out_dir)) == len(questions)
    )
    check = {
        "n_questions": len(questions),
        "n_inboxes": len(stores),
        "n_unique_pairs_new": len(new_scores),
        "n_pairs_existing": len(existing),
        "n_p_candidates_compared": n_candidates,
        "n_p_candidates_mismatched": len(mismatched),
        "mismatched_examples": mismatched[:10],
        "n_existing_pairs_missing_in_new": len(missing_pairs),
        "max_abs_diff_existing_vs_new": max_diff,
        "tolerance": TOLERANCE,
        "G_TB_passed": passed,
    }
    (out_dir / "table_check.json").write_text(json.dumps(check, indent=1), encoding="utf-8")
    print(json.dumps(check, indent=1))
    if not passed:
        return 1

    backup = table.parent / BACKUP_NAME
    if not backup.exists():
        shutil.copy2(table, backup)  # 元の表は消さず別名で残す
    qid_of = {q: qid for qid, q in question_of.items()}
    tmp = table.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for (query, doc_id), score in new_scores.items():
            f.write(
                json.dumps(
                    {"qid": qid_of[query], "query": query, "doc_id": doc_id, "score": score},
                    ensure_ascii=False,
                )
                + "\n"
            )
    tmp.replace(table)
    return 0


def done_qids(out_dir: Path) -> set[str]:
    """採点を終えた質問 ID．"""
    return set((out_dir / "done_qids.txt").read_text(encoding="utf-8").split())


if __name__ == "__main__":
    raise SystemExit(main())
