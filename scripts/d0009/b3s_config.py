"""d0009 B3（Iteration 25）：config.yaml を，H2 の主構成の実機の実行（d0009_b3s_h2_base）の条件にする（コメントを保つため文字列で書き換える）．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/b3s_config.py [--limit-per-inbox N]
Iteration 22 の P（scripts/d0009/a3_config.py p）と同じ条件で，質問の分割を holdout2（受信箱ごとの 41〜60 番目．2,807 問）にしたもの．
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a3_config import COMMON, P_SETTINGS, set_key  # noqa: E402  (同じ場所の a3_config を使う)

CONFIG = Path("config.yaml")


def main() -> None:
    """主構成（P）で質問の分割を holdout2 にした条件を config.yaml に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit-per-inbox", type=int, default=None, help="smoke 用（受信箱ごとの先頭の数）"
    )
    args = parser.parse_args()
    text = CONFIG.read_text(encoding="utf-8")
    settings = {k: dict(v) for k, v in COMMON.items()}
    for section, items in P_SETTINGS.items():
        settings.setdefault(section, {}).update(items)
    settings["experiment"]["question_split"] = "holdout2"
    if args.limit_per_inbox is not None:
        settings["experiment"]["question_limit"] = str(args.limit_per_inbox)
    for section, items in settings.items():
        for key, value in items.items():
            text = set_key(text, section, key, value)
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
