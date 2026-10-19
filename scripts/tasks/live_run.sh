#!/usr/bin/env bash
# 実機の動的な測定（p0007 I5〜I7）を制御点で流し，結果を results/<run_id>/ に回収する．
#
# 使い方（先に cluster.node_admin: true で `mise run deploy` する．docs/d0003_experiment_workflow.md の §7）:
#   bash scripts/tasks/live_run.sh <freshness_run|failure_run|join_run> <run_id> [そのコマンドの引数...]
#   例: bash scripts/tasks/live_run.sh freshness_run p0007_c_freshness --intervals 0,60,600,inf
# 制御点の scripts/remote/<script>.sh を setsid nohup で切り離して起動し，終了の印（.exit）を 1 分ごとに待つ．
# 操作端末との接続が切れても測定は続き，同じ引数で実行し直すと待ち直す．終了コードはスクリプトの終了コード．
source "$(dirname "$0")/lib.sh"

SCRIPT=${1:?usage: live_run.sh <freshness_run|failure_run|join_run> <run_id> [args...]}
RUN_ID=${2:?usage: live_run.sh <freshness_run|failure_run|join_run> <run_id> [args...]}
shift 2
case "$SCRIPT" in
  freshness_run | failure_run | join_run) ;;
  *) echo "unknown script: $SCRIPT" >&2; exit 2 ;;
esac
POLL_INTERVAL_S=60
RDIR="$REMOTE_DIR/results/$RUN_ID"

# 制御点での状態: running（実行中）／終了コード（.exit の中身）／none（未起動，または印を残さずに止まった）
remote_state() {
  # 先頭の [s] は，この pgrep を実行するシェル自身の引数に一致しないための印（正規表現の [s] は s に一致し，文字列 "[s]" には一致しない）
  ssh "$CONTROL" "if pgrep -f '[s]cripts/remote/$SCRIPT.sh $RUN_ID ' > /dev/null; then echo running; \
    elif [ -f $RDIR/.exit ]; then cat $RDIR/.exit; else echo none; fi"
}

sync_to_control
log "$SCRIPT $RUN_ID (git $GIT_HEAD)"
state=$(remote_state)
if [ "$state" != running ] && [ "$state" != 0 ]; then
  ssh "$CONTROL" "cd $REMOTE_DIR && mkdir -p $RDIR && rm -f $RDIR/.exit && \
    setsid nohup bash scripts/remote/$SCRIPT.sh $RUN_ID $GIT_HEAD $* >> $RDIR/start.log 2>&1 < /dev/null &"
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
      log "$SCRIPT.sh is not running and left no exit code (see $RDIR/start.log on $CONTROL)" >&2
      exit 1
      ;;
    *) break ;;
  esac
done
mkdir -p "results/$RUN_ID"
rsync -az --exclude .exit "$CONTROL:$RDIR/" "results/$RUN_ID/"
log "results: results/$RUN_ID (exit $state)"
exit "$state"
