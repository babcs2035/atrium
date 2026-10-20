#!/usr/bin/env bash
# 制御点の config.yaml を，質問者と配置の全ての専門家へ配り直す（コンテナは作り直さない．p0007 D）．
#
# 使い方（操作端末から）:
#   bash scripts/tasks/remote.sh sync_config
# `mise run start` は，配った config.yaml が今のものと一致しているかを確かめる．ノードが読まない値
# （experiment.parallel・experiment.question_limit など）だけを替えて同じ条件の実験を続けるときに，deploy の
# 作り直し（約 2.5 分）を省くために使う．ノードが読む値（routing・answer_mode・retrieval.fusion・answer.*・
# experiment.central_generation など）を替えたときは必ず `mise run deploy` を使う．
source "$(dirname "$0")/lib.sh"

PLACEMENT_TSV="artifacts/$DATASET/placement.tsv"
sync_config_to() {
  local host=$1
  nrsync -az config.yaml "$(ssh_dest "$host"):$REMOTE_DIR/config.yaml"
}

# shellcheck disable=SC2046
run_parallel sync_config sync_config_to "$REQUESTER" $(cut -f1 "$PLACEMENT_TSV")
log "config.yaml synced"
