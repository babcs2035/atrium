#!/usr/bin/env bash
# p0007 A6 の 1 回分の連続実行（dev または test）：事前確認 → deploy・start・analyze（A5 の a5_chain.sh）→ mise run stop →
# 制御点で全候補の採点と cross-encoder の得点（a6_judge_ctrl.sh）→ 取得 → 事後確認．途中で commit しない．
#   bash results/p0007_a6_live/a6_run.sh <dev|test>
# 規則の当てはめ（atrium select-eval --ignore-unknown）はこの後に手で行う．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO" || exit 1
SPLIT=${1:?usage: a6_run.sh <dev|test>}
RUN=p0007_a6_$SPLIT
D=results/p0007_a6_live
LOG=$D/chain_$SPLIT.log
DISK_LIMIT=90
bash $D/precheck.sh > $D/precheck_${SPLIT}_pre.txt 2>&1
# §12 の 7：制御点・専門家のディスク使用率が 90% を超えていたら始めない
if awk -v lim=$DISK_LIMIT '$2 ~ /%$/ { v = $2 + 0; if (v > lim) bad = 1 } END { exit !bad }' $D/precheck_${SPLIT}_pre.txt; then
  echo "[$(date +%F_%T)] DISK OVER ${DISK_LIMIT}%; stop" >> "$LOG"; exit 1
fi
bash results/p0007_a5_live/a5_chain.sh "$LOG" $D/specs_$SPLIT.txt
mise run stop > "$LOG.stop" 2>&1 < /dev/null
echo "[$(date +%F_%T)] stopped; judging on the control host" >> "$LOG"
scp -q $D/a6_judge_ctrl.sh wafl-ctrl5:/home/denjo/workspace/ktakahashi/atrium/a6_judge_ctrl.sh
ssh wafl-ctrl5 bash /home/denjo/workspace/ktakahashi/atrium/a6_judge_ctrl.sh $RUN > "$LOG.judge" 2>&1 < /dev/null
echo "[$(date +%F_%T)] judge: $(grep -E 'DONE rc=' "$LOG.judge" | tr '\n' ' ')" >> "$LOG"
rsync -az wafl-ctrl5:/home/denjo/workspace/ktakahashi/atrium/results/$RUN/judgements.jsonl \
  wafl-ctrl5:/home/denjo/workspace/ktakahashi/atrium/results/$RUN/candidate_judgements.jsonl \
  wafl-ctrl5:/home/denjo/workspace/ktakahashi/atrium/results/$RUN/ce_features.jsonl results/$RUN/
mise run analyze $RUN > "$LOG.analyze2" 2>&1 < /dev/null
bash $D/precheck.sh > $D/precheck_${SPLIT}_post.txt 2>&1
echo "[$(date +%F_%T)] a6_run $SPLIT: done" >> "$LOG"
