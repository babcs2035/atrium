"""compose のひな形（docker/compose.<role>.yml）の `${...}` を，config.yaml の値で埋めて compose.yml にする．

環境変数や .env ファイルでは何も制御せず，各ノードの compose.yml は常に config.yaml から作る
（deploy が `atrium render-compose` を呼ぶ）．ひな形に書いてある `${NAME}` が全て埋まらなければエラーにする．
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Literal

from atrium.config import AtriumConfig

Role = Literal["node", "node_gpu", "requester", "requester_llm"]

TEMPLATE_DIR = Path("docker")


def compose_values(
    cfg: AtriumConfig,
    role: Role,
    uid: int,
    gid: int,
    node_id: str | None,
    shard_ids: str | None,
) -> dict[str, str]:
    """ひな形に埋める値の一覧を作る．"""
    values = {
        "REGISTRY_PORT": str(cfg.cluster.registry_port),
        "OLLAMA_TAG": cfg.llm.ollama_version,
        "HOST_UID": str(uid),
        "HOST_GID": str(gid),
    }
    if role in ("requester", "requester_llm"):
        values["REQUESTER_NUM_PARALLEL"] = str(cfg.llm.requester_num_parallel)
        return values
    if not node_id or not shard_ids:
        raise ValueError("role=node requires node_id and shard_ids")
    # GPU を持つ専門家（node_gpu）は Ollama を GPU で動かし，llm.expert_model_gpu を使う
    model = cfg.llm.expert_model
    if role == "node_gpu" and cfg.llm.expert_model_gpu:
        model = cfg.llm.expert_model_gpu
    values.update(
        NODE_PORT=str(cfg.cluster.node_port),
        NODE_ID=node_id,
        SHARD_IDS=shard_ids,
        EXPERT_MODEL=model,
    )
    return values


def render_compose(
    cfg: AtriumConfig,
    role: Role,
    uid: int,
    gid: int,
    *,
    node_id: str | None = None,
    shard_ids: str | None = None,
    template_dir: Path = TEMPLATE_DIR,
) -> str:
    """ひな形を読み，値を埋めた compose.yml の内容を返す（未定義の `${NAME}` があれば KeyError）．"""
    template = Template((template_dir / f"compose.{role}.yml").read_text(encoding="utf-8"))
    return template.substitute(compose_values(cfg, role, uid, gid, node_id, shard_ids))
