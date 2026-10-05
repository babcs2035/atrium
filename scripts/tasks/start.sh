#!/usr/bin/env bash
# `mise run start [run_id]`: 実験実行．結果を results/<run_id>/ に回収する．
# run_id を省略すると現在時刻（YYYYMMDD_HHMMSS）にする．途中で切れた実行を待ち直すときは同じ run_id を渡す．
source "$(dirname "$0")/lib.sh"

RUN_ID=${1:-$(date +%Y%m%d_%H%M%S)}
sync_to_control
log "run $RUN_ID (kind=$KIND dataset=$DATASET routing=$ROUTING answer_mode=$ANSWER_MODE)"
status=0
remote start "$RUN_ID" || status=$?
# 失敗しても途中までの結果とログは回収する
mkdir -p "results/$RUN_ID"
rsync -az "$CONTROL:$REMOTE_DIR/results/$RUN_ID/" "results/$RUN_ID/"
log "results: results/$RUN_ID"
exit "$status"
