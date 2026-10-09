"""RAGRoute のルーター（MLP）の学習（データ中継点で実行する）．

学習手順は RAGRoute（https://github.com/sacs-epfl/ragroute，MIT License）の
`scripts/train/train_medrag_router.py` から移植した：BCEWithLogitsLoss，Adam（lr 1e-3，
weight decay 3e-5），150 エポック，最初の 115 エポックは CyclicLR（1e-3〜5e-3，triangular2），
以降は StepLR，勾配のノルムを 1.0 で切り，検証 AUC が最良のモデルを残す．
medrag は特徴量を標準化し，feb4rag は標準化しない（RAGRoute の推論コードと同じ）．
FeB4RAG の学習スクリプトは途中までしか公開内容を確認できていないため，同じ手順を流用する．
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from atrium.arrays import F32Array
from atrium.config import AtriumConfig, DatasetName
from atrium.manifest import combine_centroids, read_manifest
from atrium.paths import DatasetPaths
from atrium.routing.features import PAD_DIM, ragroute_features

logger = logging.getLogger(__name__)

EPOCHS = 150
CYCLIC_EPOCHS = 115
BATCH_SIZE = 128
LR = 1e-3
MAX_LR = 5e-3
WEIGHT_DECAY = 3e-5
GRAD_CLIP = 1.0
# enronqa は p0004 で追加（medrag と同じ種）
TRAIN_SEED = {"medrag": 12, "feb4rag": 42, "enronqa": 12}


@dataclass(frozen=True)
class SourceInfo:
    """学習時に使うデータ源の情報（推論時は各ノードの自己紹介から同じものを作る）．"""

    name: str
    encoder: str
    centroid: F32Array


def build_matrix(
    qids: Sequence[str],
    row_of: dict[str, int],
    query_emb: dict[str, F32Array],
    sources: Sequence[SourceInfo],
    labels: dict[str, list[str]],
    pad_dim: int,
    negatives: int | None = None,
    rng: np.random.Generator | None = None,
) -> tuple[F32Array, F32Array]:
    """（質問，データ源）の組ごとの特徴量とラベルを作る．

    negatives を与えると，関連ありの組の全てと，関連の無いデータ源を 1 問あたり negatives 個だけ無作為に使う
    （EnronQA の 150 個の受信箱では全ての組が数 GB になるため）．
    """
    xs: list[F32Array] = []
    ys: list[float] = []
    for qid in qids:
        relevant = set(labels.get(qid, []))
        chosen: list[int] = list(range(len(sources)))
        if negatives is not None:
            if rng is None:
                raise ValueError("negative sampling requires rng")
            others = [i for i, s in enumerate(sources) if s.name not in relevant]
            picked = rng.choice(others, size=min(negatives, len(others)), replace=False)
            chosen = sorted(
                [i for i, s in enumerate(sources) if s.name in relevant] + [int(i) for i in picked]
            )
        for i in chosen:
            s = sources[i]
            xs.append(
                ragroute_features(
                    query_emb[s.encoder][row_of[qid]], s.centroid, i, len(sources), pad_dim
                )
            )
            ys.append(1.0 if s.name in relevant else 0.0)
    return np.stack(xs).astype(np.float32), np.array(ys, dtype=np.float32)


def binary_metrics(labels: F32Array, probs: F32Array, threshold: float) -> dict[str, float]:
    """適合率・再現率・F1・AUC を返す．"""
    from sklearn.metrics import roc_auc_score

    pred = probs > threshold
    tp = float(np.sum(pred & (labels == 1)))
    fp = float(np.sum(pred & (labels == 0)))
    fn = float(np.sum(~pred & (labels == 1)))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    auc = float(roc_auc_score(labels, probs)) if len(set(labels.tolist())) > 1 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "auc": auc}


def train_router(cfg: AtriumConfig, dataset: DatasetName, paths: DatasetPaths) -> dict[str, object]:
    """ルーターを学習し，成果物を paths.router に書く．評価値を返す．"""
    import torch
    from sklearn.metrics import roc_curve
    from torch import nn

    from atrium.routing.ragroute import (
        ROUTER_META,
        ROUTER_SCALER,
        ROUTER_WEIGHTS,
        CorpusRoutingNN,
        RouterMeta,
    )

    seed = TRAIN_SEED[dataset]
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    manifest = read_manifest(paths.manifest)
    sources = []
    for name in cfg.data.sources_of(dataset):
        members = manifest.shards_of(name)
        sources.append(
            SourceInfo(
                name,
                members[0].encoder,
                combine_centroids([m.n_docs for m in members], [m.centroid for m in members]),
            )
        )
    ids: list[str] = json.loads(paths.query_ids.read_text(encoding="utf-8"))
    row_of = {q: i for i, q in enumerate(ids)}
    query_emb = {s.encoder: np.load(paths.query_embeddings(s.encoder)) for s in sources}
    labels: dict[str, list[str]] = json.loads(paths.labels.read_text(encoding="utf-8"))
    split: dict[str, list[str]] = json.loads(paths.split.read_text(encoding="utf-8"))
    pad_dim = PAD_DIM[dataset]
    negatives = cfg.data.require_enronqa().ragroute_negatives if dataset == "enronqa" else None
    rng = np.random.default_rng(seed)
    data = {
        name: build_matrix(split[name], row_of, query_emb, sources, labels, pad_dim, negatives, rng)
        for name in ("train", "val", "test")
    }

    # medrag と enronqa は 1 種類の検索器の埋め込みなので標準化する（RAGRoute の medrag と同じ）
    use_scaler = dataset in ("medrag", "enronqa")
    mean = data["train"][0].mean(axis=0)
    scale = data["train"][0].std(axis=0)
    scale[scale == 0] = 1.0  # sklearn の StandardScaler と同じく分散 0 の列はそのまま通す

    def transform(x: F32Array) -> F32Array:
        return (x - mean) / scale if use_scaler else x

    tensors = {
        k: (torch.from_numpy(transform(x)).float(), torch.from_numpy(y))
        for k, (x, y) in data.items()
    }
    input_dim = tensors["train"][0].shape[1]
    model = CorpusRoutingNN(input_dim).to(device)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    cyclic = torch.optim.lr_scheduler.CyclicLR(
        optimizer,
        base_lr=LR,
        max_lr=MAX_LR,
        step_size_up=10,
        mode="triangular2",
        cycle_momentum=False,
    )
    step = torch.optim.lr_scheduler.StepLR(optimizer, step_size=50, gamma=0.05)

    def predict(x: torch.Tensor) -> F32Array:
        model.eval()
        with torch.no_grad():
            out: F32Array = torch.sigmoid(model(x.to(device)).view(-1)).cpu().numpy()
        return out

    best_auc, best_state = -1.0, None
    x_train, y_train = tensors["train"]
    for epoch in range(EPOCHS):
        model.train()
        perm = torch.randperm(len(x_train))
        for start in range(0, len(perm), BATCH_SIZE):
            idx = perm[start : start + BATCH_SIZE]
            optimizer.zero_grad()
            loss = criterion(model(x_train[idx].to(device)).view(-1), y_train[idx].to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=GRAD_CLIP)
            optimizer.step()
            (cyclic if epoch < CYCLIC_EPOCHS else step).step()
        val_auc = binary_metrics(tensors["val"][1].numpy(), predict(tensors["val"][0]), 0.5)["auc"]
        if val_auc > best_auc + 1e-6:
            best_auc = val_auc
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        logger.info("epoch %d: val auc %.4f", epoch + 1, val_auc)
    if best_state is None:
        raise RuntimeError("training produced no model")
    model.load_state_dict(best_state)

    fpr, tpr, thresholds = roc_curve(tensors["val"][1].numpy(), predict(tensors["val"][0]))
    youden = float(thresholds[int(np.argmax(tpr - fpr))])
    report: dict[str, object] = {
        "best_val_auc": best_auc,
        "val_optimal_threshold": youden,
        "test_at_0.5": binary_metrics(tensors["test"][1].numpy(), predict(tensors["test"][0]), 0.5),
        "n_pairs": {k: int(len(v[1])) for k, v in tensors.items()},
    }
    paths.router.mkdir(parents=True, exist_ok=True)
    if use_scaler:
        np.savez(paths.router / ROUTER_SCALER, mean=mean, scale=scale)
    meta = RouterMeta(
        dataset=dataset,
        sources=[s.name for s in sources],
        input_dim=input_dim,
        pad_dim=pad_dim,
        use_scaler=use_scaler,
        val_optimal_threshold=youden,
    )
    (paths.router / ROUTER_META).write_text(meta.model_dump_json(indent=1), encoding="utf-8")
    (paths.router / "train_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    # データ準備は router.pt の有無で完了を判断するので，最後に（一時ファイルからの置き換えで）書く
    tmp = paths.router / f"{ROUTER_WEIGHTS}.tmp"
    torch.save(best_state, tmp)
    tmp.replace(paths.router / ROUTER_WEIGHTS)
    return report
