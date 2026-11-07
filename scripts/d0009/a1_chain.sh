#!/usr/bin/env bash
# d0009 A1（Iteration 23）：質問者を 1 台（N1）と 3 台（N3）にして主構成 R3′ の候補を実機で流す連続実行．
#   (nohup setsid sg docker -c "bash scripts/d0009/a1_chain.sh results/d0009_a1_multi_requester/chain.log" > /dev/null 2>&1 < /dev/null &)
# 順序は ABBA（n1_r1 → n3_r1 → n3_r2 → n1_r2）で時間帯を散らし，続けて負荷を増やした記録（n3_hi．parallel 90）と，障害の記録（f_n1・f_n3．
# H の受信箱ごとの先頭 2 問．開始の 60 秒後に CPU の専門家 3 台の node を止め，120 秒後に戻す）を流す．
# 各回：前の回のノードを止める → wafl500〜509 の GPU が空くのを最大 2 時間待つ（空かなければ交絡として記録して流す）→ 設定（a1_config.py）→ deploy →
#   GPU・ollama ps の記録 → start（失敗した問が残ったら同じ run_id で 1 回だけ再開．障害の記録は流し直さない）→ 記録 → analyze → config.yaml を元へ戻す．
# results/<run_id>/run_meta.json がある回は飛ばす．実行の間は commit しない．start が失敗したら（結果が無い）ノードを止めずにそこで終わる．
# 質問者を兼ねる 2 台の資材・ディスク・MemAvailable の記録（G-R）は deploy のログ（artifacts/logs/deploy-extra）から results/<run_id>/g_r_extra_requesters.txt へ写す．
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
LOG=$1
D=scripts/d0009
OUT=results/d0009_a1_multi_requester
FREE_MIB=1000
NEED_CONSEC=3
INTERVAL_S=60
MAX_WAIT_S=7200
ORDER=(n1_r1 n3_r1 n3_r2 n1_r2 n3_hi f_n1 f_n3)
CONTROL=wafl-ctrl5
REMOTE_DIR=/home/denjo/workspace/ktakahashi/atrium
# 障害の記録で止める CPU の専門家の数と，止める・戻す時刻（開始からの秒）．質問者を兼ねる 13.100・14.100 は除く（種 12 で抽出）
FAULT_N_HOSTS=3
FAULT_STOP_S=60
FAULT_RESTORE_S=180
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

# 障害の記録で止めるホスト（13.x・14.x の CPU の専門家から質問者を兼ねる 2 台を除き，種 12 で抽出．空白区切り）
fault_hosts() {
  python3 - "$FAULT_N_HOSTS" << 'PY'
import random, sys
hosts = [f"192.168.{b}.10{i}" for b in (13, 14) for i in range(10)]
hosts = [h for h in hosts if h not in ("192.168.13.100", "192.168.14.100")]
print(" ".join(random.Random(12).sample(hosts, int(sys.argv[1]))))
PY
}

for tag in "${ORDER[@]}"; do
  run="d0009_a1_${tag}"
  case "$tag" in
    n1_*) cfg_args="--n 1" ;;
    n3_hi) cfg_args="--n 3 --parallel 90" ;;
    n3_*) cfg_args="--n 3" ;;
    f_n1) cfg_args="--n 1 --limit-per-inbox 2" ;;
    f_n3) cfg_args="--n 3 --limit-per-inbox 2" ;;
  esac
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
  # shellcheck disable=SC2086
  if ! uv run python $D/a1_config.py $cfg_args >> "$LOG" 2>&1; then
    echo "[$(date +%T)] $run SETCFG FAILED" >> "$LOG"; exit 1
  fi
  if ! retry "$LOG.$run.deploy" mise run deploy; then
    echo "[$(date +%T)] $run DEPLOY FAILED" >> "$LOG"; exit 1
  fi
  echo "[$(date +%T)] $run deployed" >> "$LOG"
  # G-R：質問者を兼ねる 2 台のディスク使用率・MemAvailable（deploy のログ）
  ssh -n "$CONTROL" "cat $REMOTE_DIR/artifacts/logs/deploy-extra/*.log 2>/dev/null" > "results/$run/g_r_extra_requesters.txt" 2>&1 < /dev/null
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_pre.txt" 2>&1 < /dev/null
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_start.txt" 2>&1 < /dev/null
  fault=0
  case "$tag" in
    f_*)
      fault=1
      plan="$FAULT_STOP_S $FAULT_RESTORE_S $(fault_hosts)"
      ssh -n "$CONTROL" "mkdir -p $REMOTE_DIR/results/$run && echo '$plan' > $REMOTE_DIR/results/$run/fault_plan.txt" < /dev/null
      echo "[$(date +%T)] $run fault plan: $plan" >> "$LOG"
      ;;
  esac
  mise run start "$run" > "$LOG.$run.start" 2>&1 < /dev/null; rc=$?
  inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$run.start" | tail -1 | tr -dc '0-9')
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_end.txt" 2>&1 < /dev/null
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_post.txt" 2>&1 < /dev/null
  echo "[$(date +%T)] $run start=$rc inner=${inner:-?}" >> "$LOG"
  if [ "${inner:-}" = 2 ] && [ "$fault" = 0 ]; then
    # 失敗した問（502・timeout）だけを同じ run_id・同じ設定で 1 回流し直す（障害の記録は流し直さない）
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
