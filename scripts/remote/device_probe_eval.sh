#!/usr/bin/env bash
# 制御点で device_probe の D ごとの hit@m と D の選択（`atrium device-probe-eval`．Iteration 7）を求め，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh device_probe_eval <out_name> <placement_run> <reference_run>` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/device_probe_eval.sh <out_name> <placement_run> <reference_run>
# 受信箱の配置は results/<placement_run>/placement.json，centroid_probe の値の一致の基準は
# results/<reference_run>/metrics.json（Iteration 1 の cascade-eval）を使う．出力は集計値と受信箱の名前だけ．
source "$(dirname "$0")/lib.sh"

USAGE="usage: device_probe_eval.sh <out_name> <placement_run> <reference_run>"
OUT=${1:?$USAGE}
PLACEMENT_RUN=${2:?$USAGE}
REFERENCE_RUN=${3:?$USAGE}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml device-probe-eval --data-dir /data --out-dir "/results/$OUT" \
  --placement "/results/$PLACEMENT_RUN/placement.json" \
  --reference-metrics "/results/$REFERENCE_RUN/metrics.json"
