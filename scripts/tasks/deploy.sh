#!/usr/bin/env bash
# `mise run deploy`: 実験準備．config.yaml の現在の値で，シャード・設定・イメージを各ノードへ配り，起動する．
# イメージは毎回 build・push し，今のコードと config.yaml の版にそろえる（変更が無ければキャッシュで済む）．
source "$(dirname "$0")/lib.sh"

sync_to_control
open_registry_tunnel
publish_images
remote deploy
