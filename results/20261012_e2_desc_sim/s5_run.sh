#!/usr/bin/env bash
# p0003 S5：desc_sim（routing: card_sim）の実機での下流確認．deploy → start → analyze → stop．
# §6 の 4（制御点・専門家のディスクが 90% を超えたら止める）を deploy の前に確かめる．
# config.yaml は終了時に必ず元へ戻す．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
RUN=20261012_e2_desc_sim
LOG=/home/ktakahashi/.claude/jobs/0b6384d1/tmp/s5_run.log
cd "$REPO" || exit 1
trap 'git checkout -- config.yaml; echo "[$(date +%F_%T)] EXIT" >> "$LOG"' EXIT
echo "[$(date +%F_%T)] BEGIN $RUN" >> "$LOG"

# MedRAG は先頭 16 台（13.100〜109，14.100〜105）を使う
hosts="$(for i in 0 1 2 3 4 5 6 7 8 9; do echo 192.168.13.10$i; done; for i in 0 1 2 3 4 5; do echo 192.168.14.10$i; done)"
over=0
while read -r h; do
  u=$(ssh -n wafl-ctrl5 "ssh -n -o ConnectTimeout=10 denjo@$h \"df --output=pcent / | tail -1\"" 2>&1 | tr -dc '0-9')
  echo "disk $h ${u:-?}%" >> "$LOG"
  if [ -z "$u" ] || [ "$u" -gt 90 ]; then over=1; fi
done <<< "$hosts"
u=$(ssh -n wafl-ctrl5 "df --output=pcent / | tail -1" | tr -dc '0-9')
echo "disk wafl-ctrl5 ${u}%" >> "$LOG"
[ "$u" -gt 90 ] && over=1
if [ "$over" = 1 ]; then
  echo "[$(date +%T)] STOP: disk over 90% or unreachable (p0003 §6.4)" >> "$LOG"; exit 3
fi

mise run deploy > "$LOG.deploy" 2>&1 < /dev/null; rc=$?
echo "[$(date +%T)] deploy=$rc" >> "$LOG"
[ "$rc" = 0 ] || exit 4
mise run start "$RUN" > "$LOG.start" 2>&1 < /dev/null; rc=$?
echo "[$(date +%T)] start=$rc" >> "$LOG"
mise run analyze "$RUN" > "$LOG.analyze" 2>&1 < /dev/null
echo "[$(date +%T)] analyze=$?" >> "$LOG"
mise run stop > "$LOG.stop" 2>&1 < /dev/null
echo "[$(date +%T)] stop=$?" >> "$LOG"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
