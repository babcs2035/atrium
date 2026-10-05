#!/usr/bin/env bash
# 制御点から全ノードのコンテナを削除する（破壊的．research-cycle は自動で実行しない）．
#
# 使い方（操作端末の `mise run clean [-- --full]` から呼ばれる）:
#   bash scripts/remote/clean.sh          # コンテナを削除する（シャード・モデル・結果は残す）
#   bash scripts/remote/clean.sh --full   # さらに各ノードの REMOTE_DIR と Ollama のモデルを削除する
# 制御点のデータディレクトリ（DATA_DIR）は，再計算に数日かかるため，このスクリプトでは消さない．
source "$(dirname "$0")/lib.sh"

FULL=false
if [ "${1:-}" = "--full" ]; then FULL=true; fi

clean_host() {
  local host=$1
  if [ "$FULL" = true ]; then
    nssh "$host" "[ -f $REMOTE_DIR/compose.yml ] && (cd $REMOTE_DIR && docker compose --profile run down -v) || true"
    nssh "$host" "rm -rf $REMOTE_DIR"
  else
    nssh "$host" "[ -f $REMOTE_DIR/compose.yml ] && (cd $REMOTE_DIR && docker compose --profile run down) || true"
  fi
  # registry への逆トンネルを閉じる（制御点で動いている ssh -R を止める）
  pkill -f "ssh .*-R $REGISTRY_PORT:localhost:$REGISTRY_PORT .*@$host\$" || true
}
# shellcheck disable=SC2086
run_parallel clean clean_host $EXPERTS $REQUESTER
log "clean done (full=$FULL)"
