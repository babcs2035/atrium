#!/usr/bin/env bash
# `mise run deploy`: 実験準備．config.yaml の現在の値で，シャード・設定・イメージを各ノードへ配り，起動する．
# コードを変えた場合はイメージを作り直すため，先に `mise run setup` を実行する
# （データ準備は冪等なので，終わっていれば何もしない）．
source "$(dirname "$0")/lib.sh"

sync_to_control
remote deploy
