#!/usr/bin/env bash
# 制御点で二段ルーティング centroid_probe のオフラインの評価（`atrium cascade-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh cascade_eval <out_name> <reference_run>` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/cascade_eval.sh <out_name> <reference_run>
# 退行の確認の基準は results/<reference_run>/metrics.json（EQ2）．出力は集計値だけ．
source "$(dirname "$0")/lib.sh"

USAGE="usage: cascade_eval.sh <out_name> <reference_run>"
OUT=${1:?$USAGE}
REFERENCE_RUN=${2:?$USAGE}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml cascade-eval --data-dir /data --out-dir "/results/$OUT" \
  --reference-metrics "/results/$REFERENCE_RUN/metrics.json"
