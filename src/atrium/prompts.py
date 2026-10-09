"""回答生成のプロンプトと，LLM の出力から選択肢を取り出す処理．

プロンプトの文面と回答の抽出規則は RAGRoute（https://github.com/sacs-epfl/ragroute，MIT License，
Copyright (c) 2025 SaCS-EPFL）の `ragroute/config.py` と `ragroute/benchmark.py` から移植した．
選択肢の書式だけは MedRAG（`src/medrag.py`）と同じ「A. ...」の行にしている
（RAGRoute は dict を liquid でそのまま描画しており，書式が MedRAG の原典と異なるため）．

EnronQA（p0004）の回答のプロンプトは，原論文（Ryan et al., arXiv:2505.00263，CC BY 4.0）の付録 B.5 の
「QA With Email Prompt」を，メールを k 通（既定 5）渡す形に直したものである．回答は "Answer:" の後の 1 文を取り出す．
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from atrium.config import DatasetName
from atrium.store import RetrievedDoc

SYSTEM_PROMPTS: dict[DatasetName, str] = {
    "medrag": (
        "You are a helpful medical expert, and your task is to answer a multi-choice medical "
        "question using the relevant documents.\n"
        "Please first think step-by-step and then choose the answer from the provided options.\n"
        'Organize your output in a json formatted as Dict{"step_by_step_thinking": '
        'Str(explanation), "answer_choice": Str{A/B/C/...}}.\n'
        "Your responses will be used for research purposes only, so please have a definite answer."
    ),
    "feb4rag": (
        "You are a helpful assistant helping to answer user requests based on the provided search "
        "result.\n"
        "Your responses should directly address the user's request and must be based on the "
        "information obtained from the provided search results.\n"
        "You are forbidden to create new information that is not supported by these results.\n"
        "You must attribute your response to the source from the search results by including "
        "citations, for example, [1]."
    ),
    "enronqa": "You answer questions about emails.",
}

USER_PROMPT_TEMPLATES: dict[DatasetName, str] = {
    "medrag": (
        "Here are the relevant documents:\n{context}\n\n"
        "Here is the question:\n{question}\n\n"
        "Here are the potential choices:\n{options}\n\n"
        "Please think step-by-step and generate your output in json formatted as "
        'Dict{{"step_by_step_thinking": Str(explanation), "answer_choice": Str{{A/B/C/...}}}}:'
    ),
    "feb4rag": ("Here are the search results:\n{context}\n\nHere is the question:\n{question}"),
    "enronqa": (
        "Given emails and a question about one of those emails, write the answer to that question "
        "in a single sentence.\n\n---\n\nFollow the following format.\n\n"
        "Emails: The emails we want to answer a question about\n"
        "Question: The question we want to answer about the email\n"
        "Reasoning: Let's think step by step in order to ${{produce the answer}}\n"
        "Answer: The answer to the question\n\n---\n\n"
        "Emails: {context}\n"
        "Question: {question}\n"
        "Reasoning: Let's think step by step in order to"
    ),
}


def format_options(options: Mapping[str, str]) -> str:
    """選択肢を MedRAG と同じ「A. 本文」の行へ整形する．"""
    return "\n".join(f"{key}. {options[key]}" for key in sorted(options))


def format_context(docs: Sequence[RetrievedDoc]) -> str:
    """断片を RAGRoute と同じ「Document [i] (Title: ...) 本文」の行へ整形する．"""
    if not docs:
        return ""
    return "\n".join(
        f"Document [{i}] (Title: {doc.title or f'Doc {i}'}) {doc.content}"
        for i, doc in enumerate(docs)
    )


def build_messages(
    dataset: DatasetName,
    question: str,
    docs: Sequence[RetrievedDoc],
    options: Mapping[str, str],
) -> list[dict[str, str]]:
    """Ollama の chat API に渡す messages を作る．"""
    user = USER_PROMPT_TEMPLATES[dataset].format(
        context=format_context(docs), question=question, options=format_options(options)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPTS[dataset]},
        {"role": "user", "content": user},
    ]


# RAGRoute の locate_answer と同じ順序で試す正規表現（先に一致したものを採用する）
_ANSWER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"^\s*(A|B|C|D)$",
        r"^\s*(A|B|C|D) or",
        r"^\s*(A|B|C|D) and",
        r"^\s*(A|B|C|D)/",
        r"^\s*(A|B|C|D),",
        r"[Oo]ption (A|B|C|D)",
        r":\s*(A|B|C|D)",
        r"^\s*(A|B|C|D)\.",
        r"^\s*(A|B|C|D)\"",
        r"^\s*(A|B|C|D):",
    )
)


_FREE_ANSWER = re.compile(r"^\s*\**Answer\**\s*:\s*(.+)$", re.MULTILINE | re.IGNORECASE)


def extract_free_answer(llm_output: str) -> str:
    """自由記述の回答（EnronQA）を取り出す：最後の "Answer:" の行の内容．無ければ出力の全体を返す．"""
    found = _FREE_ANSWER.findall(llm_output)
    return str(found[-1]).strip() if found else llm_output.strip()


def extract_choice(llm_output: str) -> str | None:
    """LLM の出力から選択肢（A〜D）を取り出す．取り出せなければ None を返す．"""
    tail = llm_output.split('"answer_choice": "')[-1].strip()
    for pattern in _ANSWER_PATTERNS:
        found = pattern.findall(tail)
        if found:
            return str(found[0]).upper()
    return None
