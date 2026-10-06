#!/usr/bin/env bash
# 制御点から全ノードのコンテナを削除する（破壊的．research-cycle は自動で実行しない）．
#
# 使い方（操作端末の `mise run clean [-- --full]` から呼ばれる）:
#   bash scripts/remote/clean.sh          # コンテナを削除する（シャード・モデル・結果は残す）
#   bash scripts/remote/clean.sh --full   # さらに各ノードの REMOTE_DIR と Ollama のモデルを削除する（制御点は質問者の資材だけ）
# 制御点のデータディレクトリ（DATA_DIR）は，再計算に数時間かかるため，このスクリプトでは消さない．
# データ準備の実行中は，GPU PC の作業用の写し（REMOTE_DIR/embed-work）を壊さないよう何もせずに止まる．
source "$(dirname "$0")/lib.sh"

FULL=false
if [ "${1:-}" = "--full" ]; then FULL=true; fi

if prepare_running; then
  log "data preparation is running; refusing to clean (see: mise run data-status)" >&2
  exit 1
fi

clean_host() {
  local host=$1
  if [ "$FULL" = true ]; then
    nssh "$host" "[ -f $REMOTE_DIR/compose.yml ] && (cd $REMOTE_DIR && docker compose --profile run down -v) || true"
    if is_control_host "$host"; then
      # 制御点の REMOTE_DIR は本リポジトリの作業ディレクトリでもあるので，質問者の資材だけを消す
      local items="data hf-cache ollama compose.yml .env placement.json"
      local cmd="cd $REMOTE_DIR && rm -rf $items"
      nssh "$host" "$cmd 2> /dev/null || sudo -n bash -c '$cmd'"
    else
      # 以前 root で動いていたコンテナが作ったファイルが残っていれば sudo で消す
      nssh "$host" "rm -rf $REMOTE_DIR 2> /dev/null || sudo -n rm -rf $REMOTE_DIR"
    fi
  else
    nssh "$host" "[ -f $REMOTE_DIR/compose.yml ] && (cd $REMOTE_DIR && docker compose --profile run down) || true"
  fi
  # registry への逆トンネルを閉じる（制御点で動いている ssh -R を止める）
  pkill -f "ssh .*-R $REGISTRY_PORT:localhost:$REGISTRY_PORT .*@$host\$" || true
}
# 専門家・質問者に加え，データ準備で使った GPU PC も対象にする（逆トンネルと作業用の写しが残りうるため）
# shellcheck disable=SC2086
hosts=$(printf '%s\n' $EXPERTS $REQUESTER $GPU_WORKERS | sort -u)
# shellcheck disable=SC2086
run_parallel clean clean_host $hosts
log "clean done (full=$FULL)"
