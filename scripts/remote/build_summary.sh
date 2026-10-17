#!/usr/bin/env bash
# 制御点で term_probe・hybrid_probe の要約の成果物とオフラインの選択を作る（p0006 P4．`atrium build-summary`）．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh build_summary <out_name> <placement_run>` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/build_summary.sh <out_name> <placement_run>
# 要約は <data_dir>/enronqa/summary/（新しいサブディレクトリ．既存の成果物は変えない）へ，オフラインの選択は results/<out_name>/ へ書く．
# 本文の埋め込みは frontier-eval のキャッシュ（<data_dir>/enronqa/frontier/）を再利用する．
source "$(dirname "$0")/lib.sh"

USAGE="usage: build_summary.sh <out_name> <placement_run>"
OUT=${1:?$USAGE}
PLACEMENT_RUN=${2:?$USAGE}
mkdir -p "$DATA_DIR/enronqa/summary" "$DATA_DIR/enronqa/frontier"
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$DATA_DIR/enronqa/summary:/summary" -v "$DATA_DIR/enronqa/frontier:/frontier" \
  -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml build-summary --data-dir /data \
  --placement "/results/$PLACEMENT_RUN/placement.json" --work-dir /frontier \
  --summary-dir /summary --out-dir "/results/$OUT"
