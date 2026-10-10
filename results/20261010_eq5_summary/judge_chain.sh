#!/usr/bin/env bash
# EQ5 の 6 実行を判定モデル（qwen3:8b）で採点し，analyze する．各段の終了コードを $LOG に残す．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO"
LOG=$1
RUNS="20261009_eq5_b1 20261009_eq5_b1_flood 20261009_eq5_b2 20261009_eq5_b2_1p7b 20261009_eq5_b3 20261009_eq5_b4"
echo "[$(date +%F_%T)] BEGIN judge up" >> "$LOG"
bash scripts/tasks/judge.sh up qwen3:8b > "$LOG.up" 2>&1; rc=$?
echo "[$(date +%T)] up=$rc" >> "$LOG"
[ "$rc" = 0 ] || { echo "[$(date +%T)] ABORT" >> "$LOG"; exit 1; }
# shellcheck disable=SC2086
bash scripts/tasks/judge.sh run $RUNS > "$LOG.run" 2>&1; rc=$?
echo "[$(date +%T)] run=$rc" >> "$LOG"
for r in $RUNS; do
  mise run analyze "$r" > "$LOG.analyze.$r" 2>&1
  echo "[$(date +%T)] analyze $r=$? judged=$(wc -l < results/$r/judgements.jsonl 2> /dev/null)" >> "$LOG"
done
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
