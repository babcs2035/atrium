"""d0009 M1（Iteration 26）：config.yaml を，生成モデルの比較の 1 回分の実機の実行の条件にする（コメントを保つため文字列で書き換える）．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/m1_config.py <split> <model>
split は h（holdout）・h2（holdout2）・dev300（dev の受信箱ごとの先頭 2 問．150 受信箱で 300 問）．
model は llm.expert_model_gpu に書くモデルの識別子（例: llama3.1:8b・gemma4-12b-it-qat:latest）．
主構成は Iteration 22 の P（scripts/d0009/a3_config.py p）と同じで，変えるのは質問の分割と生成モデルだけである．
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a3_config import COMMON, P_SETTINGS, set_key  # noqa: E402  (同じ場所の a3_config を使う)

CONFIG = Path("config.yaml")
# dev300：dev の分割の各受信箱の先頭 2 問（150 受信箱）
DEV300_LIMIT_PER_INBOX = 2
SPLITS = {"h": "holdout", "h2": "holdout2", "dev300": "dev"}


def main() -> None:
    """主構成（P）の質問の分割と生成モデルを config.yaml に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("split", choices=sorted(SPLITS))
    parser.add_argument("model")
    args = parser.parse_args()
    text = CONFIG.read_text(encoding="utf-8")
    settings = {k: dict(v) for k, v in COMMON.items()}
    for section, items in P_SETTINGS.items():
        settings.setdefault(section, {}).update(items)
    settings["experiment"]["question_split"] = SPLITS[args.split]
    if args.split == "dev300":
        settings["experiment"]["question_limit"] = str(DEV300_LIMIT_PER_INBOX)
    settings["llm"] = {"expert_model_gpu": f'"{args.model}"'}
    for section, items in settings.items():
        for key, value in items.items():
            text = set_key(text, section, key, value)
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
