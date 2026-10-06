#!/usr/bin/env bash
# `mise run start [run_id]`: 実験実行．結果を results/<run_id>/ に回収する．
# run_id を省略すると現在時刻（YYYYMMDD_HHMMSS）にする．
#
# 制御点の scripts/remote/start.sh を setsid nohup で切り離して起動し，終了の印（.exit）を待つ．
# 操作端末との接続が切れても実験は続くので，同じ run_id で `mise run start <run_id>` を実行すれば待ち直せる．
# 前回が失敗で終わった run_id を渡すと，続きから再開する（E1 は成功済みの質問を，E0 は完了済みの計測を飛ばす）．
source "$(dirname "$0")/lib.sh"

RUN_ID=${1:-$(date +%Y%m%d_%H%M%S)}
POLL_INTERVAL_S=60
RDIR="$REMOTE_DIR/results/$RUN_ID"

# 制御点での状態: running（実行中）／終了コード（.exit の中身）／none（未起動，または印を残さずに止まった）
remote_state() {
  ssh "$CONTROL" "if pgrep -f 'scripts/remote/start.sh $RUN_ID\$' > /dev/null; then echo running; \
    elif [ -f $RDIR/.exit ]; then cat $RDIR/.exit; else echo none; fi"
}

sync_to_control
log "run $RUN_ID (kind=$KIND dataset=$DATASET routing=$ROUTING answer_mode=$ANSWER_MODE)"
state=$(remote_state)
if [ "$state" != running ] && [ "$state" != 0 ]; then
  [ "$state" = none ] || log "previous attempt exited with $state; resuming"
  ssh "$CONTROL" "cd $REMOTE_DIR && mkdir -p $RDIR && rm -f $RDIR/.exit && \
    setsid nohup bash scripts/remote/start.sh $RUN_ID >> $RDIR/start.log 2>&1 < /dev/null &"
  sleep 5
fi

while true; do
  state=$(remote_state || echo unknown)
  case "$state" in
    running | unknown)
      log "$(ssh "$CONTROL" "tail -n 1 $RDIR/start.log 2> /dev/null" | cut -c1-150 || true)"
      sleep "$POLL_INTERVAL_S"
      ;;
    none)
      log "start.sh is not running and left no exit code (see $RDIR/start.log on $CONTROL)" >&2
      exit 1
      ;;
    *) break ;;
  esac
done
mkdir -p "results/$RUN_ID"
rsync -az --exclude .exit "$CONTROL:$RDIR/" "results/$RUN_ID/"
log "results: results/$RUN_ID (exit $state)"
exit "$state"
