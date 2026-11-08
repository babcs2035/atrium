"""Ollama の chat API の呼び出し．

公式の ollama クライアントではなく httpx で直接呼ぶ．プロンプト長（prompt_eval_count）や
プレフィル・デコードの所要時間を Ollama の応答からそのまま記録したいため．

logprobs=True（p0007 A5．owner_answer の候補の特徴量）のときだけ，要求に `logprobs: true` を足し，生成したトークンの
対数確率の平均を返す．Ollama 0.35.1 で，付けても温度 0 の生成文が変わらないことを確かめた（2026-10-21．同じ要求の md5 が一致）．
top_logprobs=N（d0009 B3 Iteration 28．回答の検証の得点）を足すと，最初のトークンの上位 N 件の（トークン，対数確率）も返す
（`logprobs: true` と `top_logprobs: N` を要求に足す．指定しなければ要求は従来と同じ）．seed を指定すると `options.seed` を足す．
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from atrium.config import LlmConfig

NS_PER_S = 1e9


@dataclass(frozen=True)
class LlmResult:
    """1 回の生成の結果と計測値．"""

    content: str
    prompt_tokens: int
    output_tokens: int
    prefill_s: float
    decode_s: float
    total_s: float
    # logprobs=True で求めたときだけ：生成したトークンの対数確率の平均（Ollama が返さなければ None）と，その数
    avg_logprob: float | None = None
    n_logprob_tokens: int = 0
    # top_logprobs を求めたときだけ：最初に生成したトークンの上位候補（トークンの文字列，対数確率）．無ければ空
    first_top_logprobs: tuple[tuple[str, float], ...] = ()


def mean_logprob(entries: object) -> tuple[float | None, int]:
    """Ollama の応答の `logprobs`（トークンごとの {token, logprob, ...} の列）から（平均，トークン数）を返す．無ければ (None, 0)．"""
    if not isinstance(entries, list):
        return None, 0
    values = [float(e["logprob"]) for e in entries if isinstance(e, dict) and "logprob" in e]
    if not values:
        return None, 0
    return sum(values) / len(values), len(values)


def first_token_top_logprobs(entries: object) -> tuple[tuple[str, float], ...]:
    """Ollama の応答の `logprobs` の最初のトークンの `top_logprobs`（{token, logprob, ...} の列）→（トークン，対数確率）の組．無ければ空．"""
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return ()
    top = entries[0].get("top_logprobs")
    if not isinstance(top, list):
        return ()
    return tuple(
        (str(e["token"]), float(e["logprob"]))
        for e in top
        if isinstance(e, dict) and "token" in e and "logprob" in e
    )


async def chat(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    cfg: LlmConfig,
    logprobs: bool = False,
    top_logprobs: int | None = None,
    seed: int | None = None,
) -> LlmResult:
    """Ollama の /api/chat を呼び，生成文と計測値を返す（logprobs=True なら対数確率の平均も，top_logprobs 指定なら最初のトークンの上位候補も）．"""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": cfg.think,
        "options": {
            "num_predict": cfg.num_predict,
            "num_ctx": cfg.num_ctx,
            "temperature": cfg.temperature,
        },
    }
    if seed is not None:
        payload["options"]["seed"] = seed
    if logprobs or top_logprobs is not None:
        payload["logprobs"] = True
    if top_logprobs is not None:
        payload["top_logprobs"] = top_logprobs
    response = await client.post(f"{base_url}/api/chat", json=payload, timeout=cfg.timeout_s)
    response.raise_for_status()
    body = response.json()
    wants_logprobs = logprobs or top_logprobs is not None
    avg_logprob, n_logprob_tokens = (
        mean_logprob(body.get("logprobs")) if wants_logprobs else (None, 0)
    )
    return LlmResult(
        content=str(body["message"]["content"]),
        prompt_tokens=int(body.get("prompt_eval_count", 0)),
        output_tokens=int(body.get("eval_count", 0)),
        prefill_s=float(body.get("prompt_eval_duration", 0)) / NS_PER_S,
        decode_s=float(body.get("eval_duration", 0)) / NS_PER_S,
        total_s=float(body.get("total_duration", 0)) / NS_PER_S,
        avg_logprob=avg_logprob,
        n_logprob_tokens=n_logprob_tokens,
        first_top_logprobs=(
            first_token_top_logprobs(body.get("logprobs")) if top_logprobs is not None else ()
        ),
    )
