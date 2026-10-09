"""config.yaml をシェルの変数定義にして標準出力へ出す（シェルスクリプトが起動時に評価する）．

使い方（どのホストでも，atrium のパッケージを入れずに動く．必要なのは Python と PyYAML だけ）:

    eval "$(python3 src/atrium/shell_config.py config.yaml)"

シェルスクリプトは設定をファイルや環境変数で持ち回らず，起動のたびに config.yaml から直接読む．
このファイルは atrium の他のモジュール（pydantic を使う）を import しない．制御点には pydantic が無いため．
値の検証と既定値は `atrium.config` が受け持つ（操作端末の全てのタスクが先に読み込んで検証する）ので，
ここでは config.yaml に書かれた値をそのまま使い，キーが無ければ KeyError で止まる．
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH_ARG = 1


def _hosts(devices: list[dict[str, Any]]) -> str:
    return " ".join(d["host"] for d in devices)


def shell_variables(raw: dict[str, Any]) -> dict[str, str]:
    """config.yaml の内容（辞書）から，シェルが使う変数の一覧を作る（リストは空白区切り）．"""
    cluster = raw["cluster"]
    llm = raw["llm"]
    exp = raw["experiment"]
    e0 = raw["e0"]
    requester_llm = [cluster["requester_llm"]] if cluster.get("requester_llm") else []
    devices = [
        cluster["requester"],
        *requester_llm,
        *cluster["experts"],
        *cluster.get("gpu_workers", []),
    ]
    return {
        "CONTROL": cluster["control"],
        "SSH_USER": cluster["ssh_user"],
        "REMOTE_DIR": cluster["remote_dir"],
        "DATA_DIR": cluster["data_dir"],
        "REGISTRY_PORT": str(cluster["registry_port"]),
        "NODE_PORT": str(cluster["node_port"]),
        # 全デバイスの SSH のユーザー（host=user の空白区切り．scripts/remote/lib.sh の ssh_dest が引く）
        "SSH_USERS": " ".join(
            f"{d['host']}={d.get('ssh_user') or cluster['ssh_user']}" for d in devices
        ),
        "REQUESTER": cluster["requester"]["host"],
        # 質問者の LLM（Ollama）を別の GPU PC で動かすときのホスト（同じホストで動かすときは空）
        "REQUESTER_LLM": cluster["requester_llm"]["host"] if cluster.get("requester_llm") else "",
        "EXPERTS": _hosts(cluster["experts"]),
        "GPU_WORKERS": _hosts(cluster.get("gpu_workers", [])),
        "KIND": exp["kind"],
        "DATASET": exp["dataset"],
        "ROUTING": exp["routing"],
        "ANSWER_MODE": exp["answer_mode"],
        "OLLAMA_TAG": llm["ollama_version"],
        "REQUESTER_MODEL": llm["requester_model"],
        "REQUESTER_NUM_PARALLEL": str(llm["requester_num_parallel"]),
        "LLM_NUM_CTX": str(llm["num_ctx"]),
        "EXPERT_MODEL": llm["expert_model"],
        "EXPERT_MODEL_GPU": llm.get("expert_model_gpu") or llm["expert_model"],
        "E0_HOSTS": " ".join(e0["hosts"]),
        "E0_PAIRS": " ".join(f"{a},{b}" for a, b in e0["iperf_pairs"]),
        "E0_GGUF": " ".join(f"{m['name']}|{m['repo']}|{m['file']}" for m in e0["gguf_models"]),
    }


def main(argv: list[str]) -> int:
    """`config.yaml` のパスを受け取り，`NAME='値'` の行を出す．"""
    if len(argv) != CONFIG_PATH_ARG + 1:
        print("usage: shell_config.py <config.yaml>", file=sys.stderr)
        return 2
    raw = yaml.safe_load(Path(argv[CONFIG_PATH_ARG]).read_text(encoding="utf-8"))
    for name, value in shell_variables(raw).items():
        print(f"{name}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
