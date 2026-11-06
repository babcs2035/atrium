#!/usr/bin/env bash
# GPU を持つ 10 台（wafl500〜509）の，GPU の使用状況（電力・使用率・メモリ）と GPU を使うプロセスの一覧を標準出力へ出す（読み取りだけ．他の使用の記録．運用ルール 14）．
#   bash scripts/d0009/gpu_apps_snapshot.sh > results/<run_id>/gpu_apps_pre.txt
# 他の研究者のプロセスかどうかを後から判断できるよう，プロセスの名前とメモリ，cmdline の先頭 120 文字を残す．操作端末から wafl500〜509 へ直接 ssh する．
echo "# $(date +%s) $(date +%FT%T)"
for i in 0 1 2 3 4 5 6 7 8 9; do
  echo "== wafl50$i"
  ssh -n -o BatchMode=yes -o ConnectTimeout=15 "wafl50$i" "nvidia-smi --query-gpu=power.draw,utilization.gpu,memory.used --format=csv,noheader; for pid in \$(nvidia-smi --query-compute-apps=pid --format=csv,noheader); do echo \"pid=\$pid \$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | grep \"^\$pid,\" | cut -d, -f2) \$(tr \"\\0\" \" \" < /proc/\$pid/cmdline | cut -c1-120)\"; done" 2>&1 | grep -v "^\*\*"
done
