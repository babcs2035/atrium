#!/usr/bin/env bash
# 制御点でのデータ準備（コンテナ atrium-data）の進み具合を表示する．
#
# 使い方（操作端末の `mise run data-status` から呼ばれる）:
#   bash scripts/remote/data_status.sh
source "$(dirname "$0")/lib.sh"

state=$(docker inspect -f '{{.State.Status}} (exit {{.State.ExitCode}})' atrium-data 2>/dev/null || echo "not started")
log "atrium-data: $state"
for d in feb4rag medrag; do
  dir="$DATA_DIR/$d"
  [ -d "$dir" ] || continue
  echo "== $d"
  for f in benchmark/questions.jsonl manifest.json labels/labels.json labels/split.json router/router.pt; do
    if [ -e "$dir/$f" ]; then echo "  [x] $f"; else echo "  [ ] $f"; fi
  done
  if [ "$d" = medrag ] && [ -d "$dir/corpus" ]; then
    for src in "$dir"/corpus/*/; do
      n_chunk=$(find "$src/chunk" -name '*.jsonl' 2>/dev/null | wc -l)
      n_emb=$(find "$src/emb" -name '*.f16.npy' 2>/dev/null | wc -l)
      echo "  $(basename "$src"): embedded $n_emb / $n_chunk files"
    done
  fi
  [ -f "$DATA_DIR/logs/data-$d.log" ] && tail -n 3 "$DATA_DIR/logs/data-$d.log" | sed 's/^/  | /'
done
df -h "$DATA_DIR" | tail -1
