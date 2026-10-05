#!/usr/bin/env bash
# 制御点から全ノードのコンテナを停止する（削除はしない．ログ・モデル・シャードは残る）．
#
# 使い方（操作端末の `mise run stop` から呼ばれる）:
#   bash scripts/remote/stop.sh
# 実験の後に実行して，専門家のメモリと質問者の VRAM を解放する．次の実験は `mise run deploy` から始める．
source "$(dirname "$0")/lib.sh"

stop_host() {
  local host=$1
  nssh "$host" "[ -f $REMOTE_DIR/compose.yml ] && cd $REMOTE_DIR && docker compose --profile run stop || true"
}
# shellcheck disable=SC2086
run_parallel stop stop_host $EXPERTS $REQUESTER
log "stop done"
