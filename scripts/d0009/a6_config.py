"""d0009 A6（Iteration 29）：config.yaml を負荷の測定の条件 P・C・S にする（コメントを保つため文字列で書き換える）．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/a6_config.py {p,c,s} [--limit-per-inbox N]
- 共通：H（holdout）・受信箱ごとの先頭 2 問（288 問）・parallel 30・per_node の接続の池・RRF（a3_config.py の COMMON）．
- P：P2P の主構成（a3_config.py の P）＋ answer.aggregate: cross_encoder・retrieval.ce_scores: table（候補の文脈の得点を表から引く）．
- C：C1-RRF-CE（a3_config.py の C．質問者の LLM は wafl509）．
- S：C の生成を GPU の専門家 wafl500〜509 に分ける（experiment.central_generation: gpu_nodes．requester_llm は置かず，wafl509 は専門家のまま）．
"""

from __future__ import annotations

import argparse
from pathlib import Path

from a3_config import C_SETTINGS, COMMON, CONFIG, LLM_HOST, P_SETTINGS, set_key

QUESTION_LIMIT = "2"
A6_COMMON = {"experiment": {**COMMON["experiment"], "question_limit": QUESTION_LIMIT}}
P_EXTRA = {"answer": {"aggregate": "cross_encoder"}, "retrieval": {"ce_scores": "table"}}
S_EXTRA = {"experiment": {"central_generation": "gpu_nodes"}}


def merge_settings(*parts: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """節ごとのキーと値の辞書を順に重ねる（後のものが勝つ）．"""
    out: dict[str, dict[str, str]] = {}
    for part in parts:
        for section, items in part.items():
            out.setdefault(section, {}).update(items)
    return out


def main() -> None:
    """P・C・S のいずれかの条件を config.yaml に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("condition", choices=["p", "c", "s"])
    parser.add_argument(
        "--limit-per-inbox", type=int, default=None, help="smoke 用（受信箱ごとの先頭の数）"
    )
    args = parser.parse_args()
    parts = {
        "p": [COMMON, A6_COMMON, P_SETTINGS, P_EXTRA],
        "c": [COMMON, A6_COMMON, C_SETTINGS],
        "s": [COMMON, A6_COMMON, C_SETTINGS, S_EXTRA],
    }[args.condition]
    settings = merge_settings(*parts)
    if args.limit_per_inbox is not None:
        settings["experiment"]["question_limit"] = str(args.limit_per_inbox)
    text = CONFIG.read_text(encoding="utf-8")
    for section, items in settings.items():
        for key, value in items.items():
            text = set_key(text, section, key, value)
    if args.condition == "c":
        # experts・gpu_workers の最後の 1 台（wafl509）を外し，質問者の LLM に置く（Iteration 22 の C と同じ）
        host_line = f'    - {{host: "{LLM_HOST}"}}\n'
        if text.count(host_line) != 2:
            raise ValueError("expected wafl509 once in experts and once in gpu_workers")
        text = text.replace(host_line, "")
        anchor = '  requester: {host: "wafl-ctrl5"}\n'
        text = text.replace(anchor, anchor + f'  requester_llm: {{host: "{LLM_HOST}"}}\n', 1)
    Path(CONFIG).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
