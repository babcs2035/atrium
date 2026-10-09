#!/usr/bin/env bash
# 制御点で RQ-A のオフラインの評価（p0004 EQ2．`atrium advert-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から `bash scripts/tasks/remote.sh advert_eval <out_name>` の後，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/advert_eval.sh <out_name>
# メールの本文と埋め込みは制御点にだけあるので，評価も制御点で行う（出力は集計値だけ）．
source "$(dirname "$0")/lib.sh"

OUT=${1:?usage: advert_eval.sh <out_name>}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml advert-eval --data-dir /data --out-dir "/results/$OUT"
