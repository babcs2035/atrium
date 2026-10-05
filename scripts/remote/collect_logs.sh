#!/usr/bin/env bash
# 制御点で，配置にある専門家ノードのログを results/<run_id>/logs/ に集める．
#
# 使い方（操作端末の `mise run analyze` から呼ばれる）:
#   bash scripts/remote/collect_logs.sh <run_id> <dataset>
source "$(dirname "$0")/lib.sh"

RUN_ID=${1:?usage: collect_logs.sh <run_id> <dataset>}
RUN_DATASET=${2:?usage: collect_logs.sh <run_id> <dataset>}
LOG_DIR="results/$RUN_ID/logs"
mkdir -p "$LOG_DIR"
TSV="artifacts/$RUN_DATASET/placement.tsv"
if [ ! -f "$TSV" ]; then
  log "no placement for $RUN_DATASET; skip"
  exit 0
fi
while IFS=$'\t' read -r host _; do
  nssh "$host" "cd $REMOTE_DIR && docker compose logs --no-color node" > "$LOG_DIR/$host.log" 2>&1 \
    || log "could not collect logs from $host"
done < "$TSV"
log "logs: $LOG_DIR"
