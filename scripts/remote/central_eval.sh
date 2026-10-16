#!/usr/bin/env bash
# 制御点で中央集約ベースラインの評価（p0006 P1．`atrium central-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh central_eval <out_name> <c1_run> [<name>=<run> ...]` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/central_eval.sh <out_name> <c1_run> [<name>=<run> ...]
# c1_run は routing=all・snippet_return の実行（結果が制御点の results/ にあること）．<name>=<run> は Recall と露出を並べる実行．
# メールの本文と埋め込みは制御点にだけあるので，評価も制御点で行う（出力は集計値だけ）．
source "$(dirname "$0")/lib.sh"

USAGE="usage: central_eval.sh <out_name> <c1_run> [<name>=<run> ...]"
OUT=${1:?$USAGE}
C1_RUN=${2:?$USAGE}
shift 2
compare_args=()
for spec in "$@"; do
  compare_args+=(--compare-run "${spec%%=*}=/results/${spec#*=}")
done
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --user "$HOST_UID:$HOST_GID" -e HOME=/tmp \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml central-eval --data-dir /data --out-dir "/results/$OUT" \
  --c1-run "/results/$C1_RUN" ${compare_args[@]+"${compare_args[@]}"}
