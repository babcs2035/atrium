"""results.jsonl の `answer` から，LLM が繰り返したメール本文（`Emails:` から区切り線まで）を取り除く（p0004 §2.3・backlog B57）．

使い方（commit の前に，EnronQA の実行ごとに 1 回）:
    uv run python scripts/tasks/sanitize_results.py results/<run_id> [...]
`final_answer` は，メール本文を出力した行だけを省略の印にする．`gold`・`top_doc_ids` などの他のフィールドと判定は変えない．冪等．
`answer.save_candidates`（p0007 A5）の実行の `owner_answer.candidates[].answer`（各候補の "Answer:" の後ろ）も，`final_answer` と
同じ規則で整形する（採点の後に通す．候補ごとの判定 candidate_judgements.jsonl は変えない）．
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# プロンプトの形式（Emails: <文脈> ===== Question: ...）で，最後の区切り線から Question: の直前までを文脈とみなす
ECHOED_CONTEXT = re.compile(r"Emails:.*\n={5,}\n(?=Question:)", re.DOTALL)
OMITTED = "Emails: [omitted: email bodies are not stored in results/]\n"
SEPARATOR = "====="
ECHOED_FINAL = "[omitted: the model echoed email bodies instead of answering]"


def sanitize_answer(answer: str) -> str:
    """メール本文の繰り返しを除いた回答．

    形式どおりに取り切れない（Question: が続かないなど）ときは，最後の `Answer:` 以降だけを残し，
    それも無ければ全体を省略する（本文を残さないことを，回答の全文より優先する）．
    """
    cleaned = ECHOED_CONTEXT.sub(OMITTED, answer)
    if SEPARATOR not in cleaned:
        return cleaned
    marker = cleaned.rfind("Answer:")
    tail = cleaned[marker:] if marker >= 0 else ""
    return OMITTED + tail if SEPARATOR not in tail else OMITTED


def sanitize_file(path: Path) -> int:
    """results.jsonl を書き換え，整形した行の数を返す．"""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    changed = 0
    for row in rows:
        answer = row.get("answer")
        if isinstance(answer, str) and SEPARATOR in answer:
            row["answer"] = sanitize_answer(answer)
            changed += 1
        final = row.get("final_answer")
        if isinstance(final, str) and SEPARATOR in final:
            # 答えの代わりに文脈を出力した行（判定は不正解）．判定済みなので，本文を残さない印に置き換える
            row["final_answer"] = ECHOED_FINAL
            changed += 1
        owner = row.get("owner_answer")
        candidates = owner.get("candidates") if isinstance(owner, dict) else None
        for cand in candidates if isinstance(candidates, list) else []:
            text = cand.get("answer")
            if isinstance(text, str) and SEPARATOR in text:
                cand["answer"] = ECHOED_FINAL
                changed += 1
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    return changed


if __name__ == "__main__":
    for run in sys.argv[1:]:
        print(run, sanitize_file(Path(run) / "results.jsonl"))
