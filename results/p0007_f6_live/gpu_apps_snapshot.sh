#!/usr/bin/env bash
# GPU を持つ 11 台の，GPU の使用状況（電力・使用率・メモリ）と GPU を使うプロセスの一覧を標準出力へ出す（p0007 D．他の使用の記録）．
#   bash gpu_apps_snapshot.sh > results/<run_id>/gpu_apps_pre.txt
# 操作端末から制御点を踏み台にして ssh する（読み取りだけ）．ollama 以外の GPU プロセスが他の利用者のものかどうかを後から判断できるよう，
# プロセスの名前とメモリ，その実行ファイルの親ディレクトリ名（cmdline の先頭 120 文字）を残す．
HOSTS="wafl-ctrl5 192.168.15.100 192.168.15.101 192.168.15.102 192.168.15.103 192.168.15.104 192.168.15.105 192.168.15.106 192.168.15.107 192.168.15.108 192.168.15.109"
echo "# $(date +%s) $(date +%FT%T)"
ssh -o BatchMode=yes -o ConnectTimeout=10 wafl-ctrl5 'for h in '"$HOSTS"'; do
  echo "== $h"
  ssh -n -o BatchMode=yes -o ConnectTimeout=10 denjo@$h "nvidia-smi --query-gpu=power.draw,utilization.gpu,memory.used --format=csv,noheader; for pid in \$(nvidia-smi --query-compute-apps=pid --format=csv,noheader); do echo \"pid=\$pid \$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | grep \"^\$pid,\" | cut -d, -f2) \$(tr \"\\0\" \" \" < /proc/\$pid/cmdline | cut -c1-120)\"; done" 2>&1
done'
