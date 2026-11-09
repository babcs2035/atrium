"""d0009 A6 参加（Iteration 31）：G-A の published_df の不一致が，公開する語の打ち切り（term_size = 64,000）での同点の選び方の違いによることを確かめる（診断．gpu4 の CPU）．

使い方（リポジトリの直下．`summary/{vocab.json,bm25_stats.npz}` を制御点から `--summary-dir` へ写しておく．GPU は使わない）:
    CUDA_VISIBLE_DEVICES= uv run python scripts/d0009/a6j_ga_ties.py --placement results/d0009_a6_join_r1/placement.json --summary-dir /tmp/ga
- ライブ（デバイスの `summarize_device`）の公開する語は文書頻度の降順・同点は語の辞書順の上位 term_size 語．オフライン（`frontier_methods.cori_weights`）は
  語彙の初出の順（文書ごとの `set` の反復の順で決まり，実行ごとに変わりうる）の安定な降順の上位 term_size 語なので，打ち切りの df に同点があると選ぶ語が違う．
- 各デバイスの語彙の大きさ・打ち切りの df と，ライブの組み立て（デバイスごとの上位の文書頻度の合計）とオフラインの `bm25_stats.npz` の語ごとの差を数える．
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from atrium.data_enronqa import read_inbox
from atrium.device_summary import body_of, filter_terms
from atrium.paths import dataset_paths
from atrium.routing.advert import tokenize

TERM_SIZE = 64000


def tie_diagnosis(placement_path: Path, summary_dir: Path, data_dir: Path) -> dict[str, object]:
    """デバイスごとの公開する語を再計算し，オフラインの published_df との差が打ち切りの境目の同点だけかを数える（G-A′）．

    差のある語（ライブとオフラインで文書頻度の合計が違う語，オフラインにだけある語）のうち，語彙が term_size を超えるデバイスで
    その語の df がそのデバイスの打ち切りの df に等しいものを「境目の同点」とする．`n_mismatch_not_at_cutoff` が 0 なら，差は境目の同点の入れ替わりだけである．
    """
    paths = dataset_paths(data_dir, "enronqa")
    placement = json.loads(placement_path.read_text(encoding="utf-8"))
    live: Counter[str] = Counter()
    cutoffs: list[int | None] = []
    vocab_sizes: list[int] = []
    # 語彙が term_size を超えたデバイスの（df の表，打ち切りの df）
    oversize: list[tuple[Counter[str], int]] = []
    for node in placement["nodes"]:
        df: Counter[str] = Counter()
        for inbox in node["shard_ids"]:
            for doc in read_inbox(paths, inbox):
                body = body_of(doc["content"])
                if body.strip():
                    df.update(set(filter_terms(tokenize(body))))
        top = sorted(df.items(), key=lambda kv: (-kv[1], kv[0]))[:TERM_SIZE]
        live.update(dict(top))
        vocab_sizes.append(len(df))
        cutoff = top[-1][1] if len(top) == TERM_SIZE else None
        cutoffs.append(cutoff)
        if len(df) > TERM_SIZE and cutoff is not None:
            oversize.append((df, cutoff))
    stats = np.load(summary_dir / "bm25_stats.npz")
    vocab = json.loads((summary_dir / "vocab.json").read_text(encoding="utf-8"))
    offline = {t: float(stats["published_df"][i]) for i, t in enumerate(vocab)}
    mismatched = [t for t in live if t in offline and live[t] != offline[t]]
    only_offline = [t for t in offline if t not in live and offline[t] > 0]
    only_live = [t for t in live if t not in offline]

    def at_cutoff(term: str) -> bool:
        return any(df.get(term) == cutoff for df, cutoff in oversize)

    not_at_cutoff = [t for t in mismatched + only_offline + only_live if not at_cutoff(t)]
    return {
        "n_devices": len(placement["nodes"]),
        "vocab_size_min": min(vocab_sizes),
        "vocab_size_max": max(vocab_sizes),
        "n_devices_over_term_size": len(oversize),
        "cutoff_df_of_devices_over_term_size": [c for _, c in oversize],
        "n_terms_df_mismatch": len(mismatched),
        "n_terms_only_offline_df_positive": len(only_offline),
        "n_terms_only_live": len(only_live),
        "max_abs_df_diff": max((abs(live[t] - offline[t]) for t in mismatched), default=0),
        "n_mismatch_not_at_cutoff": len(not_at_cutoff),
    }


def main() -> None:
    """診断を出力する．"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--placement", required=True)
    parser.add_argument("--summary-dir", required=True)
    parser.add_argument("--data-dir", default="artifacts")
    args = parser.parse_args()
    print(
        json.dumps(
            tie_diagnosis(Path(args.placement), Path(args.summary_dir), Path(args.data_dir)),
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
