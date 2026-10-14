#!/usr/bin/env bash
# 制御点で持ち主推定の入力からヘッダーを除いた測り直し（Iteration 2 の R1・R2．`atrium header-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh header_eval <out_name> <reference_run> [t2_run] [cascade_run]` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/header_eval.sh <out_name> <reference_run> [t2_run] [cascade_run]
# 退行の確認の基準は results/<reference_run>/metrics.json（EQ2），results/<t2_run>/metrics.json（T2．既定 20261014_t2_owner_eval），
# results/<cascade_run>/metrics.json（centroid_probe．既定 20261014_iter1_cascade_eval）．出力は集計値と語だけ（メールの本文は出さない）．
source "$(dirname "$0")/lib.sh"

USAGE="usage: header_eval.sh <out_name> <reference_run> [t2_run] [cascade_run]"
OUT=${1:?$USAGE}
REFERENCE_RUN=${2:?$USAGE}
T2_RUN=${3:-20261014_t2_owner_eval}
CASCADE_RUN=${4:-20261014_iter1_cascade_eval}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml header-eval --data-dir /data --out-dir "/results/$OUT" \
  --reference-metrics "/results/$REFERENCE_RUN/metrics.json" \
  --t2-metrics "/results/$T2_RUN/metrics.json" --cascade-metrics "/results/$CASCADE_RUN/metrics.json"
