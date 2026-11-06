"""d0009 A3（Iteration 22）：config.yaml を P2P の主構成 R3′（P）または中央 C1-RRF-CE（C）の条件にする（コメントを保つため文字列で書き換える）．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/a3_config.py p [--limit-per-inbox N]
    uv run python scripts/d0009/a3_config.py c [--limit-per-inbox N]
P は results/p0007_a6_test と，C は results/p0007_a5_c1_rrf_ce と同じ条件で，質問の分割を holdout にしたもの．
C は質問者の LLM を wafl509（192.168.15.109）に置き，その台を experts・gpu_workers から外す．
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

CONFIG = Path("config.yaml")
LLM_HOST = "192.168.15.109"

COMMON = {
    "experiment": {
        "dataset": "enronqa",
        "question_split": "holdout",
        "http_pool": "per_node",
        "parallel": "30",
    },
    "retrieval": {"fusion": "rrf"},
}
P_SETTINGS = {
    "experiment": {"routing": "bm25_probe", "answer_mode": "owner_answer"},
    "answer": {"owner_m": "3", "save_candidates": "true"},
}
C_SETTINGS = {
    "experiment": {"routing": "central_local", "answer_mode": "snippet_return"},
    "retrieval": {"merge": "cross_encoder", "ce_scores": "table"},
}


def set_key(text: str, section: str, key: str, value: str) -> str:
    """トップレベルの節の中の，字下げ 2 個のキー 1 つの値を置き換える（節の中に無ければ節の末尾に足す）．"""
    match = re.search(rf"(?m)^{section}:\n((?:(?:  .*|[ \t]*)\n)*)", text)
    if match is None:
        raise ValueError(f"section {section} not found")
    body = match.group(1)
    new_body, n = re.subn(rf"(?m)^(  {key}: ).*$", rf"\g<1>{value}", body, count=1)
    if n == 0:
        content = body.rstrip("\n")
        new_body = f"{content}\n  {key}: {value}\n" + body[len(content) + 1 :]
    return text[: match.start(1)] + new_body + text[match.end(1) :]


def main() -> None:
    """P または C の条件を config.yaml に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("config", choices=["p", "c"])
    parser.add_argument(
        "--limit-per-inbox", type=int, default=None, help="smoke 用（受信箱ごとの先頭の数）"
    )
    args = parser.parse_args()
    text = CONFIG.read_text(encoding="utf-8")
    settings = {k: dict(v) for k, v in COMMON.items()}
    for section, items in (P_SETTINGS if args.config == "p" else C_SETTINGS).items():
        settings.setdefault(section, {}).update(items)
    if args.limit_per_inbox is not None:
        settings["experiment"]["question_limit"] = str(args.limit_per_inbox)
    for section, items in settings.items():
        for key, value in items.items():
            text = set_key(text, section, key, value)
    if args.config == "c":
        # experts・gpu_workers の最後の 1 台（wafl509）を外し，質問者の LLM に置く（p0007_a5_c1_rrf_ce と同じ）
        host_line = f'    - {{host: "{LLM_HOST}"}}\n'
        if text.count(host_line) != 2:
            raise ValueError("expected wafl509 once in experts and once in gpu_workers")
        text = text.replace(host_line, "")
        anchor = '  requester: {host: "wafl-ctrl5"}\n'
        text = text.replace(anchor, anchor + f'  requester_llm: {{host: "{LLM_HOST}"}}\n', 1)
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
