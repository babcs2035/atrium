"""d0009 A1（Iteration 23）：config.yaml を，質問者の数 N（1 または 3）の主構成 R3′ の条件にする（コメントを保つため文字列で書き換える）．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/a1_config.py --n 3 [--parallel 90] [--limit-per-inbox 2]
P2P の主構成（Iteration 22 の P．scripts/d0009/a3_config.py p）に，質問者の台数だけを足す．N = 3 は cluster.extra_requesters に
受信箱を持つ CPU の専門家 192.168.13.100・192.168.14.100 を置く（B83）．parallel は全質問者の合計の同時の問の数．
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a3_config import COMMON, P_SETTINGS, set_key  # noqa: E402  (同じ場所の a3_config を使う)

CONFIG = Path("config.yaml")
EXTRA_REQUESTER_HOSTS = ("192.168.13.100", "192.168.14.100")
MAIN_PARALLEL = 30


def main() -> None:
    """主構成 R3′ の条件と質問者の数を config.yaml に書く．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, choices=[1, 3], required=True, help="質問者の数 N")
    parser.add_argument("--parallel", type=int, default=MAIN_PARALLEL, help="全質問者の合計")
    parser.add_argument(
        "--limit-per-inbox", type=int, default=None, help="障害の記録用（受信箱ごとの先頭の数）"
    )
    args = parser.parse_args()
    text = CONFIG.read_text(encoding="utf-8")
    settings = {k: dict(v) for k, v in COMMON.items()}
    for section, items in P_SETTINGS.items():
        settings.setdefault(section, {}).update(items)
    settings["experiment"]["parallel"] = str(args.parallel)
    if args.limit_per_inbox is not None:
        settings["experiment"]["question_limit"] = str(args.limit_per_inbox)
    for section, items in settings.items():
        for key, value in items.items():
            text = set_key(text, section, key, value)
    hosts = ", ".join(f'{{host: "{h}"}}' for h in EXTRA_REQUESTER_HOSTS)
    value = f"[{hosts}]" if args.n == 3 else "[]"
    text, n = re.subn(r"(?m)^(  extra_requesters: ).*$", rf"\g<1>{value}", text, count=1)
    if n != 1:
        raise ValueError("cluster.extra_requesters line not found in config.yaml")
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
