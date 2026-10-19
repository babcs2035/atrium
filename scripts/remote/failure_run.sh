#!/usr/bin/env bash
# 制御点で障害の測定（p0007 I6．`atrium failure-run`）を質問者のコンテナで流し，結果を results/<run_id>/ に集める．
#
# 使い方（先に cluster.node_admin: true・routing: hybrid_probe・answer_mode: retrieval_only で `mise run deploy` する．
# 流し方の全体は docs/d0003_experiment_workflow.md の §7）:
#   bash scripts/remote/failure_run.sh <run_id> <git_head> [failure-run の引数...]
#   例: bash scripts/remote/failure_run.sh p0007_e1_failure 0123456789ab --ks 1,3,6,10
# 質問者は制御点自身（cluster.requester = cluster.control）であること（頼みのファイルを同じ results/ で受け渡す）．
#
# --modes に container を含めると，コンテナの中の `atrium failure-run` が results/<run_id>/ctl/req/ に書く頼みを，このスクリプトが
# failure_control.sh の決まり（node サービスの stop・start だけ）で ssh で実行する．止めたノードは，測定が成功・失敗・中断のどれで
# 終わっても，このスクリプトの終了時に必ず再開する（データ・イメージは削除しない）．
source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/failure_control.sh"

RUN_ID=${1:?usage: failure_run.sh <run_id> <git_head> [failure-run args...]}
GIT_HEAD=${2:?usage: failure_run.sh <run_id> <git_head> [failure-run args...]}
shift 2
OUT="results/$RUN_ID"
mkdir -p "$OUT"
WATCHER=""
CTL=""

# node_compose <host> <stop|start>: そのホストの node サービスだけを止める・再開する（failure_control.sh が呼ぶ）
node_compose() {
  nssh "$1" "cd $REMOTE_DIR && docker compose $2 node"
}

cleanup() {
  local rc=$?
  if [ -n "$CTL" ]; then
    touch "$CTL/done"
    if [ -n "$WATCHER" ]; then wait "$WATCHER" 2> /dev/null || true; fi
    if ! control_resume_stopped; then rc=1; fi
  fi
  echo "$rc" > "$OUT/.exit"
}
trap cleanup EXIT

require_node_admin
if ! is_control_host "$REQUESTER"; then
  log "cluster.requester must be the control host for failure-run" >&2
  exit 1
fi
verify_deployed "$GIT_HEAD"
cp "artifacts/$DATASET/placement.json" "$OUT/placement.json"
CTL="$OUT/ctl"
control_init "$CTL" $(cut -f1 "artifacts/$DATASET/placement.tsv")
control_watch &
WATCHER=$!
run_requester_job "$RUN_ID" failure-run --data-dir /data --placement /app/placement.json \
  --out-dir "/app/results/$RUN_ID" --ollama-url "$REQUESTER_OLLAMA_URL" "$@"
log "finished: $OUT"
