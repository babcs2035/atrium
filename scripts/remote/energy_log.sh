#!/usr/bin/env bash
# 実験の間の消費電力を各ホストで 1 秒ごとに記録し，積分（ジュール）を出す（p0007 K5・I8）．
#
# 使い方（制御点で．実際の問の処理の実験の前後に挟む）:
#   bash scripts/remote/energy_log.sh start <out_name> <host>...
#   bash scripts/remote/energy_log.sh stop  <out_name> <host>... [-- <問の数>]
# start は各ホストの $REMOTE_DIR/energy/<out_name>/sampler.{pid,csv} へ記録を始める．
# 読めない項目は空欄になり，RAPL も nvidia-smi も読めないホストは警告を出して記録しない．
# stop は記録を止め，results/<out_name>/energy/<host>.csv へ取り戻し，同じ場所の summary.json へ
# ホストごとと合計の積分（gpu_joules・cpu_joules．問の数を渡すと 1 問あたりも）を書く．
# CSV の形式と積分の方法は src/atrium/energy.py．
#
# 2026-10-19 のゲート（results/p0007_energy_gate/）の結果: RAPL は全ホストで root 専用（0400）のため読めず，
# GPU の消費電力（nvidia-smi）だけが制御点（wafl-ctrl5）と wafl500〜509（192.168.15.x）で読める．
# CPU の専門家（192.168.13.x・192.168.14.x）は GPU が無いので記録しない．RAPL が読めるようになれば，
# このスクリプトは設定を変えずに RAPL も記録する．
source "$(dirname "$0")/lib.sh"

USAGE="usage: energy_log.sh start|stop <out_name> <host>... [-- <question_count>]"
ACTION=${1:?$USAGE}
OUT=${2:?$USAGE}
shift 2
HOSTS=()
QUESTIONS=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--" ]; then
    QUESTIONS=${2:?$USAGE}
    break
  fi
  HOSTS+=("$1")
  shift
done
[ ${#HOSTS[@]} -gt 0 ] || { echo "$USAGE" >&2; exit 2; }

SAMPLE_INTERVAL_S=1
REMOTE_ENERGY_DIR="$REMOTE_DIR/energy/$OUT"
LOCAL_ENERGY_DIR="results/$OUT/energy"

# ホストで動かす記録の本体（$1: 記録のディレクトリ，$2: 間隔の秒）．RAPL・GPU のどちらも読めなければ終了コード 3．
# 記録のループは別ファイル（sampler.sh）へ書き出して setsid で切り離す（SSH が切れても続け，stop で止める）
SAMPLER_SCRIPT='
dir=$1; interval=$2
rapl=/sys/class/powercap/intel-rapl:0
rapl_ok=0; gpu_ok=0
cat "$rapl/energy_uj" > /dev/null 2>&1 && rapl_ok=1
command -v nvidia-smi > /dev/null 2>&1 && nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits > /dev/null 2>&1 && gpu_ok=1
[ $((rapl_ok + gpu_ok)) -gt 0 ] || exit 3
mkdir -p "$dir"
if [ -f "$dir/sampler.pid" ]; then kill "$(cat "$dir/sampler.pid")" 2> /dev/null || true; fi
cat > "$dir/sampler.sh" <<"EOS"
rapl=$1; rapl_ok=$2; gpu_ok=$3; interval=$4
if [ "$rapl_ok" = 1 ]; then echo "# max_energy_range_uj=$(cat "$rapl/max_energy_range_uj")"; fi
while :; do
  g=""; r=""
  if [ "$gpu_ok" = 1 ]; then
    g=$(nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits 2> /dev/null | awk "{s+=\$1} END{if (NR) printf \"%.2f\", s}")
  fi
  if [ "$rapl_ok" = 1 ]; then r=$(cat "$rapl/energy_uj" 2> /dev/null || true); fi
  echo "$(date +%s.%N),$g,$r"
  sleep "$interval"
done
EOS
setsid nohup bash "$dir/sampler.sh" "$rapl" "$rapl_ok" "$gpu_ok" "$interval" > "$dir/sampler.csv" 2> /dev/null < /dev/null &
echo $! > "$dir/sampler.pid"
echo "rapl=$rapl_ok gpu=$gpu_ok"
'

start_host() {
  local host=$1
  local status=0
  # nssh は標準入力を読ませないので，スクリプトを渡すここだけ ssh を直接使う
  ssh "${SSH_OPTS[@]}" "$(ssh_dest "$host")" bash -s -- "$REMOTE_ENERGY_DIR" "$SAMPLE_INTERVAL_S" <<< "$SAMPLER_SCRIPT" || status=$?
  if [ "$status" -eq 3 ]; then
    log "$host: neither RAPL nor nvidia-smi is readable; no energy is recorded"
    return 0
  fi
  return "$status"
}

stop_host() {
  local host=$1
  nssh "$host" "[ -f $REMOTE_ENERGY_DIR/sampler.pid ] && kill \$(cat $REMOTE_ENERGY_DIR/sampler.pid) 2> /dev/null; true"
  if nssh "$host" "[ -f $REMOTE_ENERGY_DIR/sampler.csv ]"; then
    nrsync -a "$(ssh_dest "$host"):$REMOTE_ENERGY_DIR/sampler.csv" "$LOCAL_ENERGY_DIR/$host.csv"
  else
    log "$host: no energy record (the host was skipped at start)"
  fi
}

case "$ACTION" in
  start)
    run_parallel "energy_start_$OUT" start_host "${HOSTS[@]}"
    log "energy start done: ${HOSTS[*]}"
    ;;
  stop)
    mkdir -p "$LOCAL_ENERGY_DIR"
    run_parallel "energy_stop_$OUT" stop_host "${HOSTS[@]}"
    PYTHONPATH=src python3 -m atrium.energy "$LOCAL_ENERGY_DIR" ${QUESTIONS:+"$QUESTIONS"} > "$LOCAL_ENERGY_DIR/summary.json"
    log "energy summary: $LOCAL_ENERGY_DIR/summary.json"
    ;;
  *)
    echo "$USAGE" >&2
    exit 2
    ;;
esac
