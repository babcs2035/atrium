#!/usr/bin/env bash
# d0009 B2（Iteration 38）：cross-encoder の採点を fp16 にしたときの速さと選ぶ候補の一致を実機（wafl502 の 1 枚）で流す連続実行．
#   (nohup setsid sg docker -c "bash scripts/d0009/b2c_chain.sh results/d0009_b2_scoring_cost_fp16/chain.log" > /dev/null 2>&1 < /dev/null &)
#   引数 2 個目以降に段階（publish env0 f32pass f16pass smoke prod stop）を並べると，その順に流す（省略時は全部）．
#   環境変数 HOST（既定 wafl502）で使う機を替える．wafl503 での本番の流し直しは `HOST=wafl503 ... env0 prod stop`（実行名に w503_ が付く）．
# 段階：
#   publish  atrium-full を build して制御点の registry へ push し，制御点で pull する（dtype の引数を載せる）
#   env0     G-ENV0：他の研究者の GPU のプロセスが 0（最大 2 時間待つ）・温度 ≤ 60 °C・SW/HW Slowdown が Not Active．機を host.txt へ書く
#   f32pass  G-F32：fp32 の server で 288 問を 1 周（--one-pass --dump-scores）し，表・ce_features と比べる（不合格なら本番へ進まない）
#   f16pass  fp16 の server で 1 周してダンプし，G-AT（両 dtype の attention の実装・tokenizer の最大長）と H-F1 の数値を出す
#   smoke    fp16・慣らし 10 秒・区間 30 秒
#   prod     本番：3 ラウンド．各ラウンドで fp32 と fp16 を 1 回ずつ（順は種 38）．慣らし 60 秒 + 区間 600 秒．無効な実行は同じラウンドの最後に 1 回だけ流し直す
#   stop     採点の server を止め，後の nvidia-smi を残す
# 各実行の前に機の温度が ≤ 60 °C・SW Thermal Slowdown が Not Active になるのを最大 15 分待ち，実行中は 5 秒ごとに温度と Slowdown を見て，
# 83 °C 以上か SW Thermal Slowdown が Active になったら bench を止めて無効にする（B120・B122・B123 の熱の運用）．生成・取得・probe は流さない．
# results/d0009_b2_fp16_<name>/ に実行ごとの記録（bench の出力・前後の nvidia-smi・1 秒ごとの GPU の記録・healthz.json・valid.json）を残す．
# 実行の間は commit しない．機が 2 回無効になったら本番を止める（機を wafl503 に替えた流し直しは事前登録の規則．backlog に記録する）．
source "$(dirname "${BASH_SOURCE[0]}")/../tasks/lib.sh"
set +e
set -u
cd "$REPO_ROOT" || exit 1
LOG=$1
shift
D=scripts/d0009
BASE=d0009_b2_fp16
RESULT_DIR=results/d0009_b2_scoring_cost_fp16
HOST=${HOST:-wafl502}
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
ONE_PASS_EXTRA_WAIT_S=1800
CONCURRENCY=30
PROD_WARMUP=60
PROD_MEASURE=600
MAX_INVALID_PER_HOST=2
SSH_B=(-o BatchMode=yes -o ConnectTimeout=15)
CONTROL_SSH=(ssh -n "${SSH_B[@]}" "$CONTROL")
STAGES=(publish env0 f32pass f16pass smoke prod)
if [ "$#" -gt 0 ]; then STAGES=("$@"); fi
mkdir -p "$RESULT_DIR"
say() { echo "[$(date +%F_%T)] $*" >> "$LOG"; }
ip_of() { echo "192.168.15.10${1#wafl50}"; }
HOST_IP=$(ip_of "$HOST")
CURRENT_DTYPE=none
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
# 温度と SW/HW Slowdown を 1 回読む．出力: "<温度> <SW Active|NotActive> <HW ...>"（読めなければ "? ? ?"）
gpu_state() {
  ssh_retry "$1" "nvidia-smi --query-gpu=temperature.gpu,clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_slowdown --format=csv,noheader,nounits" 2> /dev/null | tr -d ' ' | tr ',' ' ' | grep -E '^[0-9]+ ' || echo "? ? ?"
}
# 機が冷えるまで待つ．待った秒数を $1 に書く．戻り値 0：冷えた／1：COOL_MAX_S の間に冷えなかった
wait_cool() { # wait_cool <waited_file>
  local out=$1 waited=0 temp sw hw
  while true; do
    read -r temp sw hw <<< "$(gpu_state "$HOST")"
    if [ "$temp" != "?" ] && [ "$temp" -le "$HOT_C" ] && [ "$sw" = NotActive ] && [ "$hw" = NotActive ]; then
      echo "$waited" > "$out"
      return 0
    fi
    [ "$waited" -ge "$COOL_MAX_S" ] && { echo "$waited" > "$out"; return 1; }
    sleep "$COOL_POLL_S"
    waited=$((waited + COOL_POLL_S))
  done
}
gpu_log_start() { # gpu_log_start <name>
  # ssh が戻るよう，背景の処理の標準入出力を全て閉じる（括弧で包まないと && の左側の出力が ssh の接続を握り続ける）
  ssh_retry "$HOST" "mkdir -p $REMOTE_DIR/b2 && rm -f $REMOTE_DIR/b2/$1.gpu.csv && \
    (setsid nohup bash -c 'echo \$\$ > $REMOTE_DIR/b2/$1.pid; while :; do echo \"\$(date +%s.%N),\$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_slowdown --format=csv,noheader,nounits | tr -d \" \")\"; sleep 1; done' > $REMOTE_DIR/b2/$1.gpu.csv 2> /dev/null < /dev/null &) > /dev/null 2>&1"
}
gpu_log_stop() { # gpu_log_stop <name> <out>
  ssh_retry "$HOST" "kill \$(cat $REMOTE_DIR/b2/$1.pid) 2> /dev/null; sleep 1; cat $REMOTE_DIR/b2/$1.gpu.csv" > "$2" 2> /dev/null
}
# 採点の server を dtype で起動し直す（dtype が今と同じなら何もしない）．Ollama の llama3.1:8b は score_server.sh が keep_alive 0 で降ろす
ensure_server() { # ensure_server <float32|float16>
  if [ "$CURRENT_DTYPE" = "$1" ]; then return 0; fi
  bash scripts/tasks/remote.sh score_server start "--dtype=$1" "$HOST_IP" >> "$LOG.start" 2>&1 < /dev/null
  exit_if_failed $? "server start ($1) FAILED (see $LOG.start)"
  CURRENT_DTYPE=$1
  say "scorer started on $HOST_IP dtype=$1"
}
# 起動中の server の /healthz を <out> に書く（dtype・attention の実装・tokenizer の最大長の記録）
save_healthz() { # save_healthz <out>
  mkdir -p "$(dirname "$1")"
  ssh_retry "$HOST" "curl -fsS -m 10 http://localhost:$SCORER_PORT/healthz" > "$1" 2>> "$LOG"
}
# 実機の条件を 1 回流す．RUN_NAME・WARMUP・MEASURE・EXTRA（bench の追加の引数の配列）を呼び出し側が設定する．
# 戻り値 0：有効な実行／1：無効（理由は valid.json）
run_bench() {
  local name=$RUN_NAME rdir="results/${BASE}_$RUN_NAME" cname="atrium-score-bench-${BASE}-$RUN_NAME" state t_end temp sw hw
  mkdir -p "$rdir"
  rm -f "$rdir/aborted.txt"
  if ! wait_cool "$rdir/cool_wait_s.txt"; then
    echo "did not cool to ${HOT_C} C within ${COOL_MAX_S}s" > "$rdir/aborted.txt"
    say "$name not run: host did not cool"
    uv run python $D/b2c_verdict.py check --run "$rdir" --host "$HOST" >> "$LOG" 2>&1
    return 1
  fi
  save_healthz "$rdir/healthz.json"
  snap "$HOST" "$rdir/snap_${HOST}_pre.txt"
  gpu_log_start "$name"
  say "$name BEGIN host=$HOST warmup=$WARMUP measure=$MEASURE ${EXTRA[*]:-}"
  # shellcheck disable=SC2086
  "${CONTROL_SSH[@]}" "mkdir -p $REMOTE_DIR/results/${BASE}_$name && docker rm -f $cname > /dev/null 2>&1; \
    docker run -d --name $cname --network host --user \$(id -u):\$(id -g) -e HOME=/tmp -v $DATA_DIR:/data:ro \
    -v $REMOTE_DIR/results:/results -v $REMOTE_DIR/config.yaml:/app/config.yaml:ro $IMAGE_FULL \
    atrium --config /app/config.yaml score-bench --data-dir /data --out-dir /results/${BASE}_$name \
    --run-results /results/d0009_a3_p1/results.jsonl --concurrency $CONCURRENCY --warmup-s $WARMUP --measure-s $MEASURE \
    --scorer http://$HOST_IP:$SCORER_PORT ${EXTRA[*]:-}" >> "$LOG" 2>&1
  t_end=$(($(date +%s) + ${WARMUP%.*} + ${MEASURE%.*} + BENCH_GRACE_S))
  # one-pass は終わるまでの時間が決まらないので，余裕を長く取る
  if printf '%s\n' "${EXTRA[@]:-}" | grep -q -- '--one-pass'; then t_end=$((t_end + ONE_PASS_EXTRA_WAIT_S)); fi
  while true; do
    state=$("${CONTROL_SSH[@]}" "docker inspect -f '{{.State.Running}}' $cname" 2> /dev/null || echo unknown)
    [ "$state" = false ] && break
    # 安全の打ち切り：83 °C 以上か SW Thermal Slowdown が Active
    read -r temp sw hw <<< "$(gpu_state "$HOST")"
    if [ "$temp" != "?" ] && { [ "$temp" -ge "$ABORT_C" ] || [ "$sw" = Active ]; }; then
      echo "$HOST temp=$temp sw=$sw" > "$rdir/aborted.txt"
    fi
    if [ ! -f "$rdir/aborted.txt" ] && [ "$(date +%s)" -ge "$t_end" ]; then echo "bench did not finish by the deadline" > "$rdir/aborted.txt"; fi
    if [ -f "$rdir/aborted.txt" ]; then
      "${CONTROL_SSH[@]}" "docker kill $cname > /dev/null 2>&1"
      say "$name ABORTED: $(cat "$rdir/aborted.txt")"
      break
    fi
    sleep "$WATCH_POLL_S"
  done
  sleep 2
  snap "$HOST" "$rdir/snap_${HOST}_post.txt"
  gpu_log_stop "$name" "$rdir/gpu_$HOST.csv"
  "${CONTROL_SSH[@]}" "docker logs $cname 2>&1 | tail -n 5; docker rm -f $cname > /dev/null 2>&1" > "$rdir/bench_container_tail.txt" 2>&1
  nrsync_results "$name"
  local smoke_flag=""
  [ "$name" = smoke ] && smoke_flag="--smoke"
  uv run python $D/b2c_verdict.py check --run "$rdir" --host "$HOST" $smoke_flag >> "$LOG" 2>&1
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

# G-ENV0：他の研究者の GPU のプロセスが 0（最大 2 時間待ち）・温度・Slowdown を確かめる
stage_env0() {
  local waited=0
  mkdir -p "$RESULT_DIR/env0"
  while true; do
    snap "$HOST" "$RESULT_DIR/env0/snap_${HOST}_pre.txt"
    [ "$(grep '^other_procs=' "$RESULT_DIR/env0/snap_${HOST}_pre.txt" | cut -d= -f2)" = 0 ] && break
    [ "$waited" -ge "$ENV0_MAX_WAIT_S" ] && break
    say "env0: other GPU processes on $HOST; waiting"
    sleep "$ENV0_POLL_S"
    waited=$((waited + ENV0_POLL_S))
  done
  [ "$(grep '^other_procs=' "$RESULT_DIR/env0/snap_${HOST}_pre.txt" | cut -d= -f2)" = 0 ]
  exit_if_failed $? "env0 NOT PASSED: other GPU processes on $HOST after ${waited}s"
  wait_cool "$RESULT_DIR/env0/cool_wait_$HOST.txt"
  exit_if_failed $? "env0 NOT PASSED: $HOST did not cool to ${HOT_C} C"
  echo "$HOST" > "$RESULT_DIR/host.txt"
  if [ "$HOST" = wafl502 ]; then : > "$RESULT_DIR/prod_prefix.txt"; else echo "w${HOST#wafl}_" > "$RESULT_DIR/prod_prefix.txt"; fi
  say "env0 passed on $HOST (waited ${waited}s): $(tr '\n' ' ' < "$RESULT_DIR/env0/snap_${HOST}_pre.txt")"
}

stage_f32pass() {
  local rdir="results/${BASE}_f32pass"
  if [ -f "$rdir/gf32.json" ]; then say "f32pass skip (exists)"; return; fi
  ensure_server float32
  RUN_NAME=f32pass WARMUP=0 MEASURE=0 EXTRA=(--one-pass --dump-scores)
  run_bench
  exit_if_failed $? "G-F32 run INVALID: $(tr -d '\n' < "$rdir/valid.json" 2> /dev/null)"
  # G-AT の材料：fp16 の 1 周の記録の場所へ fp32 の healthz を写す
  mkdir -p "results/${BASE}_f16pass"
  cp "$rdir/healthz.json" "results/${BASE}_f16pass/healthz_float32.json"
  uv run python $D/b2c_verdict.py gf32 --run "$rdir" >> "$LOG" 2>&1
  exit_if_failed $? "G-F32 NOT PASSED (see $rdir/gf32.json); production is not run"
  say "G-F32 passed"
}

stage_f16pass() {
  local rdir="results/${BASE}_f16pass"
  if [ -f "$RESULT_DIR/fp16_agreement.json" ]; then say "f16pass skip (exists)"; return; fi
  ensure_server float16
  RUN_NAME=f16pass WARMUP=0 MEASURE=0 EXTRA=(--one-pass --dump-scores)
  run_bench
  exit_if_failed $? "fp16 one-pass run INVALID: $(tr -d '\n' < "$rdir/valid.json" 2> /dev/null)"
  cp "$rdir/healthz.json" "$rdir/healthz_float16.json"
  uv run python $D/b2c_verdict.py at >> "$LOG" 2>&1
  exit_if_failed $? "G-AT NOT PASSED (see $RESULT_DIR/gat.json); production is not run"
  say "G-AT passed"
  uv run python $D/b2c_verdict.py agree >> "$LOG" 2>&1
  exit_if_failed $? "agreement computation FAILED"
  say "H-F1 numbers written to $RESULT_DIR/fp16_agreement.json"
}

stage_smoke() {
  if [ -f "results/${BASE}_smoke/valid.json" ] && [ "$(jq .valid "results/${BASE}_smoke/valid.json")" = true ]; then say "smoke skip (valid exists)"; return; fi
  ensure_server float16
  RUN_NAME=smoke WARMUP=10 MEASURE=30 EXTRA=()
  run_bench
  exit_if_failed $? "smoke INVALID: $(tr -d '\n' < "results/${BASE}_smoke/valid.json" 2> /dev/null)"
  say "smoke ok"
}

BAD_COUNT=0
# 本番の 1 実行．機が 2 回無効になっていれば流さない（事前登録）
prod_one() { # prod_one <round> <dtype> <suffix>
  local rnd=$1 dtype=$2 suffix=$3 name prefix
  prefix=$(tr -d '\n' < "$RESULT_DIR/prod_prefix.txt" 2> /dev/null)
  name="${prefix}r${rnd}_$([ "$dtype" = float32 ] && echo f32 || echo f16)$suffix"
  if [ -f "results/${BASE}_$name/valid.json" ]; then return "$([ "$(jq .valid "results/${BASE}_$name/valid.json")" = true ] && echo 0 || echo 1)"; fi
  if [ "$BAD_COUNT" -ge "$MAX_INVALID_PER_HOST" ]; then
    say "$name skipped: $HOST was invalid $BAD_COUNT times (rerun on wafl503 per the pre-registered rule: HOST=wafl503 ... env0 prod stop)"
    return 2
  fi
  ensure_server "$dtype"
  RUN_NAME=$name WARMUP=$PROD_WARMUP MEASURE=$PROD_MEASURE EXTRA=()
  run_bench
  if [ "$?" -ne 0 ]; then
    BAD_COUNT=$((BAD_COUNT + 1))
    say "$name INVALID: $(jq -r '.reasons[]' "results/${BASE}_$name/valid.json" 2> /dev/null | tr '\n' ';')"
    return 1
  fi
  say "$name valid"
  return 0
}

stage_prod() {
  local rnd=0 line dtype retry
  while read -r line; do
    rnd=$((rnd + 1))
    retry=()
    for dtype in $line; do
      prod_one "$rnd" "$dtype" ""
      [ "$?" -eq 1 ] && retry+=("$dtype")
    done
    for dtype in "${retry[@]:-}"; do
      [ -n "$dtype" ] && prod_one "$rnd" "$dtype" "_retry"
    done
  done < <(uv run python $D/b2c_verdict.py orders)
  if [ "$BAD_COUNT" -ge "$MAX_INVALID_PER_HOST" ]; then say "prod stopped: $HOST invalid $BAD_COUNT times"; return; fi
  uv run python $D/b2c_verdict.py summary >> "$LOG" 2>&1
  say "prod done; summary written to $RESULT_DIR"
}

stage_stop() {
  bash scripts/tasks/remote.sh score_server stop "$HOST_IP" >> "$LOG.stop" 2>&1 < /dev/null
  mkdir -p "$RESULT_DIR/after_stop"
  snap "$HOST" "$RESULT_DIR/after_stop/snap_${HOST}_post.txt"
  say "scorer stopped; post-stop snapshot in $RESULT_DIR/after_stop"
}

say "BEGIN stages: ${STAGES[*]} host=$HOST (git $(git rev-parse --short=12 HEAD))"
for stage in "${STAGES[@]}"; do
  "stage_$stage"
done
say "ALL DONE"
