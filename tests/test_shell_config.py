"""シェルスクリプトが読む変数（src/atrium/shell_config.py）が，atrium.config の解釈と食い違わない仕様．"""

from __future__ import annotations

import shlex
import subprocess
import sys

from atrium.config import AtriumConfig
from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "src" / "atrium" / "shell_config.py"


def _run_script() -> dict[str, str]:
    out = subprocess.run(
        [sys.executable, str(SCRIPT), str(REPO_ROOT / "config.yaml")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    variables: dict[str, str] = {}
    for line in out.splitlines():
        name, _, quoted = line.partition("=")
        variables[name] = shlex.split(quoted)[0] if quoted != "''" else ""
    return variables


def test_shell_variables_match_validated_config(cfg: AtriumConfig) -> None:
    v = _run_script()
    assert v["CONTROL"] == cfg.cluster.control
    assert v["REMOTE_DIR"] == cfg.cluster.remote_dir
    assert v["REQUESTER"] == cfg.cluster.requester.host
    expected_llm = cfg.cluster.requester_llm.host if cfg.cluster.requester_llm else ""
    assert v["REQUESTER_LLM"] == expected_llm
    assert v["EXPERTS"].split() == cfg.cluster.expert_hosts()
    assert v["GPU_WORKERS"].split() == cfg.cluster.gpu_worker_hosts()
    assert v["REQUESTER_NUM_PARALLEL"] == str(cfg.llm.requester_num_parallel)
    assert v["LLM_NUM_CTX"] == str(cfg.llm.num_ctx)
    assert v["E0_HOSTS"].split() == cfg.e0.hosts


def test_ssh_users_resolve_like_config(cfg: AtriumConfig) -> None:
    users = dict(entry.split("=") for entry in _run_script()["SSH_USERS"].split())
    expected = {d.host: cfg.cluster.ssh_user_of(d) for d in cfg.cluster.devices()}
    assert users == expected


def test_shell_scripts_do_not_use_a_generated_env_file() -> None:
    scripts = [*REPO_ROOT.glob("scripts/*/*.sh"), REPO_ROOT / "mise.toml"]
    assert [p.name for p in scripts if "cluster.env" in p.read_text("utf-8")] == []
