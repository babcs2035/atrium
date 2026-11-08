"""d0009 Iteration 27 の診断：回答のプロンプトの形（穴埋め fill_in・直接 direct・直接＋理由 direct_cot）ごとに，生成が上限まで続くかを gpu4 で数える．

判定には使わない（正誤は採点しない）．見るのは出力のトークン数が `num_predict` に達した割合，`Answer:` の行の有無，
出力が `Emails:`・`Document [` の書き写しで始まる割合，取り出した回答の文字数である．

- 質問は Iteration 26 の G4 の dev300 の途中までの実行（`results/d0009_m1_dev300_g4_partial`）の先頭 N 問．
  文脈は base の dev（`results/p0007_a6_dev`）の同じ問の 1 番目の候補の `context_doc_ids`（5 通．順序も同じ）を，
  データの chunk から復元する（題と本文．`prompts.format_context` が使う欄）．
- 生成の設定は config.yaml の `llm`（num_predict・num_ctx・temperature・think）と同じ．呼び出しは `atrium.llm.chat`．
- direct・direct_cot の文面は下の定数で固定し，journal の Iteration 27 の事前登録に同じ文面を書く．

使い方（リポジトリの直下．gpu4．判定の container が 127.0.0.1:11436 で動いていること）:
    uv run python scripts/d0009/pf_diag.py --model gemma4-12b-it-qat --n 40 --out results/d0009_pf_diag
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from pathlib import Path

import httpx

from atrium import llm, prompts
from atrium.config import load_config
from atrium.data_enronqa import read_inbox
from atrium.judge import question_texts
from atrium.paths import dataset_paths
from atrium.store import RetrievedDoc

ENRON_TASK_SENTENCE = (
    "Given emails and a question about one of those emails, write the answer to that question "
    "in a single sentence."
)
DIRECT_TEMPLATE = (
    ENRON_TASK_SENTENCE + "\n\nEmails:\n{context}\n\nQuestion: {question}\n\n"
    "Reply with only the answer sentence."
)
DIRECT_COT_TEMPLATE = (
    ENRON_TASK_SENTENCE + "\n\nEmails:\n{context}\n\nQuestion: {question}\n\n"
    "First explain your reasoning step by step in a few sentences. Then write the answer as a "
    'single sentence on a last line that starts with "Answer:".'
)
FORMS = ("fill_in", "direct", "direct_cot")
OLLAMA_URL = "http://127.0.0.1:11436"
# 判定の container の OLLAMA_NUM_PARALLEL と同じ
CONCURRENCY = 2
COPY_PREFIXES = ("Emails:", "Document [")
ECHOED = "[omitted: the model echoed email bodies instead of answering]"


def build_messages(form: str, question: str, docs: list[RetrievedDoc]) -> list[dict[str, str]]:
    """形ごとの messages（system は 3 つの形で同じ）．fill_in は主構成の `prompts.build_messages` そのもの．"""
    if form == "fill_in":
        return prompts.build_messages("enronqa", question, docs, {})
    template = DIRECT_TEMPLATE if form == "direct" else DIRECT_COT_TEMPLATE
    user = template.format(context=prompts.format_context(docs), question=question)
    return [
        {"role": "system", "content": prompts.SYSTEM_PROMPTS["enronqa"]},
        {"role": "user", "content": user},
    ]


async def run(args: argparse.Namespace) -> None:
    """診断の生成を流し，形ごとの集計を書く．"""
    cfg = load_config()
    paths = dataset_paths(Path(args.data_dir), "enronqa")
    questions = question_texts(paths)
    qids = []
    with Path("results/d0009_m1_dev300_g4_partial/results.jsonl").open(encoding="utf-8") as f:
        for line in f:
            qids.append(json.loads(line)["qid"])
    qids = qids[: args.n]
    base = {}
    with Path("results/p0007_a6_dev/results.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row["qid"] in qids and row.get("error") is None:
                base[row["qid"]] = row["owner_answer"]["candidates"][0]
    texts: dict[str, dict[str, str]] = {}
    items = []
    for qid in qids:
        cand = base[qid]
        inbox = cand["source"]
        if inbox not in texts:
            texts[inbox] = {d["id"]: d for d in read_inbox(paths, inbox)}
        doc_ids = cand["context_doc_ids"]
        if isinstance(doc_ids, str):
            doc_ids = json.loads(doc_ids.replace("'", '"'))
        docs = [
            RetrievedDoc(d, texts[inbox][d]["title"], texts[inbox][d]["content"], 0.0)
            for d in doc_ids
        ]
        for form in FORMS:
            items.append((qid, form, build_messages(form, questions[qid], docs)))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    records = []
    async with httpx.AsyncClient() as client:

        async def one(qid: str, form: str, messages: list[dict[str, str]]) -> None:
            async with semaphore:
                try:
                    result = await llm.chat(client, OLLAMA_URL, args.model, messages, cfg.llm)
                except httpx.HTTPError as exc:
                    records.append({"qid": qid, "form": form, "error": repr(exc)})
                    return
            content = result.content
            answer = prompts.extract_free_answer(content)
            copied = content.lstrip().startswith(COPY_PREFIXES)
            # メール本文は results/ に残さない（sanitize_results.py と同じ方針．backlog B57）
            shown = ECHOED if copied else content[:160]
            records.append(
                {
                    "qid": qid,
                    "form": form,
                    "output_tokens": result.output_tokens,
                    "prompt_tokens": result.prompt_tokens,
                    "hit_cap": result.output_tokens >= cfg.llm.num_predict,
                    "has_answer_line": prompts._FREE_ANSWER.search(content) is not None,
                    "starts_with_copy": copied,
                    "answer_chars": len(answer),
                    "total_s": result.total_s,
                    "head": shown,
                    "answer": ECHOED if copied else answer[:300],
                }
            )

        await asyncio.gather(*(one(*item) for item in items))

    summary = {"model": args.model, "n_questions": len(qids), "num_predict": cfg.llm.num_predict}
    for form in FORMS:
        rows = [r for r in records if r["form"] == form and "error" not in r]
        summary[form] = {
            "n": len(rows),
            "errors": sum(1 for r in records if r["form"] == form and "error" in r),
            "hit_cap_rate": sum(r["hit_cap"] for r in rows) / len(rows) if rows else None,
            "answer_line_rate": sum(r["has_answer_line"] for r in rows) / len(rows)
            if rows
            else None,
            "copy_start_rate": sum(r["starts_with_copy"] for r in rows) / len(rows)
            if rows
            else None,
            "median_output_tokens": statistics.median(r["output_tokens"] for r in rows)
            if rows
            else None,
            "median_answer_chars": statistics.median(r["answer_chars"] for r in rows)
            if rows
            else None,
            "median_total_s": statistics.median(r["total_s"] for r in rows) if rows else None,
        }
    tag = args.model.replace(":", "_")
    with (out_dir / f"diag_{tag}.jsonl").open("w", encoding="utf-8") as f:
        for r in sorted(records, key=lambda r: (r["form"], r["qid"])):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / f"summary_{tag}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


def main() -> None:
    """引数を読んで診断を流す．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--data-dir", default="artifacts")
    parser.add_argument("--out", default="results/d0009_pf_diag")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
