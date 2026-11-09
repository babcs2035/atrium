#!/usr/bin/env bash
# d0009 B2（Iteration 37）：cross-encoder の採点を G 枚の GPU（wafl502〜509）に分けたときの処理量の測定を実機で流す連続実行．
#   (nohup setsid sg docker -c "bash scripts/d0009/b2_chain.sh results/d0009_b2_split_scoring/chain.log" > /dev/null 2>&1 < /dev/null &)
#   引数 2 個目以降に段階（publish start env0 gs cal smoke prod stop）を並べると，その順に流す（省略時は全部）．
# 段階：
#   publish  操作端末で atrium-full を build して制御点の registry へ push し，制御点で pull する（採点の server と score-bench のコードを載せる）
#   env0     G-ENV0：各機の他の研究者の GPU のプロセスが 0・温度 ≤ 60 °C・SW/HW Slowdown が Not Active（空かなければ最大 2 時間待ち，足りなければ格子を置き換える）
#   start    採点の server を各機で起動（Ollama の llama3.1:8b は keep_alive 0 で降ろす）．stop が止める．
#   gs       G-S：G = 最大で 288 問を 1 周し，全 4,320 組の得点を表と比べる（不合格なら本番へ進まない）
#   cal      G-CAL：各機を 1 枚ずつ流して入れ子の順を決める
#   smoke    G = 2・30 秒
#   prod     本番：G の格子を 3 ラウンド（G の順は種 37 の並べ替え）．無効な実行は同じラウンドの最後に 1 回だけ流し直す
# 各実行の前に使う機の温度が ≤ 60 °C・SW Thermal Slowdown が Not Active になるのを最大 15 分待ち，実行中は 5 秒ごとに温度と Slowdown を見て，
# 83 °C 以上か SW Thermal Slowdown が Active になったら bench を止めて無効にする．生成・取得・probe は流さない（採点だけ）．
# results/d0009_b2_split_scoring_<name>/ に実行ごとの記録（bench の出力・前後の nvidia-smi・1 秒ごとの GPU の記録・valid.json）を残す．
# 実行の間は commit しない．
source "$(dirname "${BASH_SOURCE[0]}")/../tasks/lib.sh"
set +e
set -u
cd "$REPO_ROOT" || exit 1
LOG=$1
shift
D=scripts/d0009
BASE=d0009_b2_split_scoring
RESULT_DIR=results/$BASE
SCORER_PORT=8200
# 制御点の registry から見た image（scripts/remote/lib.sh の IMAGE_FULL と同じ．採点の server と score-bench のコードを含む）
IMAGE_FULL="localhost:$REGISTRY_PORT/atrium-full:latest"
HOT_C=60
ABORT_C=83
COOL_MAX_S=900
COOL_POLL_S=20
ENV0_MAX_WAIT_S=7200
ENV0_POLL_S=60
WATCH_POLL_S=5
BENCH_GRACE_S=300
CONCURRENCY=30
SSH_B=(-o BatchMode=yes -o ConnectTimeout=15)
CONTROL_SSH=(ssh -n "${SSH_B[@]}" "$CONTROL")
ALL_HOSTS=(wafl502 wafl503 wafl504 wafl505 wafl506 wafl507 wafl508 wafl509)
STAGES=(publish env0 start gs cal smoke prod)
if [ "$#" -gt 0 ]; then STAGES=("$@"); fi
mkdir -p "$RESULT_DIR"
say() { echo "[$(date +%F_%T)] $*" >> "$LOG"; }
ip_of() { echo "192.168.15.10${1#wafl50}"; }
ssh_retry() {
  local i rc
  for i in 1 2 3; do
    ssh -n "${SSH_B[@]}" "$@"
    rc=$?
    [ "$rc" -ne 255 ] && return "$rc"
    sleep 5
  done
  return 255
}
snap() { # snap <host> <out>
  local i
  for i in 1 2 3; do
    ssh "${SSH_B[@]}" "$1" bash -s < $D/b2_gpu_snapshot.sh > "$2" 2> /dev/null && grep -q '^other_procs=' "$2" && return 0
    sleep 5
  done
  return 1
}
snap_all() { # snap_all <suffix> <outdir> <host>...
  local suffix=$1 outdir=$2 h
  shift 2
  for h in "$@"; do snap "$h" "$outdir/snap_${h}_$suffix.txt" & done
  wait
}
# 温度と SW/HW Slowdown を 1 回読む．出力: "<温度> <SW Active|NotActive> <HW ...>"（読めなければ "? ? ?"）
gpu_state() {
  ssh_retry "$1" "nvidia-smi --query-gpu=temperature.gpu,clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_slowdown --format=csv,noheader,nounits" 2> /dev/null | tr -d ' ' | tr ',' ' ' | grep -E '^[0-9]+ ' || echo "? ? ?"
}
# 使う全ての機が冷えるまで待つ．待った秒数を $1 に書く．戻り値 0：冷えた／1：COOL_MAX_S の間に冷えなかった
wait_cool() { # wait_cool <waited_file> <host>...
  local out=$1 waited=0 ok h state temp sw hw
  shift
  while true; do
    ok=1
    for h in "$@"; do
      read -r temp sw hw <<< "$(gpu_state "$h")"
      if [ "$temp" = "?" ] || [ "$temp" -gt "$HOT_C" ] || [ "$sw" != NotActive ] || [ "$hw" != NotActive ]; then ok=0; fi
    done
    [ "$ok" = 1 ] && { echo "$waited" > "$out"; return 0; }
    [ "$waited" -ge "$COOL_MAX_S" ] && { echo "$waited" > "$out"; return 1; }
    sleep "$COOL_POLL_S"
    waited=$((waited + COOL_POLL_S))
  done
}
gpu_log_start() { # gpu_log_start <host> <name>
  ssh_retry "$1" "mkdir -p $REMOTE_DIR/b2 && rm -f $REMOTE_DIR/b2/$2.gpu.csv && \
    setsid nohup bash -c 'echo \$\$ > $REMOTE_DIR/b2/$2.pid; while :; do echo \"\$(date +%s.%N),\$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_slowdown --format=csv,noheader,nounits | tr -d \" \")\"; sleep 1; done' > $REMOTE_DIR/b2/$2.gpu.csv 2> /dev/null < /dev/null &"
}
gpu_log_stop() { # gpu_log_stop <host> <name> <out>
  ssh_retry "$1" "kill \$(cat $REMOTE_DIR/b2/$2.pid) 2> /dev/null; sleep 1; cat $REMOTE_DIR/b2/$2.gpu.csv" > "$3" 2> /dev/null
}
# 実機の条件を 1 回流す．USE_HOSTS（使う機の配列）・RUN_NAME・WARMUP・MEASURE・EXTRA（bench の追加の引数の配列）を呼び出し側が設定する．
# 戻り値 0：有効な実行／1：無効（理由は valid.json）
run_bench() {
  local name=$RUN_NAME rdir="results/${BASE}_$RUN_NAME" cname="atrium-score-bench-${BASE}-$RUN_NAME" h scorers="" hosts_csv state t_end temp sw hw
  hosts_csv=$(IFS=,; echo "${USE_HOSTS[*]}")
  mkdir -p "$rdir"
  rm -f "$rdir/aborted.txt"
  for h in "${USE_HOSTS[@]}"; do scorers="$scorers --scorer http://$(ip_of "$h"):$SCORER_PORT"; done
  if ! wait_cool "$rdir/cool_wait_s.txt" "${USE_HOSTS[@]}"; then
    echo "did not cool to ${HOT_C} C within ${COOL_MAX_S}s" > "$rdir/aborted.txt"
    say "$name not run: hosts did not cool"
    uv run python $D/b2_verdict.py check --run "$rdir" --hosts "$hosts_csv" >> "$LOG" 2>&1
    return 1
  fi
  snap_all pre "$rdir" "${USE_HOSTS[@]}"
  for h in "${USE_HOSTS[@]}"; do gpu_log_start "$h" "$name"; done
  say "$name BEGIN hosts=$hosts_csv warmup=$WARMUP measure=$MEASURE ${EXTRA[*]:-}"
  # shellcheck disable=SC2086
  "${CONTROL_SSH[@]}" "mkdir -p $REMOTE_DIR/results/${BASE}_$name && docker rm -f $cname > /dev/null 2>&1; \
    docker run -d --name $cname --network host --user \$(id -u):\$(id -g) -e HOME=/tmp -v $DATA_DIR:/data:ro \
    -v $REMOTE_DIR/results:/results -v $REMOTE_DIR/config.yaml:/app/config.yaml:ro $IMAGE_FULL \
    atrium --config /app/config.yaml score-bench --data-dir /data --out-dir /results/${BASE}_$name \
    --run-results /results/d0009_a3_p1/results.jsonl --concurrency $CONCURRENCY --warmup-s $WARMUP --measure-s $MEASURE \
    $scorers ${EXTRA[*]:-}" >> "$LOG" 2>&1
  t_end=$(($(date +%s) + ${WARMUP%.*} + ${MEASURE%.*} + BENCH_GRACE_S))
  # one-pass（G-S）は終わるまでの時間が決まらないので，余裕を長く取る
  if printf '%s\n' "${EXTRA[@]:-}" | grep -q -- '--one-pass'; then t_end=$((t_end + 1800)); fi
  while true; do
    state=$("${CONTROL_SSH[@]}" "docker inspect -f '{{.State.Running}}' $cname" 2> /dev/null || echo unknown)
    [ "$state" = false ] && break
    # 安全の打ち切り：どの機かが 83 °C 以上か SW Thermal Slowdown が Active
    for h in "${USE_HOSTS[@]}"; do
      read -r temp sw hw <<< "$(gpu_state "$h")"
      if [ "$temp" != "?" ] && { [ "$temp" -ge "$ABORT_C" ] || [ "$sw" = Active ]; }; then
        echo "$h temp=$temp sw=$sw" > "$rdir/aborted.txt"
      fi
    done
    if [ ! -f "$rdir/aborted.txt" ] && [ "$(date +%s)" -ge "$t_end" ]; then echo "bench did not finish by the deadline" > "$rdir/aborted.txt"; fi
    if [ -f "$rdir/aborted.txt" ]; then
      "${CONTROL_SSH[@]}" "docker kill $cname > /dev/null 2>&1"
      say "$name ABORTED: $(cat "$rdir/aborted.txt")"
      break
    fi
    sleep "$WATCH_POLL_S"
  done
  sleep 2
  snap_all post "$rdir" "${USE_HOSTS[@]}"
  for h in "${USE_HOSTS[@]}"; do gpu_log_stop "$h" "$name" "$rdir/gpu_$h.csv"; done
  "${CONTROL_SSH[@]}" "docker logs $cname 2>&1 | tail -n 5; docker rm -f $cname > /dev/null 2>&1" > "$rdir/bench_container_tail.txt" 2>&1
  nrsync_results "$name"
  local smoke_flag=""
  [ "$name" = smoke ] && smoke_flag="--smoke"
  uv run python $D/b2_verdict.py check --run "$rdir" --hosts "$hosts_csv" $smoke_flag >> "$LOG" 2>&1
}
nrsync_results() {
  local i
  for i in 1 2 3; do
    rsync -az -e "ssh ${SSH_B[*]}" "$CONTROL:$REMOTE_DIR/results/${BASE}_$1/" "results/${BASE}_$1/" && return 0
    sleep 5
  done
  return 1
}
exit_if_failed() { # exit_if_failed <rc> <message>
  if [ "$1" -ne 0 ]; then say "$2"; exit 1; fi
}
read_order() { ORDER=($(cat "$RESULT_DIR/order.txt" 2> /dev/null || cat "$RESULT_DIR/hosts.txt")); }
declare -A BAD_COUNT=()

stage_publish() {
  local head
  head=$(git rev-parse --short=12 HEAD)
  if [ -n "$(git status --porcelain -- src pyproject.toml uv.lock Dockerfile)" ]; then say "publish: uncommitted changes in the image sources"; exit 1; fi
  sync_to_control
  (
    open_registry_tunnel
    local image="localhost:$LOCAL_REGISTRY_PORT/atrium-full:latest" attempt
    docker build -q --network host --target full --build-arg GIT_HEAD="$head" -t "$image" . > /dev/null || exit 1
    for attempt in 1 2 3; do
      if docker push -q "$image" > /dev/null; then exit 0; fi
      [ "$attempt" = 3 ] && exit 1
      sleep "$PUSH_RETRY_WAIT_S"
    done
  )
  exit_if_failed $? "publish FAILED (build or push)"
  "${CONTROL_SSH[@]}" "docker pull -q $IMAGE_FULL > /dev/null" >> "$LOG" 2>&1
  exit_if_failed $? "publish FAILED (pull on control)"
  say "published atrium-full $head"
}

# G-ENV0：他の研究者の GPU のプロセスが 0（最大 2 時間待ち）・温度・Slowdown を各機で確かめ，使える機と格子を決める
stage_env0() {
  local waited=0 h usable=() unusable=()
  mkdir -p "$RESULT_DIR/env0"
  while true; do
    usable=()
    unusable=()
    snap_all pre "$RESULT_DIR/env0" "${ALL_HOSTS[@]}"
    for h in "${ALL_HOSTS[@]}"; do
      if [ "$(grep '^other_procs=' "$RESULT_DIR/env0/snap_${h}_pre.txt" | cut -d= -f2)" = 0 ]; then usable+=("$h"); else unusable+=("$h"); fi
    done
    [ "${#unusable[@]}" -eq 0 ] && break
    [ "$waited" -ge "$ENV0_MAX_WAIT_S" ] && break
    say "env0: other GPU processes on ${unusable[*]}; waiting"
    sleep "$ENV0_POLL_S"
    waited=$((waited + ENV0_POLL_S))
  done
  # 温度 ≤ 60 °C・Slowdown が Not Active（冷えるのを最大 COOL_MAX_S 待つ．冷えない機は使えない機にする）
  local cooled=()
  for h in "${usable[@]}"; do
    if wait_cool "$RESULT_DIR/env0/cool_wait_$h.txt" "$h"; then cooled+=("$h"); else unusable+=("$h"); fi
  done
  say "env0: usable=${cooled[*]} unusable=${unusable[*]:-none} waited=${waited}s"
  echo "${unusable[*]:-none}" > "$RESULT_DIR/env0/unusable.txt"
  local csv
  csv=$(IFS=,; echo "${cooled[*]}")
  say "env0: grid $(uv run python $D/b2_verdict.py set-hosts --hosts "$csv" 2>&1)"
  rm -f "$RESULT_DIR/order.txt"
}

stage_start() {
  local ips=() h
  for h in $(cat "$RESULT_DIR/hosts.txt"); do ips+=("$(ip_of "$h")"); done
  bash scripts/tasks/remote.sh score_server start "${ips[@]}" >> "$LOG.start" 2>&1 < /dev/null
  exit_if_failed $? "start FAILED (see $LOG.start)"
  say "scorers started on ${ips[*]}"
}

stage_gs() {
  local rdir="results/${BASE}_gs"
  if [ -f "$rdir/gs.json" ]; then say "gs skip (exists)"; return; fi
  USE_HOSTS=($(cat "$RESULT_DIR/hosts.txt"))
  RUN_NAME=gs WARMUP=0 MEASURE=0 EXTRA=(--one-pass --dump-scores)
  run_bench
  exit_if_failed $? "G-S run INVALID: $(cat "$rdir/valid.json" 2> /dev/null | tr -d '\n')"
  uv run python $D/b2_verdict.py gs --run "$rdir" >> "$LOG" 2>&1
  exit_if_failed $? "G-S NOT PASSED (see $rdir/gs.json); production is not run"
  say "G-S passed"
}

stage_cal() {
  local h
  if [ -f "$RESULT_DIR/order.txt" ]; then say "cal skip (order.txt exists)"; return; fi
  for h in $(cat "$RESULT_DIR/hosts.txt"); do
    if [ -f "results/${BASE}_cal_$h/valid.json" ]; then continue; fi
    USE_HOSTS=("$h") RUN_NAME="cal_$h" WARMUP=10 MEASURE=60 EXTRA=()
    run_bench
    say "cal $h valid=$?"
  done
  uv run python $D/b2_verdict.py cal >> "$LOG" 2>&1
  exit_if_failed $? "cal FAILED"
  say "G-CAL order: $(cat "$RESULT_DIR/order.txt")"
}

stage_smoke() {
  read_order
  if [ -f "results/${BASE}_smoke/valid.json" ] && [ "$(jq .valid "results/${BASE}_smoke/valid.json")" = true ]; then say "smoke skip (valid exists)"; return; fi
  USE_HOSTS=("${ORDER[@]:0:2}") RUN_NAME=smoke WARMUP=10 MEASURE=30 EXTRA=()
  run_bench
  exit_if_failed $? "smoke INVALID: $(cat "results/${BASE}_smoke/valid.json" 2> /dev/null | tr -d '\n')"
  say "smoke ok"
}

# 本番の 1 実行．その機が 2 回無効になっていれば流さない（事前登録）
prod_one() { # prod_one <round> <G> <suffix>
  local rnd=$1 g=$2 suffix=$3 name h reasons
  name="r${rnd}_g${g}$suffix"
  if [ -f "results/${BASE}_$name/valid.json" ]; then return "$([ "$(jq .valid "results/${BASE}_$name/valid.json")" = true ] && echo 0 || echo 1)"; fi
  for h in "${ORDER[@]:0:$g}"; do
    if [ "${BAD_COUNT[$h]:-0}" -ge 2 ]; then say "$name skipped: $h was invalid twice"; return 2; fi
  done
  USE_HOSTS=("${ORDER[@]:0:$g}") RUN_NAME=$name WARMUP=20 MEASURE=120 EXTRA=()
  run_bench
  if [ "$?" -ne 0 ]; then
    reasons=$(jq -r '.reasons[]' "results/${BASE}_$name/valid.json" 2> /dev/null)
    for h in "${USE_HOSTS[@]}"; do
      if echo "$reasons" | grep -q "^$h:"; then BAD_COUNT[$h]=$(("${BAD_COUNT[$h]:-0}" + 1)); fi
    done
    say "$name INVALID: $(echo "$reasons" | tr '\n' ';')"
    return 1
  fi
  say "$name valid"
  return 0
}

stage_prod() {
  read_order
  local rnd=0 line g retry
  while read -r line; do
    rnd=$((rnd + 1))
    retry=()
    for g in $line; do
      prod_one "$rnd" "$g" ""
      [ "$?" -eq 1 ] && retry+=("$g")
    done
    for g in "${retry[@]:-}"; do
      [ -n "$g" ] && prod_one "$rnd" "$g" "_retry"
    done
  done < <(uv run python $D/b2_verdict.py orders)
  uv run python $D/b2_verdict.py summary >> "$LOG" 2>&1
  say "prod done; summary written to $RESULT_DIR"
}

stage_stop() {
  local ips=() h
  for h in $(cat "$RESULT_DIR/hosts.txt"); do ips+=("$(ip_of "$h")"); done
  bash scripts/tasks/remote.sh score_server stop "${ips[@]}" >> "$LOG.stop" 2>&1 < /dev/null
  mkdir -p "$RESULT_DIR/after_stop"
  snap_all post "$RESULT_DIR/after_stop" "${ALL_HOSTS[@]}"
  say "scorers stopped; post-stop snapshots in $RESULT_DIR/after_stop"
}

say "BEGIN stages: ${STAGES[*]} (git $(git rev-parse --short=12 HEAD))"
for stage in "${STAGES[@]}"; do
  "stage_$stage"
done
say "ALL DONE"
