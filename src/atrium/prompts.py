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

# p0007 I2（aggregate=abstain）：答えがメールに無いときは UNKNOWN と答えさせる．元の文面に 1 文だけ足し，ほかは変えない
UNKNOWN_TOKEN = "UNKNOWN"
ENRON_ABSTAIN_SENTENCE = (
    f"If the emails do not contain the answer, write {UNKNOWN_TOKEN} as the answer."
)
ENRON_ABSTAIN_TEMPLATE = USER_PROMPT_TEMPLATES["enronqa"].replace(
    "\n\n---\n\nFollow the following format.",
    f" {ENRON_ABSTAIN_SENTENCE}\n\n---\n\nFollow the following format.",
    1,
)

# p0007 I2（aggregate=llm_select）：質問者の LLM が，各受信箱の回答から 1 つを選ぶ（メールの本文は渡さず，回答だけを渡す）
SELECT_SYSTEM_PROMPT = "You choose the most plausible answer to a question about emails."
SELECT_USER_TEMPLATE = (
    "Several mailboxes each answered the question below using only their own emails. "
    "Choose the answer most likely to be correct. Answers saying the information is missing "
    f"or {UNKNOWN_TOKEN} are unlikely to be correct unless every answer says so.\n\n"
    "Question: {question}\n\n{candidates}\n\n"
    "Reply with the number of the best answer only."
)


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
    allow_unknown: bool = False,
) -> list[dict[str, str]]:
    """Ollama の chat API に渡す messages を作る．allow_unknown は EnronQA だけで，答えが無ければ UNKNOWN と答えさせる．"""
    if allow_unknown and dataset != "enronqa":
        raise ValueError("allow_unknown is only defined for the enronqa prompt")
    template = ENRON_ABSTAIN_TEMPLATE if allow_unknown else USER_PROMPT_TEMPLATES[dataset]
    user = template.format(
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


_UNKNOWN_ANSWER = re.compile(rf"^\W*{UNKNOWN_TOKEN}\b", re.IGNORECASE)


def is_unknown_answer(answer: str) -> bool:
    """回答が UNKNOWN で始まるか（引用符・記号・大小文字は無視する．「UNKNOWN. The emails do not say」も該当）．"""
    return _UNKNOWN_ANSWER.match(answer) is not None


def build_select_messages(question: str, answers: Sequence[str]) -> list[dict[str, str]]:
    """llm_select の messages：候補の回答に 1 から番号を振って並べる．"""
    candidates = "\n".join(f"Answer {i}: {a}" for i, a in enumerate(answers, start=1))
    user = SELECT_USER_TEMPLATE.format(question=question, candidates=candidates)
    return [
        {"role": "system", "content": SELECT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def extract_selected_index(llm_output: str, n_candidates: int) -> int | None:
    """llm_select の出力から，最初に現れる整数を 0 始まりの候補の番号にして返す（範囲外・整数が無ければ None）．"""
    found = re.search(r"\d+", llm_output)
    if found is None:
        return None
    index = int(found.group()) - 1
    return index if 0 <= index < n_candidates else None


def extract_choice(llm_output: str) -> str | None:
    """LLM の出力から選択肢（A〜D）を取り出す．取り出せなければ None を返す．"""
    tail = llm_output.split('"answer_choice": "')[-1].strip()
    for pattern in _ANSWER_PATTERNS:
        found = pattern.findall(tail)
        if found:
            return str(found[0]).upper()
    return None
