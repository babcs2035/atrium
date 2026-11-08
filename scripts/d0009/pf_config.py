"""d0009 PF（Iteration 27）：config.yaml を，回答のプロンプトを direct にした主構成 P の 1 回分の実機の実行の条件にする（コメントを保つため文字列で書き換える）．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/pf_config.py <split> [--limit-per-inbox N]
split は h（holdout）・h2（holdout2）・dev（dev の全問）．--limit-per-inbox は smoke 用（dev の受信箱ごとの先頭 N 問）．
主構成は Iteration 22 の P（scripts/d0009/a3_config.py p）と同じで，変えるのは質問の分割と `llm.answer_prompt_format: direct` だけである
（生成モデル llm.expert_model_gpu は config.yaml の既定 llama3.1:8b のまま）．
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a3_config import COMMON, P_SETTINGS, set_key  # noqa: E402  (同じ場所の a3_config を使う)

CONFIG = Path("config.yaml")
SPLITS = {"h": "holdout", "h2": "holdout2", "dev": "dev"}


def main() -> None:
    """主構成（P）の質問の分割と回答のプロンプトの書式（direct）を config.yaml に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=sorted(SPLITS))
    parser.add_argument("--limit-per-inbox", type=int, default=None)
    args = parser.parse_args()
    text = CONFIG.read_text(encoding="utf-8")
    settings = {k: dict(v) for k, v in COMMON.items()}
    for section, items in P_SETTINGS.items():
        settings.setdefault(section, {}).update(items)
    settings["experiment"]["question_split"] = SPLITS[args.split]
    if args.limit_per_inbox is not None:
        settings["experiment"]["question_limit"] = str(args.limit_per_inbox)
    settings["llm"] = {"answer_prompt_format": "direct"}
    for section, items in settings.items():
        for key, value in items.items():
            text = set_key(text, section, key, value)
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
