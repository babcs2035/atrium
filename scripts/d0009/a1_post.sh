#!/usr/bin/env bash
# d0009 A1（Iteration 23）：連続実行（a1_chain.sh）の完了した回から順に，採点（a1_judge_all.sh）と R3′ の当てはめ（a1_select_all.sh）を行う監視．
#   (nohup setsid sg docker -c "bash scripts/d0009/a1_post.sh results/d0009_a1_multi_requester/post.log" > /dev/null 2>&1 < /dev/null &)
# gpu4 の判定モデルの container（atrium-judge-ollama-local．127.0.0.1:11436）を採点の間だけ起動し，終わったら止める（削除しない）．
# gpu4 の GPU は他の研究者と共有なので，空きメモリが NEED_FREE_MIB 未満なら採点を始めずに待つ（他のプロセスには触れない）．
# 4 回の R3′ の当てはめまで済んだら終わる．
cd /home/ktakahashi/workspace/atrium || exit 1
LOG=$1
export CHAIN_LOG=results/d0009_a1_multi_requester/chain.log
NEED_FREE_MIB=14000
INTERVAL_S=300
CONTAINER=atrium-judge-ollama-local
all_selected() {
  for r in n1_r1 n3_r1 n3_r2 n1_r2; do [ -s "results/d0009_a1_${r}_R3p/judgements.jsonl" ] || return 1; done
}
while ! all_selected; do
  free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
  echo "[$(date +%T)] gpu4 free ${free} MiB" >> "$LOG"
  if [ "${free:-0}" -ge "$NEED_FREE_MIB" ]; then
    docker start "$CONTAINER" >> "$LOG" 2>&1
    bash scripts/d0009/a1_judge_all.sh >> "$LOG" 2>&1
    docker stop "$CONTAINER" >> "$LOG" 2>&1
    bash scripts/d0009/a1_select_all.sh >> "$LOG" 2>&1
  fi
  all_selected || sleep "$INTERVAL_S"
done
echo "[$(date +%T)] ALL POST DONE" >> "$LOG"
