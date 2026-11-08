#!/usr/bin/env bash
# d0009 A6（Iteration 29）：P・C・S を無作為な順序で交互に実機で流す連続実行（4 ラウンド × 3 条件．Abedi & Brecht の randomized interleaved trials）．
#   (nohup setsid sg docker -c "bash scripts/d0009/a6_chain.sh results/d0009_a6_load/chain.log" > /dev/null 2>&1 < /dev/null &)
#   引数 2 個目以降に実行の名前（例: smoke_p p_r1 s_r1 c_r1 …）を並べると，その順に流す（省略時は事前登録の 12 実行）．smoke_* は受信箱ごとに先頭 1 問．
# 各回：前の回のノードを止める → wafl500〜509 の GPU が空くのを最大 2 時間待つ（空かなければ交絡として記録して流す）→ 設定 → deploy →
#   GPU・ollama ps の記録 → start → 記録 → analyze → config.yaml を元へ戻す．
# 処理量は壁時計から求めるので，失敗した問の再開（resume）はこの中ではしない（流し直しは事前登録の規則で後から手で決める）．
# results/<run_id>/run_meta.json がある回は飛ばす．実行の間は commit しない．start が失敗したら（結果が無い）ノードを止めずにそこで終わる．
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
LOG=$1
shift
D=scripts/d0009
FREE_MIB=1000
NEED_CONSEC=3
INTERVAL_S=60
MAX_WAIT_S=7200
# Random(12) で ['P', 'C', 'S'] を 4 回 shuffle した結果（結果を見る前に固定．journal の Iteration 29）
ORDER=(p_r1 s_r1 c_r1 p_r2 c_r2 s_r2 s_r3 c_r3 p_r3 s_r4 c_r4 p_r4)
if [ "$#" -gt 0 ]; then ORDER=("$@"); fi
trap 'git checkout -- config.yaml' EXIT
retry() {
  local out=$1 i
  shift
  for i in 1 2 3; do
    "$@" > "$out" 2>&1 < /dev/null && return 0
    if [ "$i" -lt 3 ]; then
      echo "[$(date +%T)] attempt $i failed: $(basename "$out"); retrying in 300s" >> "$LOG"
      sleep 300
    fi
  done
  return 1
}

# 他の研究者の GPU の使用が無い（使用メモリが FREE_MIB 未満）状態が NEED_CONSEC 回続くのを最大 MAX_WAIT_S 秒待つ．戻り値 0：空いた／1：空かなかった
wait_for_free_gpus() {
  local consec=0 waited=0 used
  while [ "$waited" -lt "$MAX_WAIT_S" ]; do
    used=$(for i in 0 1 2 3 4 5 6 7 8 9; do
      ssh -n -o BatchMode=yes -o ConnectTimeout=15 "wafl50$i" "nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits" 2> /dev/null | grep -v '^\*\*'
    done | sort -n | tail -1)
    echo "[$(date +%T)] max GPU memory used on wafl500-509: ${used:-?} MiB" >> "$LOG"
    if [ -n "${used:-}" ] && [ "$used" -lt "$FREE_MIB" ]; then consec=$((consec + 1)); else consec=0; fi
    if [ "$consec" -ge "$NEED_CONSEC" ]; then return 0; fi
    sleep "$INTERVAL_S"
    waited=$((waited + INTERVAL_S))
  done
  return 1
}

for tag in "${ORDER[@]}"; do
  limit=()
  case "$tag" in smoke_*) kind=${tag:6:1}; limit=(--limit-per-inbox 1) ;; *) kind=${tag:0:1} ;; esac
  run="d0009_a6_${tag}"
  if [ -f "results/$run/run_meta.json" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; continue
  fi
  echo "[$(date +%F_%T)] BEGIN $run" >> "$LOG"
  bash scripts/tasks/remote.sh stop > "$LOG.$run.stop" 2>&1 < /dev/null
  if wait_for_free_gpus; then confound=none; else confound="GPU not free after ${MAX_WAIT_S}s; run anyway"; fi
  echo "[$(date +%T)] $run confound=$confound" >> "$LOG"
  mkdir -p "results/$run"
  echo "$confound" > "results/$run/gpu_wait_result.txt"
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_pre_deploy.txt" 2>&1 < /dev/null
  if ! uv run python $D/a6_config.py "$kind" "${limit[@]}" >> "$LOG" 2>&1; then
    echo "[$(date +%T)] $run SETCFG FAILED" >> "$LOG"; exit 1
  fi
  if ! retry "$LOG.$run.deploy" mise run deploy; then
    echo "[$(date +%T)] $run DEPLOY FAILED" >> "$LOG"; exit 1
  fi
  echo "[$(date +%T)] $run deployed" >> "$LOG"
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_pre.txt" 2>&1 < /dev/null
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_start.txt" 2>&1 < /dev/null
  mise run start "$run" > "$LOG.$run.start" 2>&1 < /dev/null; rc=$?
  inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$run.start" | tail -1 | tr -dc '0-9')
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_end.txt" 2>&1 < /dev/null
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_post.txt" 2>&1 < /dev/null
  echo "[$(date +%T)] $run start=$rc inner=${inner:-?}" >> "$LOG"
  if [ ! -f "results/$run/run_meta.json" ]; then
    echo "[$(date +%T)] $run START FAILED (nodes are left running; inspect $LOG.$run.start)" >> "$LOG"; exit 1
  fi
  mise run analyze "$run" > "$LOG.$run.analyze" 2>&1 < /dev/null; echo "[$(date +%T)] $run analyze=$?" >> "$LOG"
  git checkout -- config.yaml
done
bash scripts/tasks/remote.sh stop > "$LOG.final_stop" 2>&1 < /dev/null
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
