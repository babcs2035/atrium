#!/usr/bin/env bash
# 制御点で新しいメールへの追従の測定（p0007 I5．`atrium freshness-run`）を質問者のコンテナで流し，結果を results/<run_id>/ に集める．
#
# 使い方（先に cluster.node_admin: true・routing: hybrid_probe・answer_mode: retrieval_only で `mise run deploy` する．
# 流し方の全体は docs/d0003_experiment_workflow.md の §7）:
#   bash scripts/remote/freshness_run.sh <run_id> <git_head> [freshness-run の引数...]
#   例: bash scripts/remote/freshness_run.sh p0007_c_freshness 0123456789ab --intervals 0,60,600,inf
# git_head は操作端末のコミット（質問者のイメージの照合に使う．`mise run start` と同じ）．引数は `atrium freshness-run --help`．
# 長く（T = 600 秒を含めて 15 分以上）かかるので，操作端末からは setsid nohup で切り離して起動し，
# results/<run_id>/.exit（終了コード）を待つ．測定の間はノードのデータ（受信箱）を変えない（メールを足した行は再実行の先頭で外し直す）．
source "$(dirname "$0")/lib.sh"

RUN_ID=${1:?usage: freshness_run.sh <run_id> <git_head> [freshness-run args...]}
GIT_HEAD=${2:?usage: freshness_run.sh <run_id> <git_head> [freshness-run args...]}
shift 2
OUT="results/$RUN_ID"
mkdir -p "$OUT"
trap 'echo $? > "$OUT/.exit"' EXIT

require_node_admin
verify_deployed "$GIT_HEAD"
cp "artifacts/$DATASET/placement.json" "$OUT/placement.json"
run_requester_job "$RUN_ID" freshness-run --data-dir /data --placement /app/placement.json \
  --out-dir "/app/results/$RUN_ID" --ollama-url "$REQUESTER_OLLAMA_URL" "$@"
log "finished: $OUT"
