"""d0009 A6（Iteration 29）：P の候補の文脈の cross-encoder の得点の表を作り，採点の費用 t_P・t_C を測る（gpu4．運用ルール 14）．

使い方（リポジトリの直下．gpu4 の GPU が空いているときに．前後の nvidia-smi は手で記録する）:
    uv run python scripts/d0009/a6_owner_table.py --data-dir artifacts --out-dir results/d0009_a6_load/table
- H の受信箱ごとの先頭 2 問（288 問）について，`results/d0009_a3_p1` の候補ごとの `context_doc_ids`（取得は決定的）を
  `CrossEncoderReranker.scores`（同じ revision・4 組ずつ・候補ごとに別の呼び出し．live と同じ）で採点し，
  `<data-dir>/enronqa/ce_table/holdout_owner.jsonl`（質問文・文書 ID → 得点）を書く（新しいファイル）．
- t_P：1 問（3 回の呼び出し）の採点の時間．t_C：同じ 288 問の C の上位 50 通（既存の `ce_table/holdout.jsonl` と同じ組）の 1 問の時間．
  どちらも REPEATS 回測って中央値を使う．C の再採点の得点は既存の表と |差| ≤ C_TABLE_TOLERANCE で一致することを確かめる（既存の表は変えない）．
- 結果は `<out-dir>/cost.json` に書く．
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from atrium.analysis import read_results
from atrium.benchmarks import load_questions
from atrium.config import load_config
from atrium.data_enronqa import QUESTION_FILES, read_inbox
from atrium.paths import DatasetPaths, dataset_paths
from atrium.requester import CrossEncoderReranker

QUESTIONS_PER_INBOX = 2
REPEATS = 3
C_TABLE_TOLERANCE = 1e-4
P_RUN = Path("results/d0009_a3_p1")


def median_per_question(times_by_repeat: list[list[float]]) -> dict[str, float]:
    """REPEATS 回の測定について，1 問あたりの時間の（問ごとの中央値の）中央値と，回ごとの 1 問の平均を返す．"""
    per_question = [statistics.median(col) for col in zip(*times_by_repeat, strict=True)]
    return {
        "median_s": statistics.median(per_question),
        "mean_s": statistics.fmean(per_question),
        "p95_s": sorted(per_question)[int(0.95 * (len(per_question) - 1))],
        "repeat_means_s": [statistics.fmean(t) for t in times_by_repeat],
    }


def measure_p(
    scorer: CrossEncoderReranker,
    question_of: dict[str, str],
    contexts: dict[str, list[list[str]]],
    text_of: dict[str, str],
) -> tuple[dict[str, object], dict[tuple[str, str], float]]:
    """P の候補の文脈を採点して t_P を測る．（費用，（質問文，文書 ID）→ 得点）を返す．"""
    scores: dict[tuple[str, str], float] = {}
    times_by_repeat: list[list[float]] = []
    for repeat in range(REPEATS):
        times: list[float] = []
        for qid, lists in contexts.items():
            start = time.perf_counter()
            per_candidate = [
                scorer.scores(question_of[qid], [text_of[d] for d in ids]) for ids in lists
            ]
            times.append(time.perf_counter() - start)
            if repeat == 0:
                for ids, values in zip(lists, per_candidate, strict=True):
                    scores.update(
                        {(question_of[qid], d): v for d, v in zip(ids, values, strict=True)}
                    )
        times_by_repeat.append(times)
    pairs = [sum(len(ids) for ids in lists) for lists in contexts.values()]
    cost: dict[str, object] = {
        **median_per_question(times_by_repeat),
        "n_questions": len(contexts),
        "pairs_per_question_mean": statistics.fmean(pairs),
        "n_unique_pairs": len(scores),
    }
    return cost, scores


def measure_c(
    scorer: CrossEncoderReranker,
    paths: DatasetPaths,
    question_of: dict[str, str],
    text_of_c: dict[str, str],
) -> dict[str, object]:
    """C の上位 50 通を採点し直して t_C を測り，既存の表との最大の差を求める．"""
    rows_of: dict[str, list[tuple[str, float]]] = defaultdict(list)
    with paths.ce_table("holdout").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row["qid"] in question_of:
                rows_of[row["qid"]].append((row["doc_id"], float(row["score"])))
    times_by_repeat: list[list[float]] = []
    max_diff = 0.0
    for repeat in range(REPEATS):
        times: list[float] = []
        for qid, pairs in rows_of.items():
            start = time.perf_counter()
            values = scorer.scores(question_of[qid], [text_of_c[d] for d, _ in pairs])
            times.append(time.perf_counter() - start)
            if repeat == 0:
                max_diff = max(
                    max_diff, *(abs(v - s) for v, (_, s) in zip(values, pairs, strict=True))
                )
        times_by_repeat.append(times)
    return {
        **median_per_question(times_by_repeat),
        "n_questions": len(rows_of),
        "pairs_per_question_mean": statistics.fmean(len(p) for p in rows_of.values()),
        "max_abs_diff_vs_existing_table": max_diff,
        "tolerance": C_TABLE_TOLERANCE,
        "matches_existing_table": max_diff <= C_TABLE_TOLERANCE,
    }


def main() -> None:
    """表の作成と t_P・t_C の測定を行い，cost.json を書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    load_config(
        Path(args.config)
    )  # 設定が読めること（モデル・revision は CrossEncoderReranker が固定）
    paths = dataset_paths(Path(args.data_dir), "enronqa")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    questions = load_questions(
        paths.questions.parent / QUESTION_FILES["holdout"], QUESTIONS_PER_INBOX
    )
    question_of = {q.qid: q.question for q in questions}
    p_rows = {r["qid"]: r for r in read_results(P_RUN / "results.jsonl") if r.get("error") is None}
    missing = [qid for qid in question_of if qid not in p_rows]
    if missing:
        raise RuntimeError(f"{len(missing)} questions have no successful row in {P_RUN}")
    contexts = {
        qid: [list(c["context_doc_ids"]) for c in p_rows[qid]["owner_answer"]["candidates"]]
        for qid in question_of
    }

    # 採点する文は live と同じ「題 + 改行 + 本文」．C の上位 50 通は全ての受信箱に散るので全受信箱を読む
    wanted_c = {
        json.loads(line)["doc_id"]
        for line in paths.ce_table("holdout").open(encoding="utf-8")
        if json.loads(line)["qid"] in question_of
    }
    inboxes = {d.split("/")[0] for lists in contexts.values() for ids in lists for d in ids}
    inboxes |= {d.split("/")[0] for d in wanted_c}
    text_of = {
        d["id"]: f"{d['title']}\n{d['content']}".strip()
        for inbox in sorted(inboxes)
        for d in read_inbox(paths, inbox)
    }
    scorer = CrossEncoderReranker()
    p_cost, scores = measure_p(scorer, question_of, contexts, text_of)
    c_cost = measure_c(scorer, paths, question_of, text_of)

    table = paths.ce_table_owner("holdout")
    tmp = table.with_suffix(".tmp")
    qid_of = {q: qid for qid, q in question_of.items()}
    with tmp.open("w", encoding="utf-8") as f:
        for (query, doc_id), score in scores.items():
            row = {"qid": qid_of[query], "query": query, "doc_id": doc_id, "score": score}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(table)
    (out_dir / "cost.json").write_text(
        json.dumps({"t_P": p_cost, "t_C": c_cost, "table": str(table)}, indent=1), encoding="utf-8"
    )
    print(json.dumps({"t_P": p_cost, "t_C": c_cost}, indent=1))


if __name__ == "__main__":
    main()
