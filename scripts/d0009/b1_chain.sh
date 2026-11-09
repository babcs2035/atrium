#!/usr/bin/env bash
# d0009 B1（Iteration 35）：質問者の生存確認で止まったノードを外す仕組みの障害の測定を実機で流す連続実行（deploy 1 回 → smoke → 3 ラウンド．各ラウンドは failure-run 1 回で 8 条件）．
#   (nohup setsid sg docker -c "bash scripts/d0009/b1_chain.sh results/d0009_b1_exclusion/chain.log" > /dev/null 2>&1 < /dev/null &)
#   引数 2 個目以降に実行の名前（smoke・r1・r2・r3）を並べると，その順に流す（省略時は smoke r1 r2 r3）．
# a6f_chain.sh（Iteration 30）と同じ手順で，config は b1_config.py（a6f の条件 ＋ experiment.peer_health）．
# 順序：smoke（判定に使わない）の後に，Iteration 30 と同じ modes・ks の順（journal の Iteration 35）で r1・r2・r3．
# smoke の G-FP (ii)（止めていないノードが外された回数）が 1 回以上なら，fail_threshold を 2 へ 1 回だけ上げて deploy し直し，smoke を流し直す（事前登録）．
# 各ラウンドの前に wafl500〜509 の GPU が空くのを最大 2 時間待ち（空かなければ交絡として記録して流す），前後に GPU・ollama ps を記録し，
# 後に 30 台の node の /v1/profile が 200 であること（G-R）を確かめる．止まったままのノードがあれば再開してから記録し，直らなければそこで終わる．
# results/<run_id>/failure.json がある回は飛ばす．実行の間は commit しない．config.yaml は終了時に元へ戻す．
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
LOG=$1
shift
D=scripts/d0009
FREE_MIB=1000
NEED_CONSEC=3
INTERVAL_S=60
MAX_WAIT_S=7200
DEADLINE_S=30
FAIL_THRESHOLD=1
RESULT_DIR=results/d0009_b1_exclusion
NODE_PORT=8100
CONTROL=wafl-ctrl5
REMOTE_DIR=/home/denjo/workspace/ktakahashi/atrium
SSH_USER=denjo
COMMON_ARGS=(--baseline-s 15 --down-s 45 --post-s 30 --bin-s 5 --poll-interval-s 0.2 --stream-parallel 8 --central-questions 200)
# Random(12) で modes・ks をラウンドごとに shuffle した結果（結果を見る前に固定．journal の Iteration 30）
ARGS_r1=(--modes availability,container --ks 1,3,10,6)
ARGS_r2=(--modes container,availability --ks 6,3,1,10)
ARGS_r3=(--modes availability,container --ks 1,3,10,6)
ARGS_smoke=(--modes availability,container --ks 1 --down-s 15 --baseline-s 10 --post-s 10 --stream-parallel 8 --central-questions 200)
ORDER=(smoke r1 r2 r3)
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

# 他の研究者の GPU の使用が無い（自分たちの Ollama の llama3.1:8b 以外のプロセスのメモリが FREE_MIB 未満）状態が NEED_CONSEC 回続くのを最大 MAX_WAIT_S 秒待つ．
# deploy の後は自分たちの Ollama がモデルを GPU に載せたままなので，全体の使用メモリではなく，そのモデルの blob を開いていないプロセスだけを数える．
# 戻り値 0：空いた／1：空かなかった
OWN_MODEL_BLOB=667b0c1932bc6ffc
wait_for_free_gpus() {
  local consec=0 waited=0 used
  while [ "$waited" -lt "$MAX_WAIT_S" ]; do
    used=$(for i in 0 1 2 3 4 5 6 7 8 9; do
      ssh -n -o BatchMode=yes -o ConnectTimeout=15 "wafl50$i" "total=0; for pid in \$(nvidia-smi --query-compute-apps=pid --format=csv,noheader); do \
        case \"\$(tr '\\0' ' ' < /proc/\$pid/cmdline)\" in *$OWN_MODEL_BLOB*) ;; *) m=\$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits | grep \"^\$pid,\" | cut -d, -f2 | tr -d ' '); total=\$((total + \${m:-0}));; esac; done; echo \$total" 2> /dev/null | grep -v '^\*\*'
    done | sort -n | tail -1)
    echo "[$(date +%T)] max other-process GPU memory on wafl500-509: ${used:-?} MiB" >> "$LOG"
    if [ -n "${used:-}" ] && [ "$used" -lt "$FREE_MIB" ]; then consec=$((consec + 1)); else consec=0; fi
    if [ "$consec" -ge "$NEED_CONSEC" ]; then return 0; fi
    sleep "$INTERVAL_S"
    waited=$((waited + INTERVAL_S))
  done
  return 1
}

# 制御点から見た placement の全ノードの /v1/profile の状態を，応答が 200 でないホストの一覧として標準出力へ出す（空なら全て動いている）
down_hosts() {
  ssh -n -o BatchMode=yes "$CONTROL" "cd $REMOTE_DIR && for h in \$(cut -f1 artifacts/enronqa/placement.tsv); do \
    c=\$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://\$h:$NODE_PORT/v1/profile); [ \"\$c\" = 200 ] || echo \"\$h\"; done"
}

# G-R：30 台が全て動いていること．止まったノードは availability で戻し，だめなら node サービスだけを再開する（データ・image は触らない）
ensure_all_nodes_up() {
  local run=$1 down
  down=$(down_hosts)
  if [ -n "$down" ]; then
    echo "[$(date +%T)] $run G-R: not serving: $(echo $down)" >> "$LOG"
    for h in $down; do
      ssh -n -o BatchMode=yes "$CONTROL" "curl -s -o /dev/null --max-time 10 -X POST -H 'Content-Type: application/json' -d '{\"up\": true}' http://$h:$NODE_PORT/v1/admin/availability"
    done
    down=$(down_hosts)
  fi
  if [ -n "$down" ]; then
    for h in $down; do
      ssh -n -o BatchMode=yes "$CONTROL" "ssh -n -o BatchMode=yes $SSH_USER@$h 'cd $REMOTE_DIR && docker compose start node'" >> "$LOG" 2>&1
    done
    sleep 60
    down=$(down_hosts)
  fi
  echo "${down:-none}" > "results/$run/g_r_not_serving_after.txt"
  [ -z "$down" ]
}

# 実行の failure.json から，止めていないノードが外された回数（G-FP (ii)）の合計を求める
false_positive_drops() {
  jq -r '[.p2p[].peer_health.false_positive_drops] | add' "results/$1/failure.json"
}

run_one() {
  local tag=$1 run="d0009_b1_exclusion_${1}" args_name="ARGS_${1}" deadline=$2
  local -n extra=$args_name
  if [ -f "results/$run/failure.json" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; return 0
  fi
  echo "[$(date +%F_%T)] BEGIN $run (D=$deadline)" >> "$LOG"
  if wait_for_free_gpus; then confound=none; else confound="GPU not free after ${MAX_WAIT_S}s; run anyway"; fi
  echo "[$(date +%T)] $run confound=$confound" >> "$LOG"
  mkdir -p "results/$run"
  echo "$confound" > "results/$run/gpu_wait_result.txt"
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_pre.txt" 2>&1 < /dev/null
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_start.txt" 2>&1 < /dev/null
  bash scripts/tasks/live_run.sh failure_run "$run" "${COMMON_ARGS[@]}" "${extra[@]}" --answer-deadline-s "$deadline" > "$LOG.$run.live" 2>&1 < /dev/null
  local rc=$?
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_end.txt" 2>&1 < /dev/null
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_post.txt" 2>&1 < /dev/null
  echo "[$(date +%T)] $run live_run=$rc" >> "$LOG"
  if ! ensure_all_nodes_up "$run"; then
    echo "[$(date +%T)] $run G-R FAILED (some nodes still not serving; see results/$run/g_r_not_serving_after.txt)" >> "$LOG"; exit 1
  fi
  echo "[$(date +%T)] $run G-R ok (all nodes serving)" >> "$LOG"
  if [ ! -f "results/$run/failure.json" ]; then
    echo "[$(date +%T)] $run FAILED (no failure.json; inspect $LOG.$run.live and results/$run/start.log)" >> "$LOG"; exit 1
  fi
  # 外す仕組みが on で動いたこと（事象の列が failure.json にあること）
  if [ "$(jq '[.p2p[] | select(.peer_health == null)] | length' "results/$run/failure.json")" != 0 ]; then
    echo "[$(date +%T)] $run FAILED (peer_health missing in failure.json)" >> "$LOG"; exit 1
  fi
  # G-TB：表に無い組による失敗が 1 件でもあれば，次へ進まずに止めて表を直す
  misses=$(jq '[.p2p[].questions[] | select(.failure_kind == "table_miss")] | length' "results/$run/failure.json")
  echo "[$(date +%T)] $run table_miss=$misses" >> "$LOG"
  if [ "$misses" != 0 ]; then
    echo "[$(date +%T)] $run G-TB FAILED (table_miss=$misses); fix the table before the next run" >> "$LOG"; exit 1
  fi
}

# G-TB：制御点の表が gpu4 の表（a6f_owner_table_all.py の出力）と同じであること（deploy が制御点の表を質問者へ配る）
TABLE=enronqa/ce_table/holdout_owner.jsonl
local_sha=$(sha256sum "artifacts/$TABLE" | cut -d' ' -f1)
control_sha=$(ssh -n -o BatchMode=yes "$CONTROL" "sha256sum /home/denjo/atrium-data/$TABLE" 2> /dev/null | cut -d' ' -f1)
if [ "$local_sha" != "$control_sha" ]; then echo "[$(date +%T)] TABLE MISMATCH between gpu4 and control" >> "$LOG"; exit 1; fi
# config を作って deploy する（deploy は最初に 1 回．config は全ラウンドで同じ．live_run.sh が各回の前に config.yaml を制御点へ同期するので，
# config.yaml は chain の終了時（trap）まで戻さない）
set_config_and_deploy() {
  if ! uv run python $D/b1_config.py --fail-threshold "$FAIL_THRESHOLD" >> "$LOG" 2>&1; then echo "[$(date +%T)] SETCFG FAILED" >> "$LOG"; exit 1; fi
  if ! retry "$LOG.deploy" mise run deploy; then echo "[$(date +%T)] DEPLOY FAILED" >> "$LOG"; exit 1; fi
  echo "[$(date +%T)] deployed (fail_threshold=$FAIL_THRESHOLD)" >> "$LOG"
}

# 前の実験のノードを止め，config を作って deploy する
bash scripts/tasks/remote.sh stop > "$LOG.pre_stop" 2>&1 < /dev/null
mkdir -p $RESULT_DIR
bash $D/gpu_apps_snapshot.sh > $RESULT_DIR/gpu_apps_pre_deploy.txt 2>&1 < /dev/null
set_config_and_deploy

deadline=$DEADLINE_S
for tag in "${ORDER[@]}"; do
  run_one "$tag" "$deadline"
  if [ "$tag" = smoke ]; then
    # 質問者のコードの例外（failure_kind=other．ノードの失敗ではない）が 1 件でもあれば，本番へ進まずに止める
    other=$(jq '[.p2p[].questions[] | select(.failure_kind == "other")] | length' "results/d0009_b1_exclusion_smoke/failure.json")
    echo "[$(date +%T)] smoke failure_kind=other count=$other" >> "$LOG"
    if [ "$other" != 0 ]; then echo "[$(date +%T)] smoke SANITY FAILED (requester exceptions); inspect results/d0009_b1_exclusion_smoke/requester.log" >> "$LOG"; exit 1; fi
    fp=$(false_positive_drops "d0009_b1_exclusion_smoke")
    echo "[$(date +%T)] smoke false_positive_drops=$fp (fail_threshold=$FAIL_THRESHOLD)" >> "$LOG"
    if [ "$fp" != 0 ] && [ "$FAIL_THRESHOLD" = 1 ]; then
      FAIL_THRESHOLD=2
      echo "$FAIL_THRESHOLD" > $RESULT_DIR/fail_threshold_raised.txt
      echo "[$(date +%T)] raising fail_threshold to 2 once; redeploy and rerun smoke as smoke_ft2" >> "$LOG"
      set_config_and_deploy
      ARGS_smoke_ft2=("${ARGS_smoke[@]}")
      run_one smoke_ft2 "$deadline"
      echo "[$(date +%T)] smoke_ft2 false_positive_drops=$(false_positive_drops d0009_b1_exclusion_smoke_ft2)" >> "$LOG"
    fi
  fi
done
bash scripts/tasks/remote.sh stop > "$LOG.final_stop" 2>&1 < /dev/null
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
