#!/usr/bin/env bash
# p0007 A6：実行の前後に，制御点・専門家のディスク使用率（§12 の 7．90% で止める）と，GPU の PC の他のプロセスの使用状況を記録する（読み取りのみ）．
#   bash precheck.sh > results/p0007_a6_live/precheck_<name>.txt
set -uo pipefail
cd /mnt/data-raid/ktakahashi/workspace/atrium || exit 1
eval "$(uv run --quiet python src/atrium/shell_config.py config.yaml)"
echo "# $(date '+%F %T')"
echo "ctrl $(ssh "$CONTROL" "df -P /home/denjo | tail -1 | awk '{print \$5}'")"
ssh "$CONTROL" "for h in $EXPERTS $GPU_WORKERS; do echo \"\$h \$(ssh -o ConnectTimeout=5 denjo@\$h \"df -P /home/denjo | tail -1 | awk '{print \\\$5}'\")\"; done" | sort -u
echo "# GPU（制御点と GPU の PC．使用メモリ・使用率と，atrium 以外の計算のプロセス）"
for h in ctrl $GPU_WORKERS; do
  if [ "$h" = ctrl ]; then cmd=(ssh "$CONTROL"); else cmd=(ssh "$CONTROL" ssh -o ConnectTimeout=5 "denjo@$h"); fi
  echo "$h $("${cmd[@]}" "nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader; nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader" | tr '\n' ' ')"
done
