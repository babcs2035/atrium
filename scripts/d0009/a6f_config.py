"""d0009 A6 障害（Iteration 30）：config.yaml を主構成 P の障害の測定の条件にする（コメントを保つため文字列で書き換える）．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/a6f_config.py
a6_config.py の P（H の受信箱ごとの先頭 2 問の 288 問・bm25_probe・owner_answer・owner_m 3・R3′ の選択の経路・RRF・保存する候補・表の得点）に，
`cluster.node_admin: true`（failure-run が管理 API でノードを止めるために要る）を足す．違いはこの 1 つだけ（journal の Iteration 30）．
"""

from __future__ import annotations

from pathlib import Path

from a3_config import COMMON, CONFIG, P_SETTINGS, set_key
from a6_config import A6_COMMON, P_EXTRA, merge_settings

NODE_ADMIN = {"cluster": {"node_admin": "true"}}


def main() -> None:
    """P の条件に node_admin を足して config.yaml に書く．"""
    settings = merge_settings(COMMON, A6_COMMON, P_SETTINGS, P_EXTRA, NODE_ADMIN)
    text = CONFIG.read_text(encoding="utf-8")
    for section, items in settings.items():
        for key, value in items.items():
            text = set_key(text, section, key, value)
    Path(CONFIG).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
