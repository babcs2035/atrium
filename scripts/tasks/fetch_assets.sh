#!/usr/bin/env bash
# 外部の資材を操作端末で取得し，制御点へ送る（scripts/tasks/setup.sh から呼ばれる）．
#
#   1. 外部のイメージ（ollama・llama.cpp・iperf3）を取得し，制御点の registry の mirror/ へ push する
#   2. Ollama のモデル（llm.expert_model・llm.requester_model）を取得し，制御点の $DATA_DIR/ollama へ送る
#   3. E0 の GGUF を取得し，制御点の $DATA_DIR/gguf へ送る
#
# 各ノードは，これらを LAN（registry と rsync）だけで受け取り，インターネットには出ない．
# 研究室側の回線は不安定で，ノードからの取得は 700 KB/s 程度しか出ないことがあり，制御点も
# インターネットへ出られなくなることがあった（2026-10-06）．操作端末の回線は安定している．
#
# 使い方: bash scripts/tasks/fetch_assets.sh <registry へつながるローカルのポート>
source "$(dirname "$0")/lib.sh"

LOCAL_REGISTRY_PORT=${1:?usage: fetch_assets.sh <local registry port>}
CACHE="$REPO_ROOT/artifacts/cache"
mkdir -p "$CACHE/ollama" "$CACHE/gguf"

# ── 外部のイメージのミラー
# 操作端末の docker の設定には ghcr.io の古い認証情報があり，それを送ると取得を拒否されるため，
# 認証情報を持たない一時的な設定で匿名で取得する
ANON_DOCKER_CONFIG=$(mktemp -d)
trap 'rm -rf "$ANON_DOCKER_CONFIG"' EXIT
mirror() {
  local src=$1 name=$2
  local dst="localhost:$LOCAL_REGISTRY_PORT/mirror/$name"
  DOCKER_CONFIG=$ANON_DOCKER_CONFIG docker pull -q "$src"
  docker tag "$src" "$dst"
  docker push -q "$dst"
}
mirror "ollama/ollama:$OLLAMA_TAG" "ollama:$OLLAMA_TAG"
mirror "ghcr.io/ggml-org/llama.cpp:full" "llama.cpp:full"
mirror "networkstatic/iperf3:latest" "iperf3:latest"

# ── Ollama のモデル（取得済みなら ollama pull は差分だけを確かめて終わる）
docker rm -f atrium-ollama-fetch > /dev/null 2>&1 || true
docker run -d --name atrium-ollama-fetch --user "$(id -u):$(id -g)" -e HOME=/ollama \
  -e OLLAMA_MODELS=/ollama/models -v "$CACHE/ollama:/ollama" "ollama/ollama:$OLLAMA_TAG" > /dev/null
sleep 3
for model in "$EXPERT_MODEL" "$REQUESTER_MODEL"; do
  log "ollama pull $model"
  docker exec atrium-ollama-fetch ollama pull "$model" > /dev/null
done
docker rm -f atrium-ollama-fetch > /dev/null
rsync -a "$CACHE/ollama/models" "$CONTROL:$DATA_DIR/ollama/"

# ── E0 の GGUF
for spec in $E0_GGUF; do
  IFS='|' read -r _ repo file <<< "$spec"
  if [ ! -s "$CACHE/gguf/$file" ]; then
    log "downloading $file"
    # 途中で切れても続きから取り直す（HTTP/2 のストリームの切断は既定の再試行の対象外のため --retry-all-errors）
    curl -fsSL --retry 10 --retry-all-errors -C - -o "$CACHE/gguf/$file.part" "https://huggingface.co/$repo/resolve/main/$file"
    mv -f "$CACHE/gguf/$file.part" "$CACHE/gguf/$file"
  fi
done
rsync -a "$CACHE/gguf/" "$CONTROL:$DATA_DIR/gguf/"
