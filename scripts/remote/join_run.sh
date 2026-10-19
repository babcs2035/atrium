#!/usr/bin/env bash
# 制御点で参加の測定（p0007 I7．`atrium join-run`）を質問者のコンテナで流し，結果を results/<run_id>/ に集める．
#
# 使い方（先に cluster.node_admin: true・routing: hybrid_probe・answer_mode: retrieval_only で `mise run deploy` する．
# 流し方の全体は docs/d0003_experiment_workflow.md の §7）:
#   bash scripts/remote/join_run.sh <run_id> <git_head> [join-run の引数...]
#   例: bash scripts/remote/join_run.sh p0007_e2_join 0123456789ab --n-devices 3 --delays 0,10,100
# 加えるデバイス（既定は 3 台）は，測定の始めに /v1/admin/availability で離脱させ，参加の時刻に有効にする（ノードは起動したまま）．
# 測定が成功・失敗・中断のどれで終わっても，`atrium join-run` は終了時に全てのノードを有効にする．念のため，このスクリプトも
# 終了時に全ノードを有効にし直す（admin API を直接呼ぶ．データは変えない）．
source "$(dirname "$0")/lib.sh"

RUN_ID=${1:?usage: join_run.sh <run_id> <git_head> [join-run args...]}
GIT_HEAD=${2:?usage: join_run.sh <run_id> <git_head> [join-run args...]}
shift 2
OUT="results/$RUN_ID"
mkdir -p "$OUT"

# restore_availability: 全ノードを有効にする（離脱させたままにしない）
restore_availability() {
  local host
  while IFS=$'\t' read -r host _; do
    curl -fsS --max-time 10 -X POST -H 'content-type: application/json' -d '{"up": true}' \
      "http://$host:$NODE_PORT/v1/admin/availability" > /dev/null \
      || log "could not set $host available; run: curl -X POST http://$host:$NODE_PORT/v1/admin/availability -d '{\"up\": true}'"
  done < "artifacts/$DATASET/placement.tsv"
}
cleanup() {
  local rc=$?
  restore_availability
  echo "$rc" > "$OUT/.exit"
}
trap cleanup EXIT

require_node_admin
verify_deployed "$GIT_HEAD"
cp "artifacts/$DATASET/placement.json" "$OUT/placement.json"
run_requester_job "$RUN_ID" join-run --data-dir /data --placement /app/placement.json \
  --out-dir "/app/results/$RUN_ID" --ollama-url "$REQUESTER_OLLAMA_URL" "$@"
log "finished: $OUT"
