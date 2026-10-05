#!/usr/bin/env bash
# `mise run setup`: 環境構築．
#   1. 操作端末の Python 環境（uv sync．分析とテストに使う）
#   2. 制御点への同期と registry の起動
#   3. 操作端末でイメージ（node / full）を build し，SSH の転送を通して制御点の registry へ push する．
#      外部のイメージ・Ollama のモデル・GGUF も操作端末で取得して制御点へ送る（scripts/tasks/fetch_assets.sh）
#   4. 制御点でイメージを取得し，データ準備をバックグラウンドで始める（進み具合は `mise run data-status`）
# 引数でデータ準備の対象を絞れる（例: mise run setup -- medrag）．省略時は feb4rag と medrag．
source "$(dirname "$0")/lib.sh"

log "uv sync (local)"
uv sync --extra requester
log "sync to $CONTROL:$REMOTE_DIR"
sync_to_control
remote setup registry
open_registry_tunnel
publish_images
bash "$REPO_ROOT/scripts/tasks/fetch_assets.sh" "$LOCAL_REGISTRY_PORT"

remote setup start "$@"
