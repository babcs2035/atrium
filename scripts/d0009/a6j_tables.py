"""d0009 A6 参加（Iteration 31）：参加の測定で引く cross-encoder の得点の表を作り足す（gpu4．運用ルール 14．既存の表は書き換えない）．

使い方（リポジトリの直下．gpu4 で．前後の nvidia-smi は手で記録する．CPU の埋め込みと GPU の採点は別のプロセスに分ける）:
    uv run python scripts/d0009/a6j_tables.py --part owner --data-dir artifacts --out-dir results/d0009_a6_join/table
    CUDA_VISIBLE_DEVICES= uv run python scripts/d0009/a6j_tables.py --part central-embed --data-dir artifacts --out-dir results/d0009_a6_join/table
    uv run python scripts/d0009/a6j_tables.py --part central --data-dir artifacts --out-dir results/d0009_a6_join/table
- owner：加わる 15 受信箱の H の先頭 5 問（75 問）のうち，Iteration 29・30 の表（`holdout_owner.jsonl`．H の受信箱ごとの先頭 2 問 × 全 150 受信箱）に
  無い 45 問について，全 150 受信箱の上位 k_context 通（ノードの `/v1/answer` と同じ検索）を `CrossEncoderReranker.scores` で採点する．
  既存の表の全ての組と合わせて `ce_table/holdout_owner_join.jsonl` に書く．ゲート G-TB：(1) `results/d0009_a3_p1` の 864 候補の文脈がオフラインの
  検索と順序まで一致する，(2) 75 問 × 150 受信箱の上位 k_context 通の全てが新しい表にある．満たさなければ表を置かずに終わる（終了コード 1）．
- central-embed：最初の 135 受信箱の `CentralIndex`（fusion=rrf）に，加わる 15 受信箱のメールを CPU の `ArcticEncoder` で埋め込んで足した索引の
  75 問の上位 TOP_DEPTH 通を求め，`<out-dir>/central_top.json` に書く（実機の制御点の CPU での埋め込みと同じ手順．GPU は使わない）．
- central：central_top.json の組のうち `holdout.jsonl` に無い組を採点し，既存の表の全ての組と合わせて `ce_table/holdout_join.jsonl` に書く．
  ゲート G-TB：上位 retrieval.k_ret 通の全てが新しい表にある．
結果は `<out-dir>/table_check_{owner,central}.json`．
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from a6f_owner_table_all import P_RUN, TOLERANCE, build_stores, done_qids, score_all

from atrium.analysis import read_results
from atrium.benchmarks import Question, load_questions
from atrium.config import AtriumConfig, load_config
from atrium.data_enronqa import QUESTION_FILES, read_inbox
from atrium.paths import DatasetPaths, dataset_paths
from atrium.requester import CachedQueryEmbedder, CrossEncoderReranker
from atrium.routing.central import CentralIndex, inbox_of_doc

logger = logging.getLogger(__name__)

# 加わる受信箱ごとの H の問の数（join-run の --max-tries-per-inbox と同じ）
QUESTIONS_PER_JOINER_INBOX = 5
# 既存の owner の表が覆う，H の受信箱ごとの先頭の問の数（Iteration 29・30 の 288 問）
EXISTING_QUESTIONS_PER_INBOX = 2
# 実機の索引と作り直した索引の差を吸収するために，retrieval.k_ret より深く取る
TOP_DEPTH = 100
# 加わる受信箱・ホストは p0007 E2 と同じ（journal の Iteration 31 の G-J）
JOIN_PLAN = Path("results/p0007_e2_join/join.json")
OWNER_FAILURE_TABLE_DIR = Path("results/d0009_a6_failure/table")
CENTRAL_TOP_FILE = "central_top.json"

Row = dict[str, Any]


def joiner_plan() -> tuple[list[str], dict[str, list[str]]]:
    """p0007 E2 の加わる受信箱と，ホスト → その受信箱（ノードの shard_ids の順）を読む．"""
    join = json.loads(JOIN_PLAN.read_text(encoding="utf-8"))
    inboxes_of_host = {
        d["host"]: [a["inbox"] for a in d["answers"]] for d in join["p2p"]["devices"]
    }
    return list(join["plan"]["joiner_inboxes"]), inboxes_of_host


def holdout_questions(paths: DatasetPaths, per_inbox: int) -> list[Question]:
    """H の質問を，各受信箱の先頭 per_inbox 問（ファイルの順）まで読む．"""
    return load_questions(paths.questions.parent / QUESTION_FILES["holdout"], per_inbox)


def joiner_questions(paths: DatasetPaths, joiner_inboxes: list[str]) -> list[Question]:
    """加わる受信箱の H の先頭 5 問（75 問．join-run の試行の列と同じ）．"""
    joiner_set = set(joiner_inboxes)
    return [q for q in holdout_questions(paths, QUESTIONS_PER_JOINER_INBOX) if q.bank in joiner_set]


def holdout_embedder(paths: DatasetPaths, encoder: str) -> CachedQueryEmbedder:
    """H のクエリ埋め込み（queries/holdout/）を引く埋め込み器．"""
    return CachedQueryEmbedder(
        paths.split_query_ids("holdout"),
        {encoder: paths.split_query_embeddings("holdout", encoder)},
    )


def write_table(path: Path, rows: list[Row]) -> None:
    """表（1 行 1 組）を一時ファイルへ書いてから置き換える．"""
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp.replace(path)


def read_rows(path: Path) -> list[Row]:
    """表の全行を読む（qid・query・doc_id・score）．"""
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def run_owner(cfg: AtriumConfig, paths: DatasetPaths, out_dir: Path) -> int:
    """R3′ の表を作り足す．G-TB を満たせば `holdout_owner_join.jsonl` を置く（満たさなければ 1）．"""
    e = cfg.data.require_enronqa()
    joiner_inboxes, _ = joiner_plan()
    existing_qs = holdout_questions(paths, EXISTING_QUESTIONS_PER_INBOX)
    existing_qids = {q.qid for q in existing_qs}
    wanted = joiner_questions(paths, joiner_inboxes)
    new_qs = [q for q in wanted if q.qid not in existing_qids]
    # 既存の 288 問は採点済みなので，検索だけして（G-TB (1) の照合に使う），採点は新しい問に限る
    done_path = out_dir / "done_qids.txt"
    if not done_path.exists():
        shutil.copy2(OWNER_FAILURE_TABLE_DIR / "done_qids.txt", done_path)
    stores = build_stores(paths, list(e.sources), e.encoder, cfg.retrieval.fusion_depth)
    top_of = score_all(
        existing_qs + new_qs,
        stores,
        holdout_embedder(paths, e.encoder),
        e.encoder,
        e.k_context,
        out_dir,
    )

    # G-TB (1)：Iteration 29 の P の 864 候補の文脈とオフラインの上位 k_context 通が順序まで一致する
    question_of = {q.qid: q.question for q in existing_qs}
    mismatched: list[str] = []
    n_candidates = 0
    for r in read_results(P_RUN / "results.jsonl"):
        if r.get("error") is not None or r["qid"] not in question_of:
            continue
        for c in r["owner_answer"]["candidates"]:
            n_candidates += 1
            if top_of[(r["qid"], question_of[r["qid"]], c["source"])] != c["context_doc_ids"]:
                mismatched.append(f"{r['qid']}/{c['source']}")

    # G-TB (2)：新しい表 = 既存の表の全ての組 + 新しい問の採点．75 問 × 全受信箱の上位 k_context 通の全てが表にある
    existing_rows = read_rows(paths.ce_table_owner("holdout"))
    new_rows = read_rows(out_dir / "partial.jsonl")
    merged: dict[tuple[str, str], Row] = {(r["query"], r["doc_id"]): r for r in existing_rows}
    for row in new_rows:
        merged.setdefault((row["query"], row["doc_id"]), row)
    needed = {
        (q.question, doc_id)
        for q in wanted
        for inbox in e.sources
        for doc_id in top_of[(q.qid, q.question, inbox)]
    }
    missing = sorted(needed - merged.keys())
    passed = (
        not mismatched
        and not missing
        and {r["qid"] for r in new_rows} == {q.qid for q in new_qs}
        and len(done_qids(out_dir)) == len(existing_qids) + len(new_qs)
    )
    check = {
        "n_wanted_questions": len(wanted),
        "n_new_questions_scored": len(new_qs),
        "n_pairs_existing": len(existing_rows),
        "n_pairs_new_partial": len(new_rows),
        "n_pairs_merged": len(merged),
        "n_p_candidates_compared": n_candidates,
        "n_p_candidates_mismatched": len(mismatched),
        "mismatched_examples": mismatched[:10],
        "n_needed_pairs": len(needed),
        "n_needed_pairs_missing": len(missing),
        "tolerance": TOLERANCE,
        "G_TB_owner_passed": passed,
    }
    (out_dir / "table_check_owner.json").write_text(json.dumps(check, indent=1), encoding="utf-8")
    print(json.dumps(check, indent=1))
    if not passed:
        return 1
    write_table(
        paths.ce_table_owner("holdout").with_name("holdout_owner_join.jsonl"), list(merged.values())
    )
    return 0


def run_central_embed(cfg: AtriumConfig, paths: DatasetPaths, out_dir: Path) -> int:
    """最初の 135 受信箱の索引に加わる 15 受信箱を CPU で埋め込んで足し，75 問の上位 TOP_DEPTH 通を書く（GPU は使わない）．"""
    from atrium.encoders import ArcticEncoder

    e = cfg.data.require_enronqa()
    joiner_inboxes, inboxes_of_host = joiner_plan()
    joiner_set = set(joiner_inboxes)
    rrf = cfg.model_copy(update={"retrieval": cfg.retrieval.model_copy(update={"fusion": "rrf"})})
    central = CentralIndex.from_corpus(
        rrf, paths, sources=[s for s in e.sources if s not in joiner_set]
    )
    encoder = ArcticEncoder(e.encoder, e.embed_batch_size, device="cpu")
    for host, inboxes in inboxes_of_host.items():
        docs = [d for inbox in inboxes for d in read_inbox(paths, inbox)]
        emb = encoder.encode_texts([d["content"] for d in docs])
        add_s = central.append(
            [d["id"] for d in docs], [d["title"] for d in docs], [d["content"] for d in docs], emb
        )
        logger.info("appended %s: %d mails (add %.1f s)", host, len(docs), add_s)
    embedder = holdout_embedder(paths, e.encoder)
    top: dict[str, list[str]] = {}
    for q in joiner_questions(paths, joiner_inboxes):
        ids = central.search_ids(embedder.embed(q)[e.encoder], TOP_DEPTH, q.question)
        top[q.qid] = [central.doc_ids[i] for i in ids]
    (out_dir / CENTRAL_TOP_FILE).write_text(
        json.dumps({"n_docs": central.n_docs, "top": top}), encoding="utf-8"
    )
    logger.info("wrote %s (%d questions)", CENTRAL_TOP_FILE, len(top))
    return 0


def run_central(cfg: AtriumConfig, paths: DatasetPaths, out_dir: Path) -> int:
    """central_top.json の組のうち既存の表に無いものを採点し，`holdout_join.jsonl` を置く．G-TB：上位 k_ret 通の全てが表にあること．"""
    saved = json.loads((out_dir / CENTRAL_TOP_FILE).read_text(encoding="utf-8"))
    top: dict[str, list[str]] = saved["top"]
    joiner_inboxes, _ = joiner_plan()
    question_of = {q.qid: q.question for q in joiner_questions(paths, joiner_inboxes)}
    # 採点の入力は `<題>\n<本文>`（`atrium ce-table` と同じ）
    doc_text: dict[str, str] = {}
    for inbox in {inbox_of_doc(d) for ids in top.values() for d in ids}:
        for d in read_inbox(paths, inbox):
            doc_text[d["id"]] = f"{d['title']}\n{d['content']}".strip()
    table_path = paths.ce_table("holdout")
    existing_rows = read_rows(table_path)
    existing = {(r["query"], r["doc_id"]) for r in existing_rows}
    scorer = CrossEncoderReranker()
    new_rows: list[Row] = []
    for qid, ids in top.items():
        query = question_of[qid]
        todo = [d for d in ids if (query, d) not in existing]
        scores = scorer.scores(query, [doc_text[d] for d in todo])
        new_rows += [
            {"qid": qid, "query": query, "doc_id": d, "score": s}
            for d, s in zip(todo, scores, strict=True)
        ]
    merged = existing_rows + new_rows
    have = {(r["query"], r["doc_id"]) for r in merged}
    # `search_ids(k)` の結果は RRF で選んだ k 行を内積の順に並べ直したもので，先頭 k_ret 行は k_ret 通の RRF の選択と違う．
    # RRF の上位 k_ret 通は上位 TOP_DEPTH 通の部分集合なので，上位 TOP_DEPTH 通の全ての組が表にあれば G-TB を満たす
    missing = [
        (qid, d) for qid, ids in top.items() for d in ids if (question_of[qid], d) not in have
    ]
    # 参考（判定に使わない）：既存の表の 50 通（全体の索引の RRF の上位 k_ret 通）が，作り直した索引の上位 TOP_DEPTH 通に入る問の数
    in_table: dict[str, set[str]] = {}
    for r in existing_rows:
        in_table.setdefault(str(r["qid"]), set()).add(str(r["doc_id"]))
    contained = sum(in_table.get(qid, set()) <= set(ids) for qid, ids in top.items())
    passed = not missing
    k_ret = cfg.retrieval.k_ret
    check = {
        "n_questions": len(top),
        "n_docs_rebuilt_index": saved["n_docs"],
        "top_depth": TOP_DEPTH,
        "k_ret": k_ret,
        "n_pairs_existing": len(existing_rows),
        "n_pairs_new": len(new_rows),
        "n_top_depth_pairs_missing": len(missing),
        "n_questions_existing_table_set_within_rebuilt_top_depth": contained,
        "G_TB_central_passed": passed,
    }
    (out_dir / "table_check_central.json").write_text(json.dumps(check, indent=1), encoding="utf-8")
    print(json.dumps(check, indent=1))
    if not passed:
        return 1
    write_table(table_path.with_name("holdout_join.jsonl"), merged)
    return 0


def main() -> int:
    """引数の部分（owner・central-embed・central）を実行する．"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=["owner", "central-embed", "central"], required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    paths = dataset_paths(Path(args.data_dir), "enronqa")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.part == "owner":
        return run_owner(cfg, paths, out_dir)
    if args.part == "central-embed":
        return run_central_embed(cfg, paths, out_dir)
    return run_central(cfg, paths, out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
