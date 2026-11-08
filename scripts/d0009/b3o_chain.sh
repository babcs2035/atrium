#!/usr/bin/env bash
# d0009 B3（Iteration 24）：P2P の主構成 R3′ の基準（base．今の CombMAX）と，dev で選んだ得点（new）を，base → new の順に実機で流す連続実行（質問は H ∪ H2 = holdout_q）．
#   (nohup setsid sg docker -c "bash scripts/d0009/b3o_chain.sh results/d0009_b3_owner/chain.log" > /dev/null 2>&1 < /dev/null &)
# B3O_ORDER で回を絞れる・足せる（空白区切り．G-B が外れたときの new → base の 2 回の追加は "new_r2 base_r2"．結果を見て増やさない事前の規則）．
# 各回：前の回のノードを止める → wafl500〜509 の GPU が空くのを最大 2 時間待つ（空かなければ交絡として記録して流す）→ 設定 → deploy →
#   GPU・ollama ps の記録 → start（失敗した問が残ったら同じ run_id で 1 回だけ再開）→ 記録 → analyze → config.yaml を元へ戻す．
# results/<run_id>/run_meta.json がある回は飛ばす．実行の間は commit しない．start が失敗したら（結果が無い）ノードを止めずにそこで終わる．
# 1 回目の start が終わったとき（流し直しの前）に run_meta.json を phase1_run_meta.json へ写す（流し直しは finished_at を上書きする．Iteration 23 の学び）．
# new が learned の得点のときだけ，deploy の前に重み（results/d0009_b3_owner/owner_ranker.json）を制御点の <data_dir>/enronqa/summary/ へ置く．
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
LOG=$1
D=scripts/d0009
OUT=results/d0009_b3_owner
CONTROL=wafl-ctrl5
CONTROL_SUMMARY_DIR=/home/denjo/atrium-data/enronqa/summary
FREE_MIB=1000
NEED_CONSEC=3
INTERVAL_S=60
MAX_WAIT_S=7200
read -r -a ORDER <<< "${B3O_ORDER:-base new}"
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
  case "$tag" in base*) kind=base ;; new*) kind=new ;; esac
  run="d0009_b3o_${tag}"
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
  if [ "$kind" = new ] && grep -q '^  owner_score: learned' config.yaml; then
    rsync -a "$OUT/owner_ranker.json" "$CONTROL:$CONTROL_SUMMARY_DIR/owner_ranker.json" >> "$LOG" 2>&1 < /dev/null || { echo "[$(date +%T)] $run RANKER COPY FAILED" >> "$LOG"; exit 1; }
  fi
  if ! uv run python $D/b3o_config.py "$kind" >> "$LOG" 2>&1; then
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
  [ -f "results/$run/run_meta.json" ] && [ ! -f "results/$run/phase1_run_meta.json" ] && cp "results/$run/run_meta.json" "results/$run/phase1_run_meta.json" && wc -l < "results/$run/results.jsonl" > "results/$run/phase1_n_rows.txt"
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_end.txt" 2>&1 < /dev/null
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_post.txt" 2>&1 < /dev/null
  echo "[$(date +%T)] $run start=$rc inner=${inner:-?}" >> "$LOG"
  if [ "${inner:-}" = 2 ]; then
    # 失敗した問（502・timeout）だけを同じ run_id・同じ設定で 1 回流し直す
    mise run start "$run" > "$LOG.$run.start_resume" 2>&1 < /dev/null; rc=$?
    inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$run.start_resume" | tail -1 | tr -dc '0-9')
    echo "[$(date +%T)] $run resume start=$rc inner=${inner:-?}" >> "$LOG"
  fi
  if [ ! -f "results/$run/run_meta.json" ]; then
    echo "[$(date +%T)] $run START FAILED (nodes are left running; inspect $LOG.$run.start)" >> "$LOG"; exit 1
  fi
  mise run analyze "$run" > "$LOG.$run.analyze" 2>&1 < /dev/null; echo "[$(date +%T)] $run analyze=$?" >> "$LOG"
  git checkout -- config.yaml
done
bash scripts/tasks/remote.sh stop > "$LOG.final_stop" 2>&1 < /dev/null
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
