#!/usr/bin/env bash
# 制御点で A2 のオフラインの評価（p0007．`atrium fusion-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh fusion_eval <out_name> <reference>` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/fusion_eval.sh <out_name> <reference>
# reference は p0006 P1 の central-eval の出力（results/p0006_p1_central_eval_v2 など．制御点の results/ にあること）．
# 受信箱の中・中央の，密ベクトル・BM25・RRF の Recall@K（dev・test）と，回答まで流すゲートの判定を出す．出力は集計値だけ．
# 整合の確認（p0006 P1 の中央の値との一致）が崩れたときは終了コード 1．
source "$(dirname "$0")/lib.sh"

USAGE="usage: fusion_eval.sh <out_name> <reference>"
OUT=${1:?$USAGE}
REFERENCE=${2:?$USAGE}
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --user "$HOST_UID:$HOST_GID" -e HOME=/tmp \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml fusion-eval --data-dir /data \
  --out-dir "/results/$OUT" --reference "/results/$REFERENCE"
