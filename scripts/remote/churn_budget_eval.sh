#!/usr/bin/env bash
# 制御点で device_probe の D ごとに補う変種と補わない構成を比べるオフラインの評価（`atrium churn-budget-eval`．Iteration 19）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh churn_budget_eval <out_name> <placement_run> <refill_run>` を実行し，
# results/<out_name> を rsync で取得する）:
#   bash scripts/remote/churn_budget_eval.sh <out_name> <placement_run> <refill_run>
# 受信箱の配置は results/<placement_run>/placement.json，G1 の基準は results/<refill_run>/metrics.json（Iteration 18 の
# churn-refill-eval）を使う．G1 で Iteration 18 の値と比べるので，コンテナの条件は churn_refill_eval.sh と同じにしている．
source "$(dirname "$0")/lib.sh"

USAGE="usage: churn_budget_eval.sh <out_name> <placement_run> <refill_run>"
OUT=${1:?$USAGE}
PLACEMENT_RUN=${2:?$USAGE}
REFILL_RUN=${3:?$USAGE}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml churn-budget-eval --data-dir /data --out-dir "/results/$OUT" \
  --placement "/results/$PLACEMENT_RUN/placement.json" \
  --refill-metrics "/results/$REFILL_RUN/metrics.json"
