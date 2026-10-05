#!/usr/bin/env bash
# `mise run start [run_id]`: 実験実行．結果を results/<run_id>/ に回収する．
# run_id を省略すると現在時刻（YYYYMMDD_HHMMSS）にする．
#
# 制御点の scripts/remote/start.sh を setsid nohup で切り離して起動し，終了の印（.exit）を待つ．
# 操作端末との接続が切れても実験は続くので，同じ run_id で `mise run start <run_id>` を実行すれば待ち直せる．
source "$(dirname "$0")/lib.sh"

RUN_ID=${1:-$(date +%Y%m%d_%H%M%S)}
POLL_INTERVAL_S=60
RDIR="$REMOTE_DIR/results/$RUN_ID"
sync_to_control
log "run $RUN_ID (kind=$KIND dataset=$DATASET routing=$ROUTING answer_mode=$ANSWER_MODE)"
if ! ssh "$CONTROL" "[ -f $RDIR/.exit ] || pgrep -f 'scripts/remote/start.sh $RUN_ID\$' > /dev/null"; then
  ssh "$CONTROL" "cd $REMOTE_DIR && mkdir -p $RDIR && setsid nohup bash scripts/remote/start.sh $RUN_ID \
    >> $RDIR/start.log 2>&1 < /dev/null &"
fi
while ! ssh "$CONTROL" "[ -f $RDIR/.exit ]"; do
  log "$(ssh "$CONTROL" "tail -n 1 $RDIR/start.log 2> /dev/null" | cut -c1-150)"
  sleep "$POLL_INTERVAL_S"
done
status=$(ssh "$CONTROL" "cat $RDIR/.exit")
mkdir -p "results/$RUN_ID"
rsync -az --exclude .exit "$CONTROL:$RDIR/" "results/$RUN_ID/"
log "results: results/$RUN_ID (exit $status)"
exit "$status"
