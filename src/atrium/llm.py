"""Ollama の chat API の呼び出し．

公式の ollama クライアントではなく httpx で直接呼ぶ．プロンプト長（prompt_eval_count）や
プレフィル・デコードの所要時間を Ollama の応答からそのまま記録したいため．
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


async def chat(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    cfg: LlmConfig,
) -> LlmResult:
    """Ollama の /api/chat を呼び，生成文と計測値を返す．"""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": cfg.think,
        "options": {"num_predict": cfg.num_predict, "num_ctx": cfg.num_ctx},
    }
    response = await client.post(f"{base_url}/api/chat", json=payload, timeout=cfg.timeout_s)
    response.raise_for_status()
    body = response.json()
    return LlmResult(
        content=str(body["message"]["content"]),
        prompt_tokens=int(body.get("prompt_eval_count", 0)),
        output_tokens=int(body.get("eval_count", 0)),
        prefill_s=float(body.get("prompt_eval_duration", 0)) / NS_PER_S,
        decode_s=float(body.get("eval_duration", 0)) / NS_PER_S,
        total_s=float(body.get("total_duration", 0)) / NS_PER_S,
    )
