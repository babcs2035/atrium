#!/usr/bin/env bash
# 制御点で汎用のルーティング方式のフロンティア評価（p0006 P2．`atrium frontier-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh frontier_eval <out_name> <placement_run> [methods] [extra args]` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/frontier_eval.sh <out_name> <placement_run> [methods] [extra args]
# 受信箱の配置（n30）は results/<placement_run>/placement.json．methods は all かカンマ区切り（既定 all）．
# 本文の埋め込みは <data_dir>/enronqa/frontier/ に保存して再利用する（既存の成果物とは別の新しいサブディレクトリ．
# 既存のデータは読み取り専用のまま）．出力は集計値だけ（メールの本文は出さない）．
source "$(dirname "$0")/lib.sh"

USAGE="usage: frontier_eval.sh <out_name> <placement_run> [methods] [extra args]"
OUT=${1:?$USAGE}
PLACEMENT_RUN=${2:?$USAGE}
METHODS=${3:-all}
shift $(( $# < 3 ? $# : 3 ))
mkdir -p "$DATA_DIR/enronqa/frontier"
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$DATA_DIR/enronqa/frontier:/frontier" \
  -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml frontier-eval --data-dir /data --out-dir "/results/$OUT" \
  --placement "/results/$PLACEMENT_RUN/placement.json" --methods "$METHODS" --work-dir /frontier "$@"
