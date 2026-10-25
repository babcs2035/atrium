#!/usr/bin/env bash
# p0007 F-7：GPU の PC（wafl500〜509）の llama3.1:8b が GPU・CPU にどれだけ載っているか（ollama ps）を標準出力へ出す（読み取りだけ）．待ち時間の交絡の記録．
#   bash ollama_ps_snapshot.sh > results/p0007_f7_live/ollama_ps_<label>.txt
echo "# $(date +%FT%T)"
ssh -o BatchMode=yes -o ConnectTimeout=10 wafl-ctrl5 'for i in 100 101 102 103 104 105 106 107 108 109; do
  echo "== 192.168.15.$i"
  ssh -n -o BatchMode=yes -o ConnectTimeout=10 denjo@192.168.15.$i "docker exec atrium-ollama-1 ollama ps | tail -n +2" 2>&1
done'
