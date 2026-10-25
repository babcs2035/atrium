#!/usr/bin/env bash
# p0007 F-7：GPU の PC（wafl500〜509）に他の研究者の GPU の使用が無い状態が続いたときに，f6_chain.sh（deploy を含む）を始める．
# deploy の時に他の GPU の使用があると 8B の層の一部が CPU に載り生成が遅くなる（F-6）ため．他のプロセスには触れず，読み取りだけで待つ．
#   nohup bash results/p0007_f7_live/wait_and_run.sh > results/p0007_f7_live/wait.log 2>&1 &
set -uo pipefail
cd /mnt/data-raid/ktakahashi/workspace/atrium || exit 1
FREE_MIB=1000      # この値未満なら空いているとみなす
NEED_CONSEC=3      # 連続で空いている確認の回数
INTERVAL_S=60
consec=0
while true; do
  used=$(ssh -o ConnectTimeout=10 wafl-ctrl5 'for i in 100 101 102 103 104 105 106 107 108 109; do ssh -n -o ConnectTimeout=5 denjo@192.168.15.$i "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits"; done' 2>/dev/null | sort -n | tail -1)
  echo "[$(date +%T)] max used MiB: ${used:-?}"
  if [ -n "${used:-}" ] && [ "$used" -lt "$FREE_MIB" ]; then consec=$((consec + 1)); else consec=0; fi
  if [ "$consec" -ge "$NEED_CONSEC" ]; then break; fi
  sleep $INTERVAL_S
done
echo "[$(date +%F_%T)] GPU PC is free; starting the chain"
bash results/p0007_f6_live/gpu_apps_snapshot.sh > results/p0007_f7_live/gpu_apps_pre_chain.txt 2>&1
exec bash results/p0007_f6_live/f6_chain.sh results/p0007_f7_live/chain.log results/p0007_f7_live/specs.txt
