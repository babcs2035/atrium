#!/usr/bin/env bash
# p0007 A6：制御点で a6_judge_chunks.sh を nohup で起動し（操作端末の ssh が切れても続くように），その終わりの行を待って判定を取得し analyze する．
#   nohup bash results/p0007_a6_live/a6_fetch_judged.sh <run_id> <log> > /dev/null 2>&1 &
# 制御点の出力は results/<run_id>.judge_chunks.log（制御点）に書き，終わったら <log>.judge_chunks に写す．
set -uo pipefail
cd /mnt/data-raid/ktakahashi/workspace/atrium || exit 1
RUN=$1; LOG=$2
A=/home/denjo/workspace/ktakahashi/atrium
R=$A/results
REMOTE_LOG=$R/$RUN.judge_chunks.log
scp -q results/p0007_a6_live/a6_judge_chunks.sh wafl-ctrl5:$A/
ssh wafl-ctrl5 "nohup bash $A/a6_judge_chunks.sh $RUN > $REMOTE_LOG 2>&1 < /dev/null &"
echo "[$(date +%F_%T)] judge_chunks started on the control host ($REMOTE_LOG)" >> "$LOG"
until ssh wafl-ctrl5 "grep -q JUDGE_CHUNKS_DONE $REMOTE_LOG"; do sleep 120; done
scp -q wafl-ctrl5:$REMOTE_LOG "$LOG.judge_chunks"
echo "[$(date +%F_%T)] judge_chunks: $(grep -E 'DONE rc=|^PART |^split' "$LOG.judge_chunks" | tr '\n' ' ')" >> "$LOG"
if grep -q "JUDGE_CHUNKS_DONE rc=0" "$LOG.judge_chunks"; then
  rsync -az wafl-ctrl5:$R/$RUN/judgements.jsonl wafl-ctrl5:$R/$RUN/candidate_judgements.jsonl results/$RUN/
  mise run analyze "$RUN" > "$LOG.analyze2" 2>&1 < /dev/null
  echo "[$(date +%F_%T)] fetched and analyzed $RUN" >> "$LOG"
fi
echo "[$(date +%F_%T)] a6_fetch_judged $RUN: done" >> "$LOG"
