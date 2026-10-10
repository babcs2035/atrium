#!/usr/bin/env bash
# EQ4 の検証 2：EQ5 の 6 実行から 50 件ずつ（計 300 件）を参照モデル qwen3:14b で採点し直して κ を出す．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO"
LOG=$1
OUT=20261010_eq4_judge_kappa
RUNS="20261009_eq5_b1 20261009_eq5_b1_flood 20261009_eq5_b2 20261009_eq5_b2_1p7b 20261009_eq5_b3 20261009_eq5_b4"
echo "[$(date +%F_%T)] BEGIN kappa up" >> "$LOG"
bash scripts/tasks/judge.sh up qwen3:14b > "$LOG.up" 2>&1; rc=$?
echo "[$(date +%T)] up=$rc" >> "$LOG"
[ "$rc" = 0 ] || { echo "[$(date +%T)] ABORT" >> "$LOG"; exit 1; }
# shellcheck disable=SC2086
bash scripts/tasks/judge.sh kappa "$OUT" $RUNS > "$LOG.kappa" 2>&1; rc=$?
echo "[$(date +%T)] kappa=$rc" >> "$LOG"
cat "results/$OUT/metrics.json" >> "$LOG" 2> /dev/null
echo >> "$LOG"
bash scripts/tasks/judge.sh down > "$LOG.down" 2>&1; echo "[$(date +%T)] down=$?" >> "$LOG"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
