#!/usr/bin/env bash
# d0009 B1（Iteration 36）：外す仕組みの再測定の連続実行（G-V → deploy → smoke → D0 → G-ENV → deploy → r1〜r3（必要なら r4・r5））．
#   (nohup setsid sg docker -c "bash scripts/d0009/b1r_chain.sh results/d0009_b1_exclusion_i36/chain.log" > /dev/null 2>&1 < /dev/null &)
#   引数 2 個目以降に実行の名前（smoke・d0_null・r1〜r5）を並べると，その順に流す（省略時は smoke d0_null r1 r2 r3 とラウンドの追加）．
# b1_chain.sh（Iteration 35）と同じ手順に，journal の Iteration 36 の実行の環境のゲートを足したもの：
#   - G-V（b1_gv_check.py．CPU だけ．deploy の前）．不通過なら止まる．
#   - G-GPU-pre（各回の前．b1_gpu_gate.py pre）．満たさなければ 5 分ごとに確かめ直し，2 時間で満たさなければ流さずに止まる（[needs-human]）．
#     (a) の使用率だけを満たさない台は，自分たちの Ollama の container（atrium-ollama-1）を `docker compose restart ollama` で 1 回だけ再起動してよい．
#   - G-GPU-post（各回の後．b1_gpu_gate.py post）．不通過のラウンドは無効（判定は b1_verdict.py --gpu-gate）．
#   - smoke は止める前の deadline 0 件を要求する．D0（外す仕組み無し．config は a6f_config.py の出力のまま）は G-ENV（b1_verdict.py --env-check）を満たさなければ本番を流さない．
#   - ラウンドの追加：r1〜r3 のうち G-GPU-post だけで無効（G-FP (i)・G-B は満たす）のラウンドの数だけ（最大 2）r4（r2 の順）・r5（r1 の順）を足す．
# 全ての問の行に --record-qid を付ける．results/<run_id>/failure.json がある回は飛ばす．実行の間は commit しない．config.yaml は終了時に元へ戻す．
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
LOG=$1
shift
D=scripts/d0009
FREE_MIB=1000
NEED_CONSEC=3
INTERVAL_S=60
MAX_WAIT_S=7200
GATE_INTERVAL_S=300
DEADLINE_S=30
FAIL_THRESHOLD=1
MIN_PRE_STOP_HIT=0.86
MIN_BASELINE_RETURNED=0.95
RESULT_DIR=results/d0009_b1_exclusion_i36
PREFIX=d0009_b1_exclusion_i36_
NODE_PORT=8100
CONTROL=wafl-ctrl5
REMOTE_DIR=/home/denjo/workspace/ktakahashi/atrium
SSH_USER=denjo
SUMMARY_COPY=/tmp/i36_gv/summary
COMMON_ARGS=(--baseline-s 15 --down-s 45 --post-s 30 --bin-s 5 --poll-interval-s 0.2 --stream-parallel 8 --central-questions 200 --record-qid)
# Iteration 35 と同じ modes・ks の順（Random(12) で固定済み．journal の Iteration 30）
ARGS_r1=(--modes availability,container --ks 1,3,10,6)
ARGS_r2=(--modes container,availability --ks 6,3,1,10)
ARGS_r3=(--modes availability,container --ks 1,3,10,6)
ARGS_r4=("${ARGS_r2[@]}")
ARGS_r5=("${ARGS_r1[@]}")
ARGS_d0_null=("${ARGS_r1[@]}")
ARGS_smoke=(--modes availability,container --ks 1 --down-s 15 --baseline-s 10 --post-s 10 --stream-parallel 8 --central-questions 200 --record-qid)
ORDER=(smoke d0_null r1 r2 r3)
EXPLICIT_ORDER=0
if [ "$#" -gt 0 ]; then ORDER=("$@"); EXPLICIT_ORDER=1; fi
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

# G-GPU-pre：満たすまで GATE_INTERVAL_S ごとに確かめ直す（最大 MAX_WAIT_S 秒）．(a) だけを満たさない台は Ollama を 1 回だけ再起動する．
# 戻り値 0：満たした／1：満たさなかった（流さずに止まる）
ollama_restarted=0
wait_for_gpu_gate_pre() {
  local run=$1 waited=0 rc hosts h
  while true; do
    uv run python $D/b1_gpu_gate.py pre --out "results/$run/gpu_gate_pre.json" >> "$LOG" 2>&1
    rc=$?
    if [ "$rc" = 0 ]; then echo "[$(date +%T)] $run G-GPU-pre ok" >> "$LOG"; return 0; fi
    echo "[$(date +%T)] $run G-GPU-pre not met (rc=$rc, waited ${waited}s)" >> "$LOG"
    if [ "$rc" = 2 ] && [ "$ollama_restarted" = 0 ]; then
      hosts=$(jq -r '.hosts[] | select(.ok | not) | .host' "results/$run/gpu_gate_pre.json")
      ollama_restarted=1
      for h in $hosts; do
        echo "[$(date +%T)] $run restarting atrium-ollama-1 on $h (once; only (a) failed)" >> "$LOG"
        ssh -n -o BatchMode=yes "$h" "cd $REMOTE_DIR && docker compose restart ollama" >> "$LOG" 2>&1
      done
      cp "results/$run/gpu_gate_pre.json" "results/$run/gpu_gate_pre_before_restart.json"
      sleep 60
      continue
    fi
    if [ "$waited" -ge "$MAX_WAIT_S" ]; then return 1; fi
    sleep "$GATE_INTERVAL_S"
    waited=$((waited + GATE_INTERVAL_S))
  done
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

# 止める前の問の「答えられた」割合 ≥ MIN_PRE_STOP_HIT かつ回答を返した割合 ≥ MIN_BASELINE_RETURNED（G-FP (i)・G-B）か．戻り値 0：満たす
fp_b_ok() {
  jq -e --argjson h "$MIN_PRE_STOP_HIT" --argjson r "$MIN_BASELINE_RETURNED" \
    '([.p2p[].owner.baseline.n_hit] | add) / ([.p2p[].owner.baseline.n] | add) >= $h
     and ([.p2p[].owner.baseline.n_returned] | add) / ([.p2p[].owner.baseline.n] | add) >= $r' \
    "results/$1/failure.json" > /dev/null
}

# 1 回の実行（G-GPU-pre → failure-run → G-GPU-post → G-R・G-TB の確かめ）
run_one() {
  local tag=$1 run="${PREFIX}${1}" args_name="ARGS_${1}" deadline=$2
  local -n extra=$args_name
  if [ -f "results/$run/failure.json" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; return 0
  fi
  echo "[$(date +%F_%T)] BEGIN $run (D=$deadline)" >> "$LOG"
  mkdir -p "results/$run"
  if wait_for_free_gpus; then confound=none; else confound="GPU not free after ${MAX_WAIT_S}s; run anyway"; fi
  echo "[$(date +%T)] $run confound=$confound" >> "$LOG"
  echo "$confound" > "results/$run/gpu_wait_result.txt"
  if ! wait_for_gpu_gate_pre "$run"; then
    echo "[$(date +%T)] $run G-GPU-pre FAILED after ${MAX_WAIT_S}s; not running [needs-human] (see results/$run/gpu_gate_pre.json)" >> "$LOG"; exit 3
  fi
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_pre.txt" 2>&1 < /dev/null
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_start.txt" 2>&1 < /dev/null
  local since_s until_s
  since_s=$(date +%s)
  bash scripts/tasks/live_run.sh failure_run "$run" "${COMMON_ARGS[@]}" "${extra[@]}" --answer-deadline-s "$deadline" > "$LOG.$run.live" 2>&1 < /dev/null
  local rc=$?
  until_s=$(date +%s)
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_end.txt" 2>&1 < /dev/null
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_post.txt" 2>&1 < /dev/null
  uv run python $D/b1_gpu_gate.py post --out "results/$run/gpu_gate_post.json" --pre "results/$run/gpu_gate_pre.json" --since "$since_s" --until "$until_s" >> "$LOG" 2>&1
  echo "[$(date +%T)] $run live_run=$rc G-GPU-post rc=$? (window $since_s-$until_s)" >> "$LOG"
  if ! ensure_all_nodes_up "$run"; then
    echo "[$(date +%T)] $run G-R FAILED (some nodes still not serving; see results/$run/g_r_not_serving_after.txt)" >> "$LOG"; exit 1
  fi
  echo "[$(date +%T)] $run G-R ok (all nodes serving)" >> "$LOG"
  if [ ! -f "results/$run/failure.json" ]; then
    echo "[$(date +%T)] $run FAILED (no failure.json; inspect $LOG.$run.live and results/$run/start.log)" >> "$LOG"; exit 1
  fi
  # 外す仕組みの on・off が意図どおりであること（D0 は全条件で peer_health が無い．それ以外は全条件にある）
  local n_without_health expected=0
  n_without_health=$(jq '[.p2p[] | select(.peer_health == null)] | length' "results/$run/failure.json")
  if [ "$tag" = d0_null ]; then expected=$(jq '.p2p | length' "results/$run/failure.json"); fi
  if [ "$n_without_health" != "$expected" ]; then
    echo "[$(date +%T)] $run FAILED (peer_health on/off differs from the intended: without=$n_without_health expected=$expected)" >> "$LOG"; exit 1
  fi
  # G-TB：表に無い組による失敗が 1 件でもあれば，次へ進まずに止めて表を直す
  misses=$(jq '[.p2p[].questions[] | select(.failure_kind == "table_miss")] | length' "results/$run/failure.json")
  echo "[$(date +%T)] $run table_miss=$misses" >> "$LOG"
  if [ "$misses" != 0 ]; then
    echo "[$(date +%T)] $run G-TB FAILED (table_miss=$misses); fix the table before the next run" >> "$LOG"; exit 1
  fi
  fp_b_ok "$run" && echo "[$(date +%T)] $run G-FP(i)/G-B ok" >> "$LOG" || echo "[$(date +%T)] $run G-FP(i)/G-B NOT met" >> "$LOG"
}

# G-TB：制御点の表が gpu4 の表と同じであること（deploy が制御点の表を質問者へ配る）
TABLE=enronqa/ce_table/holdout_owner.jsonl
local_sha=$(sha256sum "artifacts/$TABLE" | cut -d' ' -f1)
control_sha=$(ssh -n -o BatchMode=yes "$CONTROL" "sha256sum /home/denjo/atrium-data/$TABLE" 2> /dev/null | cut -d' ' -f1)
if [ "$local_sha" != "$control_sha" ]; then echo "[$(date +%T)] TABLE MISMATCH between gpu4 and control" >> "$LOG"; exit 1; fi
echo "[$(date +%T)] table sha256 $local_sha" >> "$LOG"

# config を作って deploy する（config.yaml は live_run.sh が各回の前に制御点へ同期する．chain の終了時（trap）まで戻さない）
# $1：on（peer_health あり．FAIL_THRESHOLD）／null（D0．a6f_config.py の出力のまま）
set_config_and_deploy() {
  local kind=$1 rc
  if [ "$kind" = on ]; then
    uv run python $D/b1_config.py --fail-threshold "$FAIL_THRESHOLD" >> "$LOG" 2>&1; rc=$?
  else
    git checkout -- config.yaml && uv run python $D/a6f_config.py >> "$LOG" 2>&1; rc=$?
  fi
  if [ "$rc" != 0 ]; then echo "[$(date +%T)] SETCFG FAILED ($kind)" >> "$LOG"; exit 1; fi
  if ! retry "$LOG.deploy_$kind" mise run deploy; then echo "[$(date +%T)] DEPLOY FAILED ($kind)" >> "$LOG"; exit 1; fi
  deployed_kind=$kind
  echo "[$(date +%T)] deployed ($kind, fail_threshold=$FAIL_THRESHOLD)" >> "$LOG"
}
deployed_kind=none
# 実行 tag に必要な deploy（smoke・r*：on，d0_null：null）に，今の deploy が合っていなければ替える
ensure_deployed_for() {
  local want=on
  if [ "$1" = d0_null ]; then want=null; fi
  if [ "$deployed_kind" != "$want" ]; then set_config_and_deploy "$want"; fi
}

# G-V（CPU だけ）：config.yaml を on の条件にしてから，要約を制御点から読み取りでコピーして照合する
mkdir -p $RESULT_DIR /tmp/i36_gv/router
if ! uv run python $D/b1_config.py --fail-threshold "$FAIL_THRESHOLD" >> "$LOG" 2>&1; then echo "[$(date +%T)] SETCFG FAILED (for G-V)" >> "$LOG"; exit 1; fi
rsync -a -e "ssh -o BatchMode=yes" "$CONTROL:/home/denjo/atrium-data/enronqa/summary" /tmp/i36_gv/ >> "$LOG" 2>&1
if ! uv run python $D/b1_gv_check.py --summary-dir $SUMMARY_COPY --out $RESULT_DIR/g_v.json >> "$LOG" 2>&1; then
  echo "[$(date +%T)] G-V FAILED (visible=all differs from None); not running" >> "$LOG"; exit 1
fi
echo "[$(date +%T)] G-V ok" >> "$LOG"

# 前の実験のノードを止める（Ollama は deploy が起動する．G-GPU-pre は deploy の後）
bash scripts/tasks/remote.sh stop > "$LOG.pre_stop" 2>&1 < /dev/null
bash $D/gpu_apps_snapshot.sh > $RESULT_DIR/gpu_apps_pre_deploy.txt 2>&1 < /dev/null

deadline=$DEADLINE_S
for tag in "${ORDER[@]}"; do
  ensure_deployed_for "$tag"
  run_one "$tag" "$deadline"
  if [ "$tag" = smoke ]; then
    smoke_run="${PREFIX}smoke"
    # 質問者のコードの例外（failure_kind=other）が 1 件でもあれば，本番へ進まずに止める
    other=$(jq '[.p2p[].questions[] | select(.failure_kind == "other")] | length' "results/$smoke_run/failure.json")
    echo "[$(date +%T)] smoke failure_kind=other count=$other" >> "$LOG"
    if [ "$other" != 0 ]; then echo "[$(date +%T)] smoke SANITY FAILED (requester exceptions); inspect results/$smoke_run/requester.log" >> "$LOG"; exit 1; fi
    # 止める前の deadline 0 件（Iteration 35 の学び）．全体の件数も記録する
    pre_deadline=$(jq '[.p2p[].questions[] | select(.failure_kind == "deadline" and .t_s < 0)] | length' "results/$smoke_run/failure.json")
    all_deadline=$(jq '[.p2p[].questions[] | select(.failure_kind == "deadline")] | length' "results/$smoke_run/failure.json")
    echo "[$(date +%T)] smoke deadline pre_stop=$pre_deadline all=$all_deadline" >> "$LOG"
    if [ "$pre_deadline" != 0 ]; then echo "[$(date +%T)] smoke FAILED (deadline before stop: $pre_deadline); not running the rest" >> "$LOG"; exit 1; fi
    fp=$(false_positive_drops "${PREFIX}smoke")
    echo "[$(date +%T)] smoke false_positive_drops=$fp (fail_threshold=$FAIL_THRESHOLD)" >> "$LOG"
    if [ "$fp" != 0 ] && [ "$FAIL_THRESHOLD" = 1 ]; then
      FAIL_THRESHOLD=2
      echo "$FAIL_THRESHOLD" > $RESULT_DIR/fail_threshold_raised.txt
      echo "[$(date +%T)] raising fail_threshold to 2 once; redeploy and rerun smoke as smoke_ft2" >> "$LOG"
      set_config_and_deploy on
      ARGS_smoke_ft2=("${ARGS_smoke[@]}")
      run_one smoke_ft2 "$deadline"
      echo "[$(date +%T)] smoke_ft2 false_positive_drops=$(false_positive_drops "${PREFIX}smoke_ft2")" >> "$LOG"
    fi
  fi
  if [ "$tag" = d0_null ]; then
    # G-ENV：満たさなければ本番を流さずに止める
    uv run python $D/b1_verdict.py --env-check "results/${PREFIX}d0_null" > "$RESULT_DIR/g_env.json" 2>> "$LOG"
    if [ "$?" != 0 ]; then echo "[$(date +%T)] G-ENV FAILED (see $RESULT_DIR/g_env.json); not running the rounds" >> "$LOG"; exit 1; fi
    echo "[$(date +%T)] G-ENV ok" >> "$LOG"
  fi
done

# ラウンドの追加：G-GPU-post だけで無効（G-FP (i)・G-B は満たす）の r1〜r3 の数だけ，最大 2（r4・r5）
if [ "$EXPLICIT_ORDER" = 0 ]; then
  n_extra=0
  for r in r1 r2 r3; do
    run="${PREFIX}${r}"
    if [ -f "results/$run/gpu_gate_post.json" ] && [ "$(jq '.pass' "results/$run/gpu_gate_post.json")" != true ] && fp_b_ok "$run"; then
      n_extra=$((n_extra + 1))
    fi
  done
  if [ "$n_extra" -gt 2 ]; then n_extra=2; fi
  echo "[$(date +%T)] extra rounds (G-GPU-post only): $n_extra" >> "$LOG"
  for r in r4 r5; do
    if [ "$n_extra" -le 0 ]; then break; fi
    ensure_deployed_for "$r"
    run_one "$r" "$deadline"
    n_extra=$((n_extra - 1))
  done
fi
bash scripts/tasks/remote.sh stop > "$LOG.final_stop" 2>&1 < /dev/null
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
