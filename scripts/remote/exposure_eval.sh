#!/usr/bin/env bash
# 制御点で p0007 B の露出の評価（`atrium exposure-eval`）を行い，results/<out_name>/ に書く．
#
# 使い方（操作端末から，イメージを publish した後に
# `bash scripts/tasks/remote.sh exposure_eval <out_name> <p2p 条件のコンマ区切り> <name>=<run>[:<1 問あたりの値>] ...`
# を実行し，results/<out_name> を rsync で取得する）:
#   bash scripts/remote/exposure_eval.sh <out_name> <p2p> <name>=<run>[:<expected>] ...
# <p2p> は H-B の判定に使う条件の name（コンマ区切り）．<run> は制御点の results/ の実行．
# <expected> は journal の 1 問あたり外に出た文書（実機の記録との照合用）．
# メールの埋め込みは制御点にだけあるので，評価も制御点で行う（出力は集計値と文書の数だけ．本文は含めない）．
source "$(dirname "$0")/lib.sh"

USAGE="usage: exposure_eval.sh <out_name> <p2p,...> <name>=<run>[:<expected>] ..."
OUT=${1:?$USAGE}
P2P=${2:?$USAGE}
shift 2
[ "$#" -gt 0 ] || { echo "$USAGE" >&2; exit 2; }
args=()
for name in ${P2P//,/ }; do args+=(--p2p "$name"); done
for spec in "$@"; do
  name=${spec%%=*}
  rest=${spec#*=}
  args+=(--run "$name=/results/${rest%%:*}")
  if [[ "$rest" == *:* ]]; then args+=(--expected-per-query "$name=${rest#*:}"); fi
done
docker pull -q "$IMAGE_FULL" > /dev/null
docker run --rm --user "$HOST_UID:$HOST_GID" -e HOME=/tmp \
  -v "$DATA_DIR:/data:ro" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
  "$IMAGE_FULL" atrium --config /app/config.yaml exposure-eval --data-dir /data --out-dir "/results/$OUT" \
  --attribution-metrics /results/p0006_p2_iter2/metrics.json "${args[@]}"
