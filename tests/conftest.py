"""テスト用のデータビルダー（小さなシャード・設定）と，複数ノードへ振り分ける HTTP トランスポート．"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np
import pytest

from atrium.arrays import F32Array
from atrium.config import AtriumConfig, load_config
from atrium.manifest import SHARD_SPEC_FILENAME, ChunkFile, ShardSpec, write_json_model

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg() -> AtriumConfig:
    """リポジトリの config.yaml（テストごとに model_copy で必要な値だけ変える）．"""
    return load_config(REPO_ROOT / "config.yaml")


def with_experiment(cfg: AtriumConfig, **changes: object) -> AtriumConfig:
    """experiment 節の一部を変えた設定を返す．"""
    return cfg.model_copy(update={"experiment": cfg.experiment.model_copy(update=changes)})


def write_faiss_shard(
    root: Path, shard_id: str, source: str, files: dict[str, F32Array], description: str = "test"
) -> ShardSpec:
    """断片ファイル名 → 埋め込み行列 から，atrium.store の構成のシャードを作る．

    断片 i の本文は "<shard_id>/<file>/<i>" とし，ID も同じにする（検索結果から出どころが分かるように）．
    """
    shard_dir = root / shard_id
    (shard_dir / "chunk").mkdir(parents=True)
    (shard_dir / "emb").mkdir()
    chunks: list[ChunkFile] = []
    all_rows: list[F32Array] = []
    for name, emb in files.items():
        np.save(shard_dir / "emb" / f"{name}.f16.npy", emb.astype(np.float16))
        with (shard_dir / "chunk" / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for i in range(emb.shape[0]):
                doc_id = f"{shard_id}/{name}/{i}"
                f.write(json.dumps({"id": doc_id, "title": f"t{i}", "content": doc_id}) + "\n")
        chunks.append(ChunkFile(name=name, n_docs=emb.shape[0]))
        all_rows.append(emb.astype(np.float16).astype(np.float32))
    stacked = np.concatenate(all_rows)
    spec = ShardSpec(
        shard_id=shard_id,
        source=source,
        kind="faiss",
        files=chunks,
        n_docs=int(stacked.shape[0]),
        dim=int(stacked.shape[1]),
        encoder="enc",
        centroid=stacked.mean(axis=0).tolist(),
        description=description,
    )
    write_json_model(shard_dir / SHARD_SPEC_FILENAME, spec)
    return spec


class HostDispatchTransport(httpx.AsyncBaseTransport):
    """URL のホスト名ごとに別の ASGI アプリ（またはハンドラ）へ要求を渡す．"""

    def __init__(self, routes: dict[str, httpx.AsyncBaseTransport]) -> None:
        """ホスト名 → トランスポート．"""
        self._routes = routes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """要求を振り分ける．"""
        return await self._routes[request.url.host].handle_async_request(request)


def ollama_mock(reply: Callable[[dict[str, object]], str]) -> httpx.MockTransport:
    """Ollama の /api/chat を模したトランスポート．reply は要求の JSON から生成文を返す．"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": reply(body)},
                "prompt_eval_count": 100,
                "eval_count": 20,
                "prompt_eval_duration": 1_000_000,
                "eval_duration": 2_000_000,
                "total_duration": 3_000_000,
            },
        )

    return httpx.MockTransport(handler)
