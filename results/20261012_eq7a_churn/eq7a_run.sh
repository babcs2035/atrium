#!/usr/bin/env bash
# p0004 EQ7a：イメージを publish し，制御点のコンテナで churn-eval を 1 回だけ実行して results/ へ取得する．
# 事前登録（f5e663d）の実行コードは 34258f7．この時点の main（03698c3）は src が同じ．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
OUT=20261012_eq7a_churn
LOG=/home/ktakahashi/.claude/jobs/0b6384d1/tmp/eq7a_run.log
cd "$REPO" || exit 1
echo "[$(date +%F_%T)] BEGIN $OUT (HEAD $(git rev-parse --short HEAD))" >> "$LOG"

bash /home/ktakahashi/.claude/jobs/0b6384d1/tmp/publish.sh > "$LOG.publish" 2>&1; rc=$?
echo "[$(date +%T)] publish=$rc" >> "$LOG"
[ "$rc" = 0 ] || exit 4

bash scripts/tasks/remote.sh churn_eval "$OUT" 20261009_eq5_b1 20261009_eq2_advert_eval > "$LOG.run" 2>&1; rc=$?
echo "[$(date +%T)] churn_eval=$rc" >> "$LOG"
[ "$rc" = 0 ] || exit 5

mkdir -p "results/$OUT"
rsync -az "wafl-ctrl5:/home/denjo/workspace/ktakahashi/atrium/results/$OUT/" "results/$OUT/"; rc=$?
echo "[$(date +%T)] fetch=$rc" >> "$LOG"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
