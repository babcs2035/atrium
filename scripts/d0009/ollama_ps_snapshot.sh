#!/usr/bin/env bash
# wafl500〜509 の llama3.1:8b が GPU・CPU にどれだけ載っているか（ollama ps）を標準出力へ出す（読み取りだけ．待ち時間の交絡の記録）．
#   bash scripts/d0009/ollama_ps_snapshot.sh > results/<run_id>/ollama_ps_start.txt
echo "# $(date +%FT%T)"
for i in 0 1 2 3 4 5 6 7 8 9; do
  echo "== wafl50$i"
  ssh -n -o BatchMode=yes -o ConnectTimeout=15 "wafl50$i" "docker exec atrium-ollama-1 ollama ps | tail -n +2" 2>&1 | grep -v "^\*\*"
done
