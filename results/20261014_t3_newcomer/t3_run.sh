#!/usr/bin/env bash
# p0005 T3：イメージを publish し，制御点のコンテナで newcomer-eval を 1 回だけ実行して results/ へ取得する．
# 事前登録（b89d289）の実行コードは d00d350（src は同じ）．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
OUT=20261014_t3_newcomer
LOG=/home/ktakahashi/.claude/jobs/0b6384d1/tmp/t3_run.log
cd "$REPO" || exit 1
echo "[$(date +%F_%T)] BEGIN $OUT (HEAD $(git rev-parse --short HEAD))" >> "$LOG"
bash /home/ktakahashi/.claude/jobs/0b6384d1/tmp/publish.sh > "$LOG.publish" 2>&1; rc=$?
echo "[$(date +%T)] publish=$rc" >> "$LOG"
[ "$rc" = 0 ] || exit 4
echo "[$(date +%T)] PUBLISHED" >> "$LOG"
bash scripts/tasks/remote.sh newcomer_eval "$OUT" 20261009_eq2_advert_eval > "$LOG.run" 2>&1; rc=$?
echo "[$(date +%T)] newcomer_eval=$rc" >> "$LOG"
[ "$rc" = 0 ] || exit 5
mkdir -p "results/$OUT"
rsync -az "wafl-ctrl5:/home/denjo/workspace/ktakahashi/atrium/results/$OUT/" "results/$OUT/"; rc=$?
echo "[$(date +%T)] fetch=$rc" >> "$LOG"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
