"""d0009 A3（Iteration 22）のゲート G-E・G-J・G2 を gpu4 で計算する（結果は results/d0009_a3_rerun_variance/ の JSON）．

使い方（リポジトリの直下で．GPU と，G-J は Ollama（判定モデル）が要る）:
    uv run python scripts/d0009/a3_gates.py ge   --data-dir artifacts          # G-E：holdout と同じ手順の test の 100 問の埋め込みが既存の値と一致するか
    uv run python scripts/d0009/a3_gates.py g2   --data-dir artifacts          # G2：gpu4 で採点し直した R3′ の選択が元のオフラインの選択と一致するか
    uv run python scripts/d0009/a3_gates.py gj   --data-dir artifacts --ollama-url http://127.0.0.1:11436   # G-J：判定モデルの一致率
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from atrium.analysis import read_results
from atrium.benchmarks import load_questions
from atrium.config import load_config
from atrium.data_enronqa import QUESTION_FILES, read_gold
from atrium.paths import dataset_paths

OUT = Path("results/d0009_a3_rerun_variance")
RESULTS = Path("results")
A6_TEST = RESULTS / "p0007_a6_test"
A6_R3P = RESULTS / "p0007_a6_test_R3p"
A5_C1 = RESULTS / "p0007_a5_c1_rrf_ce"
N_EMBED_CHECK = 100
N_JUDGE_CHECK = 300
SEED = 12
COSINE_THRESHOLD = 0.9999
SELECT_THRESHOLD = 0.99
JUDGE_THRESHOLD = 0.95


def gate_embedding(data_dir: Path) -> dict[str, Any]:
    """G-E：test の先頭 100 問のクエリ埋め込みを holdout と同じ手順（ArcticEncoder.encode_queries）で計算し，既存の値との余弦を出す．"""
    from atrium.encoders import ArcticEncoder

    cfg = load_config(Path("config.yaml"))
    e = cfg.data.require_enronqa()
    paths = dataset_paths(data_dir, "enronqa")
    questions = load_questions(paths.questions.parent / QUESTION_FILES["test"])[:N_EMBED_CHECK]
    ids: list[str] = json.loads(paths.query_ids.read_text(encoding="utf-8"))
    row = {qid: i for i, qid in enumerate(ids)}
    existing = np.load(paths.query_embeddings(e.encoder), mmap_mode="r")
    new = ArcticEncoder(e.encoder, e.embed_batch_size).encode_queries(
        [q.question for q in questions]
    )
    old = np.stack([np.asarray(existing[row[q.qid]], dtype=np.float32) for q in questions])
    cosine = (new * old).sum(axis=1) / (np.linalg.norm(new, axis=1) * np.linalg.norm(old, axis=1))
    return {
        "gate": "G-E",
        "n": len(questions),
        "min_cosine": float(cosine.min()),
        "mean_cosine": float(cosine.mean()),
        "threshold": COSINE_THRESHOLD,
        "passed": bool(cosine.min() >= COSINE_THRESHOLD),
    }


def gate_selection(data_dir: Path) -> dict[str, Any]:
    """G2：A6 の test の候補を gpu4 の select-features で採点し直し，R3′ の選んだ候補の番号を元のオフラインの選択と比べる．"""
    from atrium.data_enronqa import read_inbox
    from atrium.judge import question_texts
    from atrium.requester import CrossEncoderReranker
    from atrium.select_eval import CE_FEATURES, cross_encoder_features, evaluate, write_derived_run

    paths = dataset_paths(data_dir, "enronqa")
    work = OUT / "g2_work" / "p0007_a6_test"
    work.mkdir(parents=True, exist_ok=True)
    for name in ("results.jsonl", "candidate_judgements.jsonl", "judgements.jsonl"):
        shutil.copy(A6_TEST / name, work / name)
    if not (work / CE_FEATURES).exists():
        rows = [r for r in read_results(work / "results.jsonl") if r.get("error") is None]
        inboxes = {
            str(c["source"])
            for r in rows
            for c in (r.get("owner_answer") or {}).get("candidates") or []
        }
        text_of = {
            d["id"]: f"{d['title']}\n{d['content']}".strip()
            for inbox in sorted(inboxes)
            for d in read_inbox(paths, inbox)
        }
        features = cross_encoder_features(
            rows, question_texts(paths), text_of, CrossEncoderReranker().scores
        )
        with (work / CE_FEATURES).open("w", encoding="utf-8") as f:
            for item in features:
                f.write(json.dumps(item) + "\n")
    _, outcomes = evaluate(work, ["R3"], work / CE_FEATURES, True)
    derived = OUT / "g2_work" / "p0007_a6_test_R3p_gpu4"
    write_derived_run(work, outcomes["R3"], "R3", derived, True)
    new = {r["qid"]: r.get("chosen_index") for r in read_results(derived / "results.jsonl")}
    old = {r["qid"]: r.get("chosen_index") for r in read_results(A6_R3P / "results.jsonl")}
    shared = sorted(set(new) & set(old))
    same = sum(new[q] == old[q] for q in shared)
    old_correct = {
        j["qid"]: j["correct"] for j in map(json.loads, (A6_R3P / "judgements.jsonl").open())
    }
    new_correct = {
        j["qid"]: j["correct"] for j in map(json.loads, (derived / "judgements.jsonl").open())
    }
    return {
        "gate": "G2",
        "n": len(shared),
        "agree": same / len(shared),
        "accuracy_original": sum(bool(old_correct.get(q)) for q in shared) / len(shared),
        "accuracy_gpu4": sum(bool(new_correct.get(q)) for q in shared) / len(shared),
        "threshold": SELECT_THRESHOLD,
        "passed": bool(same / len(shared) >= SELECT_THRESHOLD),
    }


def gate_judge(data_dir: Path, urls: list[str]) -> dict[str, Any]:
    """G-J（記述）：A6 の R3′ と C1-RRF-CE の test の回答から 300 件ずつ（種 12）を gpu4 で採点し直し，元の判定との一致率を出す．"""
    from atrium.judge import JudgeItem, email_lookup, judge_items, question_texts

    cfg = load_config(Path("config.yaml"))
    model = cfg.data.require_enronqa().judge_model
    paths = dataset_paths(data_dir, "enronqa")
    gold = read_gold(paths)
    emails = email_lookup(cfg, paths)
    questions = question_texts(paths)
    # 元の判定：A6 の R3′ は派生の実行の judgements.jsonl，C1-RRF-CE は実行自身の judgements.jsonl
    r3p_choice = {r["qid"]: r.get("chosen_index") for r in read_results(A6_R3P / "results.jsonl")}
    r3p_answers: dict[str, str] = {}
    for r in read_results(A6_TEST / "results.jsonl"):
        cands = (r.get("owner_answer") or {}).get("candidates") or []
        index = r3p_choice.get(r["qid"])
        if r.get("error") is None and index is not None and index < len(cands):
            r3p_answers[r["qid"]] = str(cands[index].get("answer", ""))
    c1_answers = {
        r["qid"]: r.get("final_answer", "")
        for r in read_results(A5_C1 / "results.jsonl")
        if r.get("error") is None and "final_answer" in r
    }
    result: dict[str, Any] = {"gate": "G-J", "model": model, "threshold": JUDGE_THRESHOLD}
    for name, answers, judged_path in (
        ("R3p", r3p_answers, A6_R3P / "judgements.jsonl"),
        ("C1_RRF_CE", c1_answers, A5_C1 / "judgements.jsonl"),
    ):
        original = {
            j["qid"]: j["correct"] for j in map(json.loads, judged_path.open(encoding="utf-8"))
        }
        qids = sorted(q for q in answers if original.get(q) is not None)
        picked = [
            qids[int(i)] for i in np.random.default_rng(SEED).permutation(len(qids))[:N_JUDGE_CHECK]
        ]
        items = [
            JudgeItem(q, emails[gold[q]["path"]], questions[q], gold[q]["gold_answer"], answers[q])
            for q in picked
        ]
        verdicts = asyncio.run(judge_items(items, urls, model, cfg))
        agree = sum(verdicts[q][0] == original[q] for q in picked)
        result[name] = {"n": len(picked), "agree": agree / len(picked)}
    result["passed"] = all(result[k]["agree"] >= JUDGE_THRESHOLD for k in ("R3p", "C1_RRF_CE"))
    return result


def main() -> None:
    """ゲートを 1 つ計算して JSON に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=["ge", "g2", "gj"])
    parser.add_argument("--data-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--ollama-url", action="append", default=[])
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.gate == "ge":
        result = gate_embedding(args.data_dir)
    elif args.gate == "g2":
        result = gate_selection(args.data_dir)
    else:
        result = gate_judge(args.data_dir, args.ollama_url)
    (OUT / f"{result['gate'].lower()}.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8"
    )
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
