#!/usr/bin/env bash
# 制御点で公開情報をヘッダーを除いた本文から作り直した RQ-A の測り直し（Iteration 3．`atrium body-advert-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh body_advert_eval <out_name> <reference_run> [header_run] [cascade_run]` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/body_advert_eval.sh <out_name> <reference_run> [header_run] [cascade_run]
# 退行の確認の基準は results/<reference_run>/metrics.json（EQ2），results/<header_run>/metrics.json（header-eval．既定 20261014_iter2_header_eval），
# results/<cascade_run>/metrics.json（centroid_probe．既定 20261014_iter1_cascade_eval）．本文の埋め込みは保存しない（データは読み取り専用）．
# 出力は集計値だけ（メールの本文は出さない）．
source "$(dirname "$0")/lib.sh"

USAGE="usage: body_advert_eval.sh <out_name> <reference_run> [header_run] [cascade_run]"
OUT=${1:?$USAGE}
REFERENCE_RUN=${2:?$USAGE}
HEADER_RUN=${3:-20261014_iter2_header_eval}
CASCADE_RUN=${4:-20261014_iter1_cascade_eval}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml body-advert-eval --data-dir /data --out-dir "/results/$OUT" \
  --reference-metrics "/results/$REFERENCE_RUN/metrics.json" \
  --header-metrics "/results/$HEADER_RUN/metrics.json" --cascade-metrics "/results/$CASCADE_RUN/metrics.json"
