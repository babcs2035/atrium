"""d0009 B3（Iteration 24）：config.yaml を，持ち主の特定の実機の実行（基準 base・dev で選んだ得点 new）の条件にする（コメントを保つため文字列で書き換える）．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/b3o_config.py base [--limit-per-inbox N]
    uv run python scripts/d0009/b3o_config.py new  [--limit-per-inbox N]
どちらも Iteration 22 の P（scripts/d0009/a3_config.py p）と同じ条件で，質問の分割を holdout_q（H ∪ H2）にしたもの．
new は `results/d0009_b3_owner/owner_score.json`（`atrium owner-score-eval` の出力）の G_D.selection.chosen を読み，
routing.owner_score・owner_score_agg・owner_score_k・bm25_weight（V の wb）・owner_ranker_path（L）を足す．
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a3_config import COMMON, P_SETTINGS, set_key  # noqa: E402  (同じ場所の a3_config を使う)

CONFIG = Path("config.yaml")
OWNER_SCORE_JSON = Path("results/d0009_b3_owner/owner_score.json")
# learned の重みのファイル名（質問者の <data_dir>/enronqa/summary/ の下．deploy が summary ごと配る）
RANKER_FILE = "owner_ranker.json"
NAME_PATTERN = re.compile(r"^V_(sum|expsum)_k(\d+)_wb([0-9.]+)$")


def routing_settings(chosen: str) -> dict[str, str]:
    """選んだ得点の名前（V_<agg>_k<k>_wb<wb>・L）から，config.yaml の routing の追加のキーを作る．"""
    if chosen == "L":
        return {"owner_score": "learned", "owner_ranker_path": f'"{RANKER_FILE}"'}
    match = NAME_PATTERN.match(chosen)
    if match is None:
        raise ValueError(f"unexpected score name: {chosen}")
    agg, k, wb = match.groups()
    return {
        "owner_score": "vote",
        "owner_score_agg": agg,
        "owner_score_k": k,
        "bm25_weight": str(float(wb)),
    }


def main() -> None:
    """base または new の条件を config.yaml に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("config", choices=["base", "new"])
    parser.add_argument(
        "--limit-per-inbox", type=int, default=None, help="smoke 用（受信箱ごとの先頭の数）"
    )
    args = parser.parse_args()
    text = CONFIG.read_text(encoding="utf-8")
    settings = {k: dict(v) for k, v in COMMON.items()}
    for section, items in P_SETTINGS.items():
        settings.setdefault(section, {}).update(items)
    settings["experiment"]["question_split"] = "holdout_q"
    if args.limit_per_inbox is not None:
        settings["experiment"]["question_limit"] = str(args.limit_per_inbox)
    if args.config == "new":
        chosen = json.loads(OWNER_SCORE_JSON.read_text(encoding="utf-8"))["G_D"]["selection"][
            "chosen"
        ]
        settings.setdefault("routing", {}).update(routing_settings(chosen))
    for section, items in settings.items():
        for key, value in items.items():
            text = set_key(text, section, key, value)
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
