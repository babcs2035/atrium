"""d0009 B1（Iteration 35）：config.yaml を a6f_config.py の条件に，質問者の生存確認（experiment.peer_health）を足した条件にする．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/b1_config.py [--fail-threshold 1]
a6f_config.py の出力（主構成 P の障害の測定の条件）に，experiment.peer_health の 1 つだけを足す（journal の Iteration 35）．
値は事前登録のもの：間隔 1.0 秒・1 回の打ち切り 2.0 秒・連続 1 回の失敗で外し・連続 1 回の成功で戻す．
--fail-threshold は smoke の誤検出（G-FP (ii)）があったときに 2 へ 1 回だけ上げるためのもの（b1_chain.sh が使う）．
"""

from __future__ import annotations

import argparse
from pathlib import Path

import a6f_config
from a3_config import CONFIG, set_key

PEER_HEALTH = (
    "{{interval_s: 1.0, timeout_s: 2.0, fail_threshold: {fail_threshold}, recover_threshold: 1}}"
)


def main() -> None:
    """a6f_config の条件を config.yaml に書いてから，experiment.peer_health を足す．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-threshold", type=int, default=1)
    args = parser.parse_args()
    a6f_config.main()
    text = CONFIG.read_text(encoding="utf-8")
    text = set_key(
        text, "experiment", "peer_health", PEER_HEALTH.format(fail_threshold=args.fail_threshold)
    )
    Path(CONFIG).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
