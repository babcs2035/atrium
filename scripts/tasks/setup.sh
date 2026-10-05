#!/usr/bin/env bash
# `mise run setup`: 環境構築．
#   1. 操作端末の Python 環境（uv sync．分析とテストに使う）
#   2. 制御点への同期・registry・イメージの build と push
#   3. 制御点でのデータ準備の開始（バックグラウンド．進み具合は `mise run data-status`）
# 引数でデータ準備の対象を絞れる（例: mise run setup -- medrag）．省略時は feb4rag と medrag．
source "$(dirname "$0")/lib.sh"

log "uv sync (local)"
uv sync --extra requester
log "sync to $CONTROL:$REMOTE_DIR"
sync_to_control
remote setup "$@"
