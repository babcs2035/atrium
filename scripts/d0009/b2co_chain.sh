#!/usr/bin/env bash
# d0009 B2（Iteration 40）：cross-encoder の採点（fp16・最大長 1,024・batch 4）を，llama3.1:8b を載せたままの同じ GPU に同居させたときの可否と速さを
# 実機（wafl502 の 1 枚）で流す連続実行．
#   (nohup setsid sg docker -c "bash scripts/d0009/b2co_chain.sh results/d0009_b2_colocated_scoring/chain.log" > /dev/null 2>&1 < /dev/null &)
#   引数 2 個目以降に段階（publish env0 ggen s16 co smoke prod verdict stop）を並べると，その順に流す（省略時は publish 以外の全部）．
#   環境変数 HOST（既定 wafl502）で使う機を替える．wafl503 での本番の流し直しは `HOST=wafl503 ... env0 ggen co prod stop`（実行名に w503_ が付く）．
# 段階：
#   publish  atrium-full を build して制御点の registry へ push し，制御点で pull する（colocate-bench を載せる）
#   env0     G-ENV0：他の研究者の GPU のプロセスが 0（最大 2 時間待つ）・温度 ≤ 60 °C・SW/HW Slowdown が Not Active．機を host.txt へ書く
#   ggen     G-GEN：生成だけ．llama3.1:8b を num_ctx 16,384 で読み込み，/api/ps で 100% GPU を確かめ，864 候補の先頭 30 件を C_g = 2 で送る
#   s16      G-S16：採点だけ．llama3.1:8b を降ろし，fp16・1,024 の server で 288 問を 1 周（--one-pass --dump-scores）して Iteration 39 と得点を比べる
#   co       G-CO：採点の server を先に起動してから llama3.1:8b を読み込み（同居），生成を流しながら採点を 1 周して可否と得点の一致を見る
#            （載せられなければ本番を流さず，verdict を書いて止まる）
#   smoke    両方（B）．慣らし 10 秒・区間 30 秒
#   prod     本番：3 ラウンド．各ラウンドで G・S・B を 1 回ずつ（順は種 40）．慣らし 60 秒 + 区間 600 秒．無効な実行は同じラウンドの最後に 1 回だけ流し直す
#   verdict  b2co_verdict.py summary（prod の最後にも流す）
#   stop     採点の server と生成の container を始めの状態へ戻し，後の nvidia-smi を残す
# 実行の状態（事前登録）：G＝採点の container を止めて llama3.1:8b だけを載せる／S＝llama3.1:8b を降ろして採点の server だけを載せる／
#   B＝llama3.1:8b を降ろして採点の server を（--keep-generator で）起動し直し，その後で llama3.1:8b を num_ctx 16,384・keep_alive -1 で読み込む．
# 各実行の前に機の温度が ≤ 60 °C・SW Thermal Slowdown が Not Active になるのを最大 15 分待ち，実行中は 5 秒ごとに温度と Slowdown を見て，
# 83 °C 以上か SW Thermal Slowdown が Active になったら bench を止めて無効にする（B120・B122・B123 の熱の運用）．取得・probe は流さない．
# results/d0009_b2_co_<name>/ に実行ごとの記録（bench の出力・前後の nvidia-smi・1 秒ごとの GPU の記録・前後の /api/ps・oom.json・healthz.json・valid.json）を残す．
# 実行の間は commit しない．機が 2 回無効になったら本番を止める（機を wafl503 に替えた流し直しは事前登録の規則．backlog に記録する）．
source "$(dirname "${BASH_SOURCE[0]}")/../tasks/lib.sh"
set +e
set -u
cd "$REPO_ROOT" || exit 1
LOG=$1
shift
D=scripts/d0009
BASE=d0009_b2_co
RESULT_DIR=results/d0009_b2_colocated_scoring
HOST=${HOST:-wafl502}
SCORER_PORT=8200
NODE_PORT=8100
GEN_MODEL=llama3.1:8b
GEN_NUM_CTX=16384
# 制御点の registry から見た image（scripts/remote/lib.sh の IMAGE_FULL と同じ．採点の server と colocate-bench のコードを含む）
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
UP_WAIT_S=300
UNLOAD_WAIT_S=120
CONCURRENCY=30
GEN_CONCURRENCY=2
GEN_LIMIT=30
PROD_WARMUP=60
PROD_MEASURE=600
MAX_INVALID_PER_HOST=2
SSH_B=(-o BatchMode=yes -o ConnectTimeout=15)
CONTROL_SSH=(ssh -n "${SSH_B[@]}" "$CONTROL")
STAGES=(env0 ggen s16 co smoke prod stop)
if [ "$#" -gt 0 ]; then STAGES=("$@"); fi
mkdir -p "$RESULT_DIR"
say() { echo "[$(date +%F_%T)] $*" >> "$LOG"; }
ip_of() { echo "192.168.15.10${1#wafl50}"; }
HOST_IP=$(ip_of "$HOST")
SCORER_UP=unknown
STOPPED=false
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
    ssh "${SSH_B[@]}" "$1" bash -s < $D/b2co_gpu_snapshot.sh > "$2" 2> /dev/null && grep -q '^other_procs=' "$2" && return 0
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
exit_if_failed() { # exit_if_failed <rc> <message>
  if [ "$1" -ne 0 ]; then say "$2"; exit 1; fi
}

# ── 生成の container（Ollama・node）と採点の server の操作 ──────────────────────────────
# GPU の PC の自分たちの Ollama の container の IP へ curl する（Ollama の port は外へ出ていない）．戻り値 3：Ollama の container が動いていない
# （ssh -n は標準入力を閉じるので，here-doc で渡すこの関数では -n を付けない）
ollama_curl() { # ollama_curl <path> [<json>]
  local path=$1 data=${2:-} i rc
  for i in 1 2 3; do
    ssh "${SSH_B[@]}" "$HOST" "bash -s -- $(printf '%q %q' "$path" "$data")" << 'REMOTE'
ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' atrium-ollama-1 2> /dev/null)
# 止まっている container の IP は "invalid IP" と出るので，数字とドットだけのときに限る
case "$ip" in ""|*[!0-9.]*) exit 3 ;; esac
if [ -n "$2" ]; then curl -fsS -m 900 "http://$ip:11434$1" -d "$2"; else curl -fsS -m 30 "http://$ip:11434$1"; fi
REMOTE
    rc=$?
    [ "$rc" -ne 255 ] && return "$rc"
    sleep 5
  done
  return 255
}
container_running() { # container_running <name>
  [ "$(ssh_retry "$HOST" "docker inspect -f '{{.State.Running}}' $1 2> /dev/null" 2> /dev/null)" = true ]
}
# 生成の container（Ollama・node）を動かす．始めの状態（動いていたか・llama3.1:8b が載っていたか）を 1 度だけ記録する（stop で戻す）
gen_up() {
  if [ ! -f "$RESULT_DIR/gen_initial_state.txt" ]; then
    local running=no loaded=no
    container_running atrium-ollama-1 && running=yes
    if [ "$running" = yes ] && ollama_curl /api/ps 2> /dev/null | jq -e '.models | length > 0' > /dev/null 2>&1; then loaded=yes; fi
    echo "ollama_running=$running model_loaded=$loaded" > "$RESULT_DIR/gen_initial_state.txt"
  fi
  local waited=0
  ssh_retry "$HOST" "docker start atrium-ollama-1 atrium-node-1 > /dev/null"
  exit_if_failed $? "docker start of the generation containers FAILED on $HOST"
  until ssh_retry "$HOST" "curl -fsS -m 5 http://localhost:$NODE_PORT/healthz" > /dev/null 2>&1 && ollama_curl /api/ps > /dev/null 2>&1; do
    sleep 5
    waited=$((waited + 5))
    [ "$waited" -ge "$UP_WAIT_S" ] && { say "generation containers did not become healthy on $HOST"; exit 1; }
  done
}
# llama3.1:8b を降ろし，/api/ps が空になるまで待つ
model_unload() {
  local waited=0
  ollama_curl /api/generate "{\"model\":\"$GEN_MODEL\",\"keep_alive\":0}" > /dev/null 2>> "$LOG"
  until [ "$(ollama_curl /api/ps 2> /dev/null | jq '.models | length')" = 0 ]; do
    sleep 3
    waited=$((waited + 3))
    [ "$waited" -ge "$UNLOAD_WAIT_S" ] && { say "model did not unload within ${UNLOAD_WAIT_S}s"; exit 1; }
  done
}
# llama3.1:8b を主構成と同じ num_ctx で読み込み，keep_alive -1 で常駐させる
model_load() {
  ollama_curl /api/generate "{\"model\":\"$GEN_MODEL\",\"keep_alive\":-1,\"options\":{\"num_ctx\":$GEN_NUM_CTX}}" > /dev/null 2>> "$LOG"
  exit_if_failed $? "model load FAILED on $HOST"
  say "model loaded: $(ollama_curl /api/ps 2> /dev/null | jq -c '[.models[] | {name, size, size_vram, context_length}]')"
}
scorer_start() { # scorer_start <colocated|alone>
  local args=(--dtype=float16 --max-length=1024)
  [ "$1" = colocated ] && args+=(--keep-generator)
  bash scripts/tasks/remote.sh score_server start "${args[@]}" "$HOST_IP" >> "$LOG.start" 2>&1 < /dev/null
  exit_if_failed $? "scorer start ($1) FAILED (see $LOG.start)"
  SCORER_UP=yes
  say "scorer started on $HOST_IP fp16 1024 ($1)"
}
scorer_stop() {
  bash scripts/tasks/remote.sh score_server stop "$HOST_IP" >> "$LOG.stop" 2>&1 < /dev/null
  SCORER_UP=no
}
# 実行の状態を整える（事前登録の順）．G：採点を止め llama3.1:8b だけ／S：llama3.1:8b を降ろし採点だけ／B：採点の server を先に起動してから llama3.1:8b を読み込む
set_state() { # set_state <g|s|b>
  case "$1" in
    g)
      scorer_stop
      gen_up
      model_load
      ;;
    s)
      gen_up
      model_unload
      [ "$SCORER_UP" = yes ] || scorer_start alone
      ;;
    b)
      gen_up
      model_unload
      scorer_stop
      scorer_start colocated
      model_load
      ;;
  esac
}
# 起動中の server の /healthz を <out> に書く（dtype・attention の実装・効いている最大長の記録）
save_healthz() { # save_healthz <out>
  mkdir -p "$(dirname "$1")"
  ssh_retry "$HOST" "curl -fsS -m 10 http://localhost:$SCORER_PORT/healthz" > "$1" 2>> "$LOG"
}
# container のログの out of memory の行の数（container が無ければ 0）
oom_count() { # oom_count <container>
  local n
  n=$(ssh_retry "$HOST" "docker logs $1 2>&1 | grep -ci 'out of memory'; true" 2> /dev/null | head -n 1 | tr -dc '0-9')
  echo "${n:-0}"
}

# 実機の条件を 1 回流す．RUN_NAME・KIND（g|s|b）・WARMUP・MEASURE・EXTRA（bench の追加の引数の配列）を呼び出し側が設定する（状態は呼び出し側が set_state 済み）．
# 戻り値 0：有効な実行／1：無効（理由は valid.json）
run_bench() {
  local name=$RUN_NAME rdir="results/${BASE}_$RUN_NAME" cname="atrium-colocate-bench-${BASE}-$RUN_NAME" state t_end temp sw hw oom_ollama_pre mode_args=()
  mkdir -p "$rdir"
  rm -f "$rdir/aborted.txt"
  case "$KIND" in
    g) mode_args=(--mode gen --gen-url "http://$HOST_IP:$NODE_PORT" --gen-concurrency "$GEN_CONCURRENCY") ;;
    s) mode_args=(--mode score --scorer "http://$HOST_IP:$SCORER_PORT") ;;
    b) mode_args=(--mode both --scorer "http://$HOST_IP:$SCORER_PORT" --gen-url "http://$HOST_IP:$NODE_PORT" --gen-concurrency "$GEN_CONCURRENCY") ;;
  esac
  if ! wait_cool "$rdir/cool_wait_s.txt"; then
    echo "did not cool to ${HOT_C} C within ${COOL_MAX_S}s" > "$rdir/aborted.txt"
    say "$name not run: host did not cool"
    uv run python $D/b2co_verdict.py check --run "$rdir" --host "$HOST" --kind "$KIND" >> "$LOG" 2>&1
    return 1
  fi
  [ "$KIND" = g ] || save_healthz "$rdir/healthz.json"
  ollama_curl /api/ps > "$rdir/ps_pre.json" 2>> "$LOG"
  oom_ollama_pre=$(oom_count atrium-ollama-1)
  snap "$HOST" "$rdir/snap_${HOST}_pre.txt"
  gpu_log_start "$name"
  say "$name BEGIN host=$HOST kind=$KIND warmup=$WARMUP measure=$MEASURE ${EXTRA[*]:-}"
  # shellcheck disable=SC2086
  "${CONTROL_SSH[@]}" "mkdir -p $REMOTE_DIR/results/${BASE}_$name && docker rm -f $cname > /dev/null 2>&1; \
    docker run -d --name $cname --network host --user \$(id -u):\$(id -g) -e HOME=/tmp -v $DATA_DIR:/data:ro \
    -v $REMOTE_DIR/results:/results -v $REMOTE_DIR/config.yaml:/app/config.yaml:ro $IMAGE_FULL \
    atrium --config /app/config.yaml colocate-bench --data-dir /data --out-dir /results/${BASE}_$name \
    --run-results /results/d0009_a3_p1/results.jsonl --concurrency $CONCURRENCY --warmup-s $WARMUP --measure-s $MEASURE \
    ${mode_args[*]} ${EXTRA[*]:-}" >> "$LOG" 2>&1
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
  ollama_curl /api/ps > "$rdir/ps_post.json" 2>> "$LOG"
  echo "{\"scorer\": $(oom_count atrium-scorer), \"ollama\": $(($(oom_count atrium-ollama-1) - oom_ollama_pre))}" > "$rdir/oom.json"
  gpu_log_stop "$name" "$rdir/gpu_$HOST.csv"
  "${CONTROL_SSH[@]}" "docker logs $cname 2>&1 | tail -n 5; docker rm -f $cname > /dev/null 2>&1" > "$rdir/bench_container_tail.txt" 2>&1
  nrsync_results "$name"
  local smoke_flag=""
  [ "$name" = smoke ] && smoke_flag="--smoke"
  uv run python $D/b2co_verdict.py check --run "$rdir" --host "$HOST" --kind "$KIND" $smoke_flag >> "$LOG" 2>&1
}
nrsync_results() {
  local i
  for i in 1 2 3; do
    rsync -az -e "ssh ${SSH_B[*]}" "$CONTROL:$REMOTE_DIR/results/${BASE}_$1/" "results/${BASE}_$1/" && return 0
    sleep 5
  done
  return 1
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

stage_ggen() {
  local rdir="results/${BASE}_ggen"
  if [ -f "$rdir/ggen.json" ] && [ "$(jq .pass "$rdir/ggen.json")" = true ]; then say "ggen skip (G-GEN passed)"; return; fi
  set_state g
  mkdir -p "$rdir"
  ssh_retry "$HOST" "cat $REMOTE_DIR/config.yaml" > "$rdir/remote_config.yaml" 2>> "$LOG"
  RUN_NAME=ggen KIND=g WARMUP=0 MEASURE=0 EXTRA=(--gen-limit "$GEN_LIMIT")
  run_bench
  exit_if_failed $? "G-GEN run INVALID: $(tr -d '\n' < "$rdir/valid.json" 2> /dev/null)"
  uv run python $D/b2co_verdict.py ggen --run "$rdir" >> "$LOG" 2>&1
  exit_if_failed $? "G-GEN NOT PASSED (see $rdir/ggen.json); production is not run"
  say "G-GEN passed"
}

stage_s16() {
  local rdir="results/${BASE}_s16pass"
  if [ -f "$rdir/gf16.json" ] && [ "$(jq .pass "$rdir/gf16.json")" = true ]; then say "s16 skip (G-S16 passed)"; return; fi
  set_state s
  RUN_NAME=s16pass KIND=s WARMUP=0 MEASURE=0 EXTRA=(--one-pass --dump-scores)
  run_bench
  exit_if_failed $? "G-S16 run INVALID: $(tr -d '\n' < "$rdir/valid.json" 2> /dev/null)"
  uv run python $D/b2co_verdict.py gs16 --run "$rdir" >> "$LOG" 2>&1
  exit_if_failed $? "G-S16 NOT PASSED (see $rdir/gf16.json); production is not run"
  say "G-S16 passed"
}

stage_co() {
  local rdir="results/${BASE}_copass" rc
  if [ -f "$rdir/gco.json" ] && [ "$(jq .pass "$rdir/gco.json")" = true ]; then say "co skip (G-CO passed)"; return; fi
  set_state b
  RUN_NAME=copass KIND=b WARMUP=0 MEASURE=0 EXTRA=(--one-pass --dump-scores)
  run_bench
  exit_if_failed $? "G-CO run INVALID: $(tr -d '\n' < "$rdir/valid.json" 2> /dev/null)"
  uv run python $D/b2co_verdict.py gco --run "$rdir" >> "$LOG" 2>&1
  rc=$?
  if [ "$rc" -eq 2 ]; then
    # 載せられない：事前登録で本番を流さず，H-C0 を「載せられない」として閉じる
    say "G-CO NOT FIT (see $rdir/gco.json): H-C0 = cannot_fit; production is not run"
    uv run python $D/b2co_verdict.py summary >> "$LOG" 2>&1
    exit 1
  fi
  exit_if_failed "$rc" "G-CO scores did not match (see $rdir/gco.json); production is not run (undetermined)"
  say "G-CO passed"
}

stage_smoke() {
  if [ -f "results/${BASE}_smoke/valid.json" ] && [ "$(jq .valid "results/${BASE}_smoke/valid.json")" = true ]; then say "smoke skip (valid exists)"; return; fi
  set_state b
  RUN_NAME=smoke KIND=b WARMUP=10 MEASURE=30 EXTRA=()
  run_bench
  exit_if_failed $? "smoke INVALID: $(tr -d '\n' < "results/${BASE}_smoke/valid.json" 2> /dev/null)"
  say "smoke ok"
}

BAD_COUNT=0
# 本番の 1 実行．機が 2 回無効になっていれば流さない（事前登録）
prod_one() { # prod_one <round> <g|s|b> <suffix>
  local rnd=$1 kind=$2 suffix=$3 name prefix
  prefix=$(tr -d '\n' < "$RESULT_DIR/prod_prefix.txt" 2> /dev/null)
  name="${prefix}r${rnd}_${kind}$suffix"
  if [ -f "results/${BASE}_$name/valid.json" ]; then return "$([ "$(jq .valid "results/${BASE}_$name/valid.json")" = true ] && echo 0 || echo 1)"; fi
  if [ "$BAD_COUNT" -ge "$MAX_INVALID_PER_HOST" ]; then
    say "$name skipped: $HOST was invalid $BAD_COUNT times (rerun on wafl503 per the pre-registered rule: HOST=wafl503 ... env0 ggen co prod stop)"
    return 2
  fi
  set_state "$kind"
  RUN_NAME=$name KIND=$kind WARMUP=$PROD_WARMUP MEASURE=$PROD_MEASURE EXTRA=()
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
  local rnd=0 line kind retry
  while read -r line; do
    rnd=$((rnd + 1))
    retry=()
    for kind in $line; do
      prod_one "$rnd" "$kind" ""
      [ "$?" -eq 1 ] && retry+=("$kind")
    done
    for kind in "${retry[@]:-}"; do
      [ -n "$kind" ] && prod_one "$rnd" "$kind" "_retry"
    done
  done < <(uv run python $D/b2co_verdict.py orders)
  if [ "$BAD_COUNT" -ge "$MAX_INVALID_PER_HOST" ]; then say "prod stopped: $HOST invalid $BAD_COUNT times"; return; fi
  stage_verdict
  say "prod done; summary written to $RESULT_DIR"
}

stage_verdict() {
  uv run python $D/b2co_verdict.py summary >> "$LOG" 2>&1
}

# 採点の server と生成の container を始めの状態（gen_initial_state.txt）へ戻す：始めに止まっていれば止め，動いていたら llama3.1:8b を元の常駐へ戻す
stage_stop() {
  scorer_stop
  if [ -f "$RESULT_DIR/gen_initial_state.txt" ] && container_running atrium-ollama-1; then
    if grep -q 'ollama_running=no' "$RESULT_DIR/gen_initial_state.txt"; then
      model_unload
      ssh_retry "$HOST" "docker stop atrium-node-1 atrium-ollama-1 > /dev/null" >> "$LOG" 2>&1
    elif grep -q 'model_loaded=yes' "$RESULT_DIR/gen_initial_state.txt"; then
      model_load
    else
      model_unload
    fi
  fi
  mkdir -p "$RESULT_DIR/after_stop"
  snap "$HOST" "$RESULT_DIR/after_stop/snap_${HOST}_post.txt"
  STOPPED=true
  say "scorer and generation containers restored; post-stop snapshot in $RESULT_DIR/after_stop"
}

# 途中で止まったときも共有の GPU を空けて終わる（stop の段階を流していなければ流す）
cleanup() {
  if [ "$STOPPED" = false ] && [ -f "$RESULT_DIR/host.txt" ]; then
    set +e
    stage_stop
  fi
}
trap cleanup EXIT

say "BEGIN stages: ${STAGES[*]} host=$HOST (git $(git rev-parse --short=12 HEAD))"
for stage in "${STAGES[@]}"; do
  "stage_$stage"
done
say "ALL DONE"
