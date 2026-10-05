"""関連ラベルの計算と，ルーター学習用の分割（データ中継点で実行する）．

MedRAG のラベルは RAGRoute の定義に従う：全データ源から k_ret 件ずつ検索し，検索スコアで統合した
上位 k_rerank 件に 1 件以上の断片を出したデータ源を「関連あり」とする．専門家ノードと同じ
fp16 の埋め込みに対して fp32 で内積を取るので，ノード上の FAISS（fp16 を fp32 に戻して内積を取る）と
同じ順位になる．FeB4RAG のラベルは配布の qrels から作る（atrium.data_feb4rag）．
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import numpy as np

from atrium.arrays import F32Array
from atrium.config import AtriumConfig, DatasetName
from atrium.manifest import read_manifest
from atrium.paths import DatasetPaths

logger = logging.getLogger(__name__)

# RAGRoute の学習スクリプトの分割（medrag: train_medrag_router.py，feb4rag: train_feb4rag_router.py）
MEDRAG_SPLIT_SEED = 12
MEDRAG_TEST_FRACTION = 0.6  # 問題集ごとに 40% を学習，60% を評価に使う
MEDRAG_VAL_FRACTION_OF_TRAIN = 0.1
FEB4RAG_SPLIT_SEED = 42
FEB4RAG_REST_FRACTION = 0.7  # 30% を学習に使い，残りを val : test = 1 : 6 に分ける
FEB4RAG_TEST_FRACTION_OF_REST = 6 / 7
# 関連ラベルの計算で一度に GPU へ載せる断片数（7,663 問 × 5 万断片の得点で約 1.5 GB）
LABEL_BLOCK_ROWS = 50_000


def contributing_sources_from_topk(scores: dict[str, F32Array], k_rerank: int) -> list[list[str]]:
    """データ源ごとの上位スコア（質問数 × k_ret）から，統合後の上位 k_rerank に入ったデータ源を求める．"""
    names = sorted(scores)
    stacked = np.concatenate([scores[n] for n in names], axis=1)
    owner = np.concatenate([np.full(scores[n].shape[1], i) for i, n in enumerate(names)])
    order = np.argsort(-stacked, axis=1, kind="stable")[:, :k_rerank]
    out: list[list[str]] = []
    for row_scores, row in zip(stacked, order, strict=True):
        valid = row[np.isfinite(row_scores[row])]
        out.append(sorted({names[int(owner[j])] for j in valid}))
    return out


def compute_medrag_labels(cfg: AtriumConfig, paths: DatasetPaths) -> None:
    """全シャードを総当たりで検索して関連ラベルを作る（GPU があれば使う）．"""
    import torch

    if paths.labels.exists():
        return
    device = "cuda" if torch.cuda.is_available() else "cpu"
    manifest = read_manifest(paths.manifest)
    ids: list[str] = json.loads(paths.query_ids.read_text(encoding="utf-8"))
    queries = torch.from_numpy(np.load(paths.query_embeddings(cfg.data.medrag.query_encoder))).to(
        device
    )
    k_ret = cfg.retrieval.k_ret
    top: dict[str, F32Array] = {}
    for source in manifest.sources:
        best = torch.full((len(ids), k_ret), float("-inf"), device=device)
        for shard in manifest.shards_of(source):
            for f in shard.files:
                emb = np.load(paths.shards / shard.shard_id / "emb" / f"{f.name}.f16.npy")
                # 断片ファイルには 30 万断片を超えるものがあり，質問数 × 断片数の得点を一度に作ると
                # 12 GB の GPU に収まらない．区切って上位 k_ret を順に統合しても結果は同じである
                for start in range(0, emb.shape[0], LABEL_BLOCK_ROWS):
                    block = (
                        torch.from_numpy(emb[start : start + LABEL_BLOCK_ROWS]).to(device).float()
                    )
                    scores = queries @ block.T
                    best = torch.topk(torch.cat([best, scores], dim=1), k_ret, dim=1).values
            logger.info("labels: searched shard %s", shard.shard_id)
        top[source] = best.cpu().numpy()
    contributing = contributing_sources_from_topk(top, cfg.retrieval.k_rerank)
    paths.labels.parent.mkdir(parents=True, exist_ok=True)
    paths.labels.write_text(json.dumps(dict(zip(ids, contributing, strict=True))), encoding="utf-8")


def make_split(dataset: DatasetName, qids: Sequence[str]) -> dict[str, list[str]]:
    """ルーター学習の train / val / test を RAGRoute と同じ比率・種で作る（入力は名前順にそろえる）."""
    from sklearn.model_selection import train_test_split

    ordered = sorted(qids)
    if dataset == "medrag":
        train: list[str] = []
        val: list[str] = []
        test: list[str] = []
        banks = sorted({q.split("/", 1)[0] for q in ordered})
        for bank in banks:
            in_bank = [q for q in ordered if q.startswith(bank + "/")]
            tr, te = train_test_split(
                in_bank, test_size=MEDRAG_TEST_FRACTION, random_state=MEDRAG_SPLIT_SEED
            )
            train.extend(tr)
            test.extend(te)
        train, val = train_test_split(
            train, test_size=MEDRAG_VAL_FRACTION_OF_TRAIN, random_state=MEDRAG_SPLIT_SEED
        )
        return {"train": sorted(train), "val": sorted(val), "test": sorted(test)}
    tr, rest = train_test_split(
        ordered, test_size=FEB4RAG_REST_FRACTION, random_state=FEB4RAG_SPLIT_SEED
    )
    va, te = train_test_split(
        rest, test_size=FEB4RAG_TEST_FRACTION_OF_REST, random_state=FEB4RAG_SPLIT_SEED
    )
    return {"train": sorted(tr), "val": sorted(va), "test": sorted(te)}


def write_split(dataset: DatasetName, paths: DatasetPaths) -> None:
    """ラベルのある質問だけを分割して split.json を書く．"""
    if paths.split.exists():
        return
    labels: dict[str, list[str]] = json.loads(paths.labels.read_text(encoding="utf-8"))
    paths.split.write_text(json.dumps(make_split(dataset, list(labels))), encoding="utf-8")
