#!/usr/bin/env bash
# wafl の 1 台に判定モデル（qwen3:8b）の Ollama container を立てる（d0009 A2 の棄権判定の分担用．運用ルール 15 に従う）．
#
# 使い方（操作端末 gpu4 から）:
#   bash scripts/remote/judge_setup_wafl_node.sh 501     # wafl501 に container atrium-judge-ollama-local（127.0.0.1:11436）を立てる
# モデルは各台で `hf download`（既定の ~/.cache/huggingface）で取得し，その blob を読み取り専用で bind mount する（複製しない）．
# hf CLI は作業ディレクトリ ~/workspace/ktakahashi/atrium/.venv-hf に入れる（uv の実行ファイルは gpu4 のものを bin/ に置く）．
# イメージ ollama/ollama:0.35.1 は，各台に無ければ gpu4 から docker save で送る（モデルではないため）．
# 他の研究者の GPU プロセスがある台は何もせず終了コード 1 を返す．
set -euo pipefail
n="${1:?usage: judge_setup_wafl_node.sh <wafl number>}"
host="wafl${n}"
ssh_cmd=(ssh -q -o BatchMode=yes "$host")
WORK='$HOME/workspace/ktakahashi/atrium'
REPO_ID="Qwen/Qwen3-8B-GGUF"
GGUF_FILE="Qwen3-8B-Q4_K_M.gguf"
# gpu4 で採点に使っているのと同じ revision
REVISION="7c41481f57cb95916b40956ab2f0b139b296d974"
IMAGE="ollama/ollama:0.35.1"

if "${ssh_cmd[@]}" 'nvidia-smi --query-compute-apps=pid --format=csv,noheader' | grep -v '^\*\*' | grep -q .; then
  echo "$host busy (GPU process exists)"; exit 1
fi

# uv（hf CLI の導入用．作業ディレクトリの bin/ に置く）
"${ssh_cmd[@]}" "mkdir -p $WORK/bin"
scp -q -o BatchMode=yes "$(command -v uv)" "$host:workspace/ktakahashi/atrium/bin/uv" 2>&1 | grep -v '^\*\*' || true
"${ssh_cmd[@]}" "cd $WORK && export UV_CACHE_DIR=$WORK/.uv-cache && chmod +x bin/uv && \
  { [ -x .venv-hf/bin/hf ] || { bin/uv venv --allow-existing .venv-hf && bin/uv pip install --python .venv-hf/bin/python huggingface_hub; }; }" 2>&1 | grep -v '^\*\*' || true

# モデルの取得（既定のキャッシュ ~/.cache/huggingface）
"${ssh_cmd[@]}" "$WORK/.venv-hf/bin/hf download $REPO_ID $GGUF_FILE --revision $REVISION" 2>&1 | grep -v '^\*\*' | tail -1

# イメージ（無い台にだけ送る）
if ! "${ssh_cmd[@]}" "docker image inspect $IMAGE >/dev/null 2>&1"; then
  sg docker -c "docker save $IMAGE" | "${ssh_cmd[@]}" 'docker load' 2>&1 | grep -v '^\*\*'
fi

# container（blob をそのまま読み取り専用で mount．container 内の ollama create は volume への取り込み）
"${ssh_cmd[@]}" "set -e
BLOB=\$(readlink -f \$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-8B-GGUF/snapshots/$REVISION/$GGUF_FILE)
docker volume create atrium-judge-ollama >/dev/null
docker rm -f atrium-judge-ollama-local >/dev/null 2>&1 || true
docker run -d --name atrium-judge-ollama-local --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all -p 127.0.0.1:11436:11434 \
  -e OLLAMA_NUM_PARALLEL=2 -e OLLAMA_VULKAN=0 -v atrium-judge-ollama:/root/.ollama -v \"\$BLOB\":/gguf/$GGUF_FILE:ro $IMAGE >/dev/null
until curl -s localhost:11436/api/version >/dev/null; do sleep 2; done
docker exec atrium-judge-ollama-local sh -c 'printf \"FROM /gguf/$GGUF_FILE\n\" > /tmp/Modelfile && ollama create qwen3:8b -f /tmp/Modelfile' | tail -1
docker exec atrium-judge-ollama-local ollama list" 2>&1 | grep -v '^\*\*'
echo "$host done"
