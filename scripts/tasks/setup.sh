#!/usr/bin/env bash
# `mise run setup`: 環境構築．
#   1. 操作端末の Python 環境（uv sync．分析とテストに使う）
#   2. 制御点への同期と registry の起動
#   3. 操作端末でイメージ（node / full）を build し，SSH の転送を通して制御点の registry へ push する
#   4. 制御点でイメージを取得し，データ準備をバックグラウンドで始める（進み具合は `mise run data-status`）
# 引数でデータ準備の対象を絞れる（例: mise run setup -- medrag）．省略時は feb4rag と medrag．
source "$(dirname "$0")/lib.sh"

# 操作端末から制御点の registry へつなぐローカルのポート（操作端末の 5000 番などと衝突しない番号）
LOCAL_REGISTRY_PORT=15000
PUSH_RETRY_WAIT_S=300
TUNNEL_SOCKET="$REPO_ROOT/artifacts/registry-tunnel.sock"

log "uv sync (local)"
uv sync --extra requester
log "sync to $CONTROL:$REMOTE_DIR"
sync_to_control
remote setup registry

ssh -fNT -M -S "$TUNNEL_SOCKET" -o ExitOnForwardFailure=yes \
  -L "$LOCAL_REGISTRY_PORT:localhost:$REGISTRY_PORT" "$CONTROL"
trap 'ssh -S "$TUNNEL_SOCKET" -O exit "$CONTROL" 2> /dev/null || true' EXIT
for target in node full; do
  image="localhost:$LOCAL_REGISTRY_PORT/atrium-$target:latest"
  log "building $image (git $GIT_HEAD)"
  docker build --target "$target" --build-arg GIT_HEAD="$GIT_HEAD" -t "$image" .
  # 数 GB の層は registry が digest を検証する間にクライアントが応答待ちで打ち切ることがある．
  # すぐ送り直すと検証中の upload と重なって blob が壊れたため，検証が終わるまで待ってから送り直す
  for attempt in 1 2 3; do
    if docker push -q "$image"; then break; fi
    if [ "$attempt" = 3 ]; then exit 1; fi
    log "push failed; retrying after ${PUSH_RETRY_WAIT_S}s ($attempt/3)"
    sleep "$PUSH_RETRY_WAIT_S"
  done
done

remote setup start "$@"
