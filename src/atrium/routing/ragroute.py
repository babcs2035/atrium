"""RAGRoute のルーター（中央で学習した MLP 分類器）．

モデル構造は RAGRoute（https://github.com/sacs-epfl/ragroute，MIT License，
Copyright (c) 2025 SaCS-EPFL）の `ragroute/router.py` の `CorpusRoutingNN` から移植した．
学習は `atrium.train_router` が行い，成果物は次の 3 ファイルである．

    router.pt      MLP の state_dict
    router.json    RouterMeta（入力次元・pad 次元・データ源の順序・標準化の有無）
    scaler.npz     StandardScaler の mean と scale（標準化する場合のみ）
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import torch
from pydantic import BaseModel, ConfigDict
from torch import nn
from torch.nn import functional as F  # noqa: N812

from atrium.arrays import F32Array
from atrium.routing import RoutingQuery, SourceProfile
from atrium.routing.features import ragroute_features

ROUTER_WEIGHTS = "router.pt"
ROUTER_META = "router.json"
ROUTER_SCALER = "scaler.npz"


class RouterMeta(BaseModel):
    """学習済みルーターの付帯情報．"""

    model_config = ConfigDict(extra="forbid")

    dataset: str
    sources: list[str]
    input_dim: int
    pad_dim: int
    use_scaler: bool
    val_optimal_threshold: float


class CorpusRoutingNN(nn.Module):
    """（クエリ，データ源）の組が関連ありかを判定する MLP．"""

    def __init__(self, input_dim: int) -> None:
        """RAGRoute と同じ 128-64-32 の 3 層．"""
        super().__init__()
        self.fc1 = nn.Linear(input_dim, 128)
        self.ln1 = nn.LayerNorm(128)
        self.dropout1 = nn.Dropout(0.4)
        self.fc2 = nn.Linear(128, 64)
        self.ln2 = nn.LayerNorm(64)
        self.dropout2 = nn.Dropout(0.4)
        self.fc3 = nn.Linear(64, 32)
        self.ln3 = nn.LayerNorm(32)
        self.dropout3 = nn.Dropout(0.4)
        self.fc_out = nn.Linear(32, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """ロジットを返す．"""
        x = self.dropout1(F.relu(self.ln1(self.fc1(x))))
        x = self.dropout2(F.relu(self.ln2(self.fc2(x))))
        x = self.dropout3(F.relu(self.ln3(self.fc3(x))))
        out: torch.Tensor = self.fc_out(x)
        return out


class RagrouteRouter:
    """学習済みの MLP でデータ源ごとに関連の有無を判定する．"""

    name = "ragroute"

    def __init__(self, router_dir: Path, sources: Sequence[str], threshold: float) -> None:
        """成果物を読み込む．データ源の順序が学習時と違えば one-hot がずれるので拒否する．"""
        self.meta = RouterMeta.model_validate_json((router_dir / ROUTER_META).read_text("utf-8"))
        if list(sources) != self.meta.sources:
            raise ValueError(f"source order {list(sources)} != trained order {self.meta.sources}")
        self._threshold = threshold
        self._model = CorpusRoutingNN(self.meta.input_dim)
        self._model.load_state_dict(torch.load(router_dir / ROUTER_WEIGHTS, map_location="cpu"))
        self._model.eval()
        self._mean: F32Array | None = None
        self._scale: F32Array | None = None
        if self.meta.use_scaler:
            scaler = np.load(router_dir / ROUTER_SCALER)
            self._mean, self._scale = scaler["mean"], scaler["scale"]

    def probabilities(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> F32Array:
        """各データ源が関連ありである確率を返す．"""
        rows = [
            ragroute_features(
                query.embeddings[s.encoder],
                s.centroid,
                self.meta.sources.index(s.source),
                len(self.meta.sources),
                self.meta.pad_dim,
            )
            for s in sources
        ]
        x = np.stack(rows)
        if self._mean is not None and self._scale is not None:
            x = (x - self._mean) / self._scale
        with torch.no_grad():
            logits = self._model(torch.from_numpy(x.astype(np.float32))).view(-1)
        probs: F32Array = torch.sigmoid(logits).numpy()
        return probs

    def select(self, query: RoutingQuery, sources: Sequence[SourceProfile]) -> list[str]:
        """確率が閾値を超えたデータ源を返す．"""
        if not sources:
            return []
        probs = self.probabilities(query, sources)
        return [s.source for s, p in zip(sources, probs, strict=True) if p > self._threshold]
