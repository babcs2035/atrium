"""RAGRoute のルーターの入力特徴量（学習と推論で共有する）．

特徴量は [クエリ埋め込み, データ源の重心, データ源の one-hot] の連結であり，埋め込みの長さが
データ源ごとに異なる場合（FeB4RAG）は pad_dim まで 0 で埋める．RAGRoute の
`ragroute/router.py` の `select_relevant_sources_ragroute` と同じ構成である．
"""

from __future__ import annotations

import numpy as np

from atrium.arrays import F32Array

# RAGRoute の EMBEDDING_MAX_LENGTH と同じ値（MedCPT は 768 次元，FeB4RAG は SGPT の 4096 次元が最大）
PAD_DIM = {"medrag": 768, "feb4rag": 4096}


def pad_to(vec: F32Array, dim: int) -> F32Array:
    """ベクトルを dim まで 0 で埋める．"""
    if vec.shape[0] > dim:
        raise ValueError(f"vector dim {vec.shape[0]} exceeds pad dim {dim}")
    return np.pad(vec.astype(np.float32), (0, dim - vec.shape[0]))


def ragroute_features(
    query_embedding: F32Array,
    centroid: F32Array,
    source_index: int,
    n_sources: int,
    pad_dim: int,
) -> F32Array:
    """1 組の（クエリ，データ源）の特徴量を作る．"""
    one_hot = np.zeros(n_sources, dtype=np.float32)
    one_hot[source_index] = 1.0
    return np.concatenate([pad_to(query_embedding, pad_dim), pad_to(centroid, pad_dim), one_hot])
