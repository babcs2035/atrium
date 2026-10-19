#!/usr/bin/env bash
# 制御点で routing=central_local の索引と C1 の上位文書 ID の一致の確認（p0007 I4．`atrium central-local-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh central_local_eval <out_name> <c1_run>` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/central_local_eval.sh <out_name> <c1_run>
# c1_run は routing=all・snippet_return の実行（results/p0006_c1_all_pernode など．制御点の results/ にあること）．
# 判定は厳密な集合の一致 ≥ 0.999．満たさず，同点を許す一致 ≥ 0.999 なら同点を許す判定に自動で切り替える（D9）．
# 出力は集計値と，索引を作る費用（秒）だけ（メールの本文は含まない）．
source "$(dirname "$0")/lib.sh"

USAGE="usage: central_local_eval.sh <out_name> <c1_run>"
OUT=${1:?$USAGE}
C1_RUN=${2:?$USAGE}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --user "$HOST_UID:$HOST_GID" -e HOME=/tmp \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml central-local-eval --data-dir /data \
  --out-dir "/results/$OUT" --c1-run "/results/$C1_RUN"
