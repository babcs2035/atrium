#!/usr/bin/env bash
# 制御点でのデータ準備（scripts/remote/prepare_data.sh）の進み具合を表示する．
#
# 使い方（操作端末の `mise run data-status` から呼ばれる）:
#   bash scripts/remote/data_status.sh
source "$(dirname "$0")/lib.sh"

LOG_DIR="$DATA_DIR/logs"
state=$(cat "$LOG_DIR/prepare.status" 2> /dev/null || echo "not started")
if [ -f "$LOG_DIR/prepare.pid" ] && kill -0 "$(cat "$LOG_DIR/prepare.pid")" 2> /dev/null; then
  state="$state (pid $(cat "$LOG_DIR/prepare.pid") alive)"
fi
log "prepare_data: $state"
# 進捗バー（\r で上書きされる長い行）は末尾だけを見せる
[ -f "$LOG_DIR/prepare.log" ] && tail -n 5 "$LOG_DIR/prepare.log" | tr '\r' '\n' | tail -n 5 | cut -c1-200 | sed 's/^/  | /'

for d in feb4rag medrag; do
  dir="$DATA_DIR/$d"
  [ -d "$dir" ] || continue
  echo "== $d"
  for f in benchmark/questions.jsonl manifest.json queries/query_ids.json labels/labels.json labels/split.json router/router.pt; do
    if [ -e "$dir/$f" ]; then echo "  [x] $f"; else echo "  [ ] $f"; fi
  done
  if [ "$d" = medrag ] && [ -d "$dir/corpus" ]; then
    for src in "$dir"/corpus/*/; do
      # まだディレクトリが無い段階では find が失敗するので，0 件として数える
      # 空の断片ファイル（上流でも 0 バイトのもの．埋め込まない）は数えない
      n_chunk=$(find "$src/chunk" -name '*.jsonl' -size +0 2> /dev/null | wc -l || true)
      n_emb=$(find "$src/emb" -name '*.f16.npy' ! -name '*.tmp.npy' 2> /dev/null | wc -l || true)
      echo "  $(basename "$src"): embedded $n_emb / $n_chunk files (on the control host)"
    done
  fi
  if [ "$d" = feb4rag ] && [ -f "$LOG_DIR/data-feb4rag.log" ]; then
    tail -n 2 "$LOG_DIR/data-feb4rag.log" | tr '\r' '\n' | tail -n 2 | cut -c1-200 | sed 's/^/  | /'
  fi
done

if [ -d artifacts/logs/embed ]; then
  echo "== embedding workers (last line of each log)"
  for f in artifacts/logs/embed/*.log; do
    printf '  %-16s %s\n' "$(basename "$f" .log)" "$(tail -n 1 "$f" | cut -c1-150)"
  done
fi
df -h "$DATA_DIR" | tail -1
