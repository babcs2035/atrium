#!/usr/bin/env bash
# 制御点で専門家ノード 1 台への閉じた系の probe の負荷試験（`atrium node-bench`．Iteration 9）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，deploy でノードを起動し，イメージを publish した後に
# `bash scripts/tasks/remote.sh node_bench <out_name> <placement_run> <target_host> <concurrency>` を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/node_bench.sh <out_name> <placement_run> <target_host> <concurrency> [max_procs]
# 受信箱の配置は results/<placement_run>/placement.json（同じ deploy の `mise run start` の実行が残したもの）．
# concurrency は流す順のカンマ区切り（例: 1,2,4,8,16,32,64）．max_procs は負荷生成のプロセスの数の上限（省略時 4．Iteration 10 は 8）．
# 標準出力とログは results/<out_name>/node_bench.log にも残す．
source "$(dirname "$0")/lib.sh"

USAGE="usage: node_bench.sh <out_name> <placement_run> <target_host> <concurrency> [max_procs]"
OUT=${1:?$USAGE}
PLACEMENT_RUN=${2:?$USAGE}
TARGET=${3:?$USAGE}
CONCURRENCY=${4:?$USAGE}
MAX_PROCS=${5:-4}
mkdir -p "results/$OUT"
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --user "$HOST_UID:$HOST_GID" -e HOME=/tmp \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml node-bench --data-dir /data --out-dir "/results/$OUT" \
  --placement "/results/$PLACEMENT_RUN/placement.json" --target-host "$TARGET" --concurrency "$CONCURRENCY" --max-procs "$MAX_PROCS" \
  2>&1 | tee "results/$OUT/node_bench.log"
