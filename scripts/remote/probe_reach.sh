#!/usr/bin/env bash
# 制御点で centroid_probe の K ごとの届くデバイスの数と再現率（`atrium probe-reach`．Iteration 4）を求め，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh probe_reach <out_name> <placement_run>` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/probe_reach.sh <out_name> <placement_run>
# 受信箱の配置は results/<placement_run>/placement.json を使う．出力は集計値と受信箱の名前だけ．
source "$(dirname "$0")/lib.sh"

USAGE="usage: probe_reach.sh <out_name> <placement_run>"
OUT=${1:?$USAGE}
PLACEMENT_RUN=${2:?$USAGE}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml probe-reach --data-dir /data --out-dir "/results/$OUT" \
  --placement "/results/$PLACEMENT_RUN/placement.json"
