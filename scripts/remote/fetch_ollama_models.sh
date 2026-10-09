#!/usr/bin/env bash
# 制御点で Ollama のモデルを取得し，$DATA_DIR/ollama/models に置く（各ノードへは deploy・judge が rsync で配る）．
#
# 使い方（操作端末から `bash scripts/tasks/remote.sh fetch_ollama_models <model>...`）:
#   bash scripts/remote/fetch_ollama_models.sh qwen3:1.7b qwen3:8b qwen3:14b
# データセットやモデルの取得先は制御点にする（2026-10-09 のユーザーの指示．操作端末 gpu2 のディスクは空きが少ない）．
# 取得済みのモデルは ollama pull が差分だけを確かめて終わる．
source "$(dirname "$0")/lib.sh"

[ "$#" -gt 0 ] || { echo "usage: fetch_ollama_models.sh <model>..." >&2; exit 1; }
NAME=atrium-ollama-fetch
IMAGE="localhost:$REGISTRY_PORT/mirror/ollama:$OLLAMA_TAG"
mkdir -p "$DATA_DIR/ollama"
docker rm -f "$NAME" > /dev/null 2>&1 || true
trap 'docker rm -f "$NAME" > /dev/null 2>&1 || true' EXIT
docker run -d --name "$NAME" --user "$HOST_UID:$HOST_GID" -e HOME=/ollama -e OLLAMA_MODELS=/ollama/models \
  -v "$DATA_DIR/ollama:/ollama" "$IMAGE" > /dev/null
# サーバーが応答するまで待つ
for _ in $(seq 1 30); do
  docker exec "$NAME" ollama list > /dev/null 2>&1 && break
  sleep 1
done
for model in "$@"; do
  log "ollama pull $model"
  retry 3 docker exec "$NAME" ollama pull "$model" > /dev/null
done
docker exec "$NAME" ollama list
