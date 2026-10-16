#!/usr/bin/env bash
# 制御点で離脱の通知の遅れ L_leave ごとのオフラインの評価（`atrium churn-leave-eval`．Iteration 17）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh churn_leave_eval <out_name> <placement_run> <join_run>` を実行し，
# results/<out_name> を rsync で取得する）:
#   bash scripts/remote/churn_leave_eval.sh <out_name> <placement_run> <join_run>
# 受信箱の配置は results/<placement_run>/placement.json，G1・G2 の基準は results/<join_run>/metrics.json（Iteration 16 の
# churn-join-eval）を使う．G1・G2 で Iteration 16 の値と比べるので，コンテナの条件は churn_join_eval.sh と同じにしている．
source "$(dirname "$0")/lib.sh"

USAGE="usage: churn_leave_eval.sh <out_name> <placement_run> <join_run>"
OUT=${1:?$USAGE}
PLACEMENT_RUN=${2:?$USAGE}
JOIN_RUN=${3:?$USAGE}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml churn-leave-eval --data-dir /data --out-dir "/results/$OUT" \
  --placement "/results/$PLACEMENT_RUN/placement.json" \
  --join-metrics "/results/$JOIN_RUN/metrics.json"
