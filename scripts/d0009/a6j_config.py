"""d0009 A6 参加（Iteration 31）：config.yaml を主構成 P の参加の測定の条件にする（コメントを保つため文字列で書き換える）．

使い方（リポジトリの直下で．実行の後は `git checkout -- config.yaml` で元へ戻し，commit しない）:
    uv run python scripts/d0009/a6j_config.py
a6_config.py の P（H・bm25_probe・owner_answer・owner_m 3・R3′ の選択の経路・RRF・保存する候補・表の得点）に，
`cluster.node_admin: true`（join-run が管理 API でノードを離脱・有効にするために要る）を足す．これは障害の測定（a6f_config.py）と同じ条件で，
違いは `node_admin` だけ（`question_limit` は join-run が使わない．質問は join-run の `--max-tries-per-inbox` が決める）．
"""

from __future__ import annotations

from a6f_config import main

if __name__ == "__main__":
    main()
