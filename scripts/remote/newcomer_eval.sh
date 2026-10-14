#!/usr/bin/env bash
# 制御点で 150 人での新参者の評価（p0005 T3．`atrium newcomer-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh newcomer_eval <out_name> <reference_run> [n_newcomers]` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/newcomer_eval.sh <out_name> <reference_run> [n_newcomers]
# 退行の確認の基準は results/<reference_run>/metrics.json（EQ2）．再学習したルーターは <data_dir>/enronqa/newcomer/ に置く
# （既存の router/ とは別．results/ には評価の値だけを書く）．
source "$(dirname "$0")/lib.sh"

USAGE="usage: newcomer_eval.sh <out_name> <reference_run> [n_newcomers]"
OUT=${1:?$USAGE}
REFERENCE_RUN=${2:?$USAGE}
N_NEWCOMERS=${3:-30}
mkdir -p "$DATA_DIR/enronqa/newcomer"
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$DATA_DIR/enronqa/newcomer:/newcomer" \
  -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml newcomer-eval --data-dir /data --out-dir "/results/$OUT" \
  --reference-metrics "/results/$REFERENCE_RUN/metrics.json" --work-dir /newcomer --n-newcomers "$N_NEWCOMERS"
