#!/usr/bin/env bash
# 制御点で二段の方式の参加・離脱への頑健さのオフラインの評価（`atrium churn-cascade-eval`．Iteration 15）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh churn_cascade_eval <out_name> <placement_run> <device_probe_run> <churn_run>` を実行し，
# results/<out_name> を rsync で取得する）:
#   bash scripts/remote/churn_cascade_eval.sh <out_name> <placement_run> <device_probe_run> <churn_run>
# 受信箱の配置は results/<placement_run>/placement.json，G1 の基準は results/<device_probe_run>/metrics.json（Iteration 7 の
# device-probe-eval），G2 の基準は results/<churn_run>/metrics.json（EQ7a の churn-eval）を使う．
# G2 で EQ7a の値と比べるので，コンテナの条件は churn_eval.sh と同じにしている．
source "$(dirname "$0")/lib.sh"

USAGE="usage: churn_cascade_eval.sh <out_name> <placement_run> <device_probe_run> <churn_run>"
OUT=${1:?$USAGE}
PLACEMENT_RUN=${2:?$USAGE}
DEVICE_PROBE_RUN=${3:?$USAGE}
CHURN_RUN=${4:?$USAGE}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml churn-cascade-eval --data-dir /data --out-dir "/results/$OUT" \
  --placement "/results/$PLACEMENT_RUN/placement.json" \
  --device-probe-metrics "/results/$DEVICE_PROBE_RUN/metrics.json" \
  --churn-metrics "/results/$CHURN_RUN/metrics.json"
