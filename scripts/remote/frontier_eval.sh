#!/usr/bin/env bash
# 制御点で汎用のルーティング方式のフロンティア評価（p0006 P2・P3．`atrium frontier-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh frontier_eval <out_name> <placement_run|-> [methods] [extra args]` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/frontier_eval.sh <out_name> <placement_run|-> [methods] [--dataset enronqa|feb4rag|medrag] [extra args]
# EnronQA は受信箱の配置（n30）が results/<placement_run>/placement.json にあること．FeB4RAG・MedRAG は配置が要らないので - を渡す．
# methods は all かカンマ区切り（既定 all）．本文の埋め込み・検索の得点は <data_dir>/<dataset>/frontier/ に保存して再利用する
# （既存の成果物とは別の新しいサブディレクトリ．既存のデータは読み取り専用のまま）．出力は集計値だけ（メールの本文は出さない）．
source "$(dirname "$0")/lib.sh"

USAGE="usage: frontier_eval.sh <out_name> <placement_run|-> [methods] [extra args]"
OUT=${1:?$USAGE}
PLACEMENT_RUN=${2:?$USAGE}
METHODS=${3:-all}
shift $(( $# < 3 ? $# : 3 ))
DATASET=enronqa
prev=""
for a in "$@"; do
  if [ "$prev" = "--dataset" ]; then DATASET=$a; fi
  prev=$a
done
placement_args=()
if [ "$PLACEMENT_RUN" != "-" ]; then placement_args=(--placement "/results/$PLACEMENT_RUN/placement.json"); fi
mkdir -p "$DATA_DIR/$DATASET/frontier"
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 \
  -v "$DATA_DIR:/data:ro" -v "$DATA_DIR/$DATASET/frontier:/frontier" \
  -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml frontier-eval --data-dir /data --out-dir "/results/$OUT" \
  ${placement_args[@]+"${placement_args[@]}"} --methods "$METHODS" --work-dir /frontier "$@"
