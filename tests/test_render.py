"""compose のひな形の描画と，環境変数を使わない方針の仕様．"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from atrium.config import AtriumConfig
from atrium.render import render_compose
from tests.conftest import REPO_ROOT

TEMPLATE_DIR = REPO_ROOT / "docker"
SRC_DIR = REPO_ROOT / "src" / "atrium"


def test_node_compose_has_values_from_config_and_no_placeholders(cfg: AtriumConfig) -> None:
    text = render_compose(
        cfg,
        "node",
        1001,
        1002,
        node_id="192.168.13.100",
        shard_ids="pubmed-00,pubmed-01",
        template_dir=TEMPLATE_DIR,
    )
    assert "${" not in text
    node = yaml.safe_load(text)["services"]["node"]
    command = node["command"]
    assert command[command.index("--node-id") + 1] == "192.168.13.100"
    assert command[command.index("--shard-ids") + 1] == "pubmed-00,pubmed-01"
    assert node["user"] == "1001:1002"
    assert node["ports"] == [f"{cfg.cluster.node_port}:8100"]
    assert "environment" not in node  # ノードの動作を環境変数で渡さない


def test_requester_compose_takes_ollama_parallelism_from_config(cfg: AtriumConfig) -> None:
    text = render_compose(cfg, "requester", 1000, 1000, template_dir=TEMPLATE_DIR)
    ollama = yaml.safe_load(text)["services"]["ollama"]
    assert ollama["environment"]["OLLAMA_NUM_PARALLEL"] == cfg.llm.requester_num_parallel
    assert ollama["image"].endswith(f"mirror/ollama:{cfg.llm.ollama_version}")


def test_node_compose_requires_node_id_and_shards(cfg: AtriumConfig) -> None:
    with pytest.raises(ValueError, match="node_id and shard_ids"):
        render_compose(cfg, "node", 1000, 1000, template_dir=TEMPLATE_DIR)


def test_undefined_placeholder_in_template_is_an_error(cfg: AtriumConfig, tmp_path: Path) -> None:
    (tmp_path / "compose.requester.yml").write_text("image: ${UNDEFINED}\n", encoding="utf-8")
    with pytest.raises(KeyError):
        render_compose(cfg, "requester", 1000, 1000, template_dir=tmp_path)


def test_python_sources_do_not_read_environment_variables() -> None:
    pattern = re.compile(r"os\.environ|os\.getenv|\bgetenv\(")
    offenders = [p.name for p in SRC_DIR.rglob("*.py") if pattern.search(p.read_text("utf-8"))]
    assert offenders == []
