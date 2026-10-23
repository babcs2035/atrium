#!/usr/bin/env bash
# 制御点（wafl-ctrl5）の GPU の使用メモリ・使用率・電力と，GPU を使うプロセスごとのメモリを 1 秒ごとに記録する（p0007 F-6．読み取りのみ）．
#   bash gpu_mem_sampler.sh start <run_id>     # 制御点の $REMOTE_DIR/energy/f6_gpu/<run_id>.csv へ記録を始める
#   bash gpu_mem_sampler.sh stop <run_id>      # 止めて results/<run_id>/gpu_mem_ctrl.csv へ取り戻す
# 置き場所は $REMOTE_DIR/energy/ の下（mise タスクの rsync --delete の対象外．f6_gpu は deploy・start の同期で消された）
# 行：<unix 秒>,<使用メモリ MiB>,<使用率 %>,<電力 W>,<pid:MiB の並び（; 区切り）>
set -uo pipefail
cd /mnt/data-raid/ktakahashi/workspace/atrium || exit 1
ACTION=${1:?usage: gpu_mem_sampler.sh start|stop <run_id>}
RUN=${2:?usage: gpu_mem_sampler.sh start|stop <run_id>}
REMOTE_DIR=/home/denjo/workspace/ktakahashi/atrium
case "$ACTION" in
  start)
    ssh wafl-ctrl5 bash -s -- "$REMOTE_DIR/energy/f6_gpu" "$RUN" <<'EOS'
dir=$1; run=$2
mkdir -p "$dir"
cat > "$dir/$run.sh" <<"LOOP"
while :; do
  g=$(nvidia-smi --query-gpu=memory.used,utilization.gpu,power.draw --format=csv,noheader,nounits 2> /dev/null | tr -d ' ')
  a=$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits 2> /dev/null | tr -d ' ' | sed 's/,/:/' | paste -sd';')
  echo "$(date +%s.%N),$g,$a"
  sleep 1
done
LOOP
setsid nohup bash "$dir/$run.sh" > "$dir/$run.csv" 2> /dev/null < /dev/null &
echo $! > "$dir/$run.pid"
EOS
    ;;
  stop)
    ssh wafl-ctrl5 "kill \$(cat $REMOTE_DIR/energy/f6_gpu/$RUN.pid) 2> /dev/null; true"
    mkdir -p "results/$RUN"
    rsync -az "wafl-ctrl5:$REMOTE_DIR/energy/f6_gpu/$RUN.csv" "results/$RUN/gpu_mem_ctrl.csv"
    ;;
esac
