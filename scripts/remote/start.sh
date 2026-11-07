#!/usr/bin/env bash
# 制御点での実験実行．結果を制御点の results/<run_id>/ に集める．
#
# 使い方（操作端末の `mise run start` が setsid nohup で切り離して起動する）:
#   bash scripts/remote/start.sh <run_id> <git_head>   # 出力は results/<run_id>/start.log，終了コードは results/<run_id>/.exit
# git_head は操作端末のリポジトリのコミット（制御点には .git が無いため引数で受ける．質問者のイメージの照合と記録に使う）
#
# e1_routing: 質問者でコンテナ atrium-run-<run_id> を起動し，終わるまで進み具合を表示して待つ．
#   SSH が切れてもコンテナは動き続ける．同じ run_id で再度実行すると，動いているコンテナを待ち直す．
# e0_measure: 対象ホストで メモリ構成・ストレージ・llama-bench・FAISS・MedCPT・RTT・iperf3 を測る．
source "$(dirname "$0")/lib.sh"

RUN_ID=${1:?usage: start.sh <run_id> <git_head>}
GIT_HEAD=${2:?usage: start.sh <run_id> <git_head>}
OUT="results/$RUN_ID"
POLL_INTERVAL_S=60
LLAMA_CPP_IMAGE="localhost:$REGISTRY_PORT/mirror/llama.cpp:full"
IPERF_IMAGE="localhost:$REGISTRY_PORT/mirror/iperf3:latest"
IPERF_PORT=5202
# llama-bench のスレッド数（i5-8250U / 8350U の物理コア数）
LLAMA_THREADS=4
mkdir -p "$OUT"
# 終了コードを残す（操作端末の scripts/tasks/start.sh はこのファイルを待つ）
trap 'echo $? > "$OUT/.exit"' EXIT

# ── E0 ───────────────────────────────────────────────────────────────────────
# 1 つの計測が失敗しても（例: メモリ不足で 8B のモデルが載らない）残りの計測は続け，失敗は errors.txt に残す
try_step() {
  local dir=$1 label=$2
  shift 2
  if ! "$@"; then
    echo "$(date '+%F %T') failed: $label" >> "$dir/errors.txt"
    log "  failed: $label"
  fi
}

# try_step の if の中では set -e が効かないので，計測関数は各手順の失敗を明示的に返す
measure_memory() {
  local host=$1 dir=$2
  local dmi
  dmi=$(nssh "$host" "sudo -n dmidecode -t memory") || return 1
  awk -F': ' '/^\tSize:/ && $2 !~ /No Module/ {s=$2} /^\tLocator:/ {l=$2} /^\tConfigured Memory Speed:/ && s {print l" "s" @"$2; s=""}' \
    <<< "$dmi" > "$dir/dimm.txt"
  nssh "$host" "free -b | awk '/^Mem:/ {print \$2}'; df -B1 --output=avail / | tail -1; lsblk -d -o NAME,SIZE,ROTA,MODEL; grep -E 'MemAvailable' /proc/meminfo" > "$dir/host.raw" || return 1
  awk 'NR==1 {m=$1} NR==2 {a=$1} END {printf "{\"mem_total_gb\": %.1f, \"root_avail_gb\": %.1f}\n", m/1e9, a/1e9}' "$dir/host.raw" > "$dir/host.json"
}

measure_llama() {
  local host=$1 dir=$2 name=$3 file=$4
  # 同じ run_id でやり直すときは，完了済みの計測を飛ばす
  if jq -e 'length > 0' "$dir/llama-bench-$name.json" > /dev/null 2>&1; then return 0; fi
  nssh "$host" "docker run --rm -v $REMOTE_DIR/gguf:/models:ro --entrypoint /app/llama-bench $LLAMA_CPP_IMAGE \
    -m /models/$file -p 512,4096 -n 128 -t $LLAMA_THREADS -o json" > "$dir/llama-bench-$name.json"
}

measure_python() {
  local host=$1 dir=$2 what=$3
  local rdir="$REMOTE_DIR/results/$RUN_ID"
  if jq -e 'length > 0' "$dir/$what.json" > /dev/null 2>&1; then return 0; fi
  nssh "$host" "mkdir -p $rdir && docker run --rm --user \$(id -u):\$(id -g) -e HOME=/tmp -e HF_HOME=/cache -e HF_HUB_OFFLINE=1 \
    -v $REMOTE_DIR/config.yaml:/app/config.yaml:ro -v $REMOTE_DIR/hf-cache:/cache -v $rdir:/out $IMAGE_FULL \
    atrium --config /app/config.yaml e0 $what --out /out/$what.json" || return 1
  nrsync -a "$(ssh_dest "$host"):$rdir/$what.json" "$dir/"
}

measure_e0_host() {
  local host=$1
  local dir="$OUT/e0/$host"
  mkdir -p "$dir"
  try_step "$dir" memory measure_memory "$host" "$dir"
  for spec in $E0_GGUF; do
    IFS='|' read -r name _ file <<< "$spec"
    try_step "$dir" "llama-bench $name" measure_llama "$host" "$dir" "$name" "$file"
  done
  try_step "$dir" faiss measure_python "$host" "$dir" faiss
  try_step "$dir" medcpt measure_python "$host" "$dir" medcpt
}

measure_pair() {
  local a=$1 b=$2
  local out="$OUT/e0/net/${a}_${b}.json"
  mkdir -p "$OUT/e0/net"
  # 5201 番はホストの iperf3 のサービスが使っていることがあるので，計測用のサーバーは別の番号で公開する
  nssh "$b" "docker rm -f atrium-iperf >/dev/null 2>&1; docker run -d --rm --name atrium-iperf -p $IPERF_PORT:5201 $IPERF_IMAGE -s" > /dev/null || return 1
  sleep 2
  local rtt mbps
  rtt=$(nssh "$a" "ping -c 20 -q $b" | awk -F'/' '/^rtt|^round-trip/ {print $5}')
  mbps=$(nssh "$a" "docker run --rm $IPERF_IMAGE -c $b -p $IPERF_PORT -t 10 -J" | jq '.end.sum_received.bits_per_second / 1e6 | floor')
  nssh "$b" "docker stop atrium-iperf" > /dev/null || true
  if [ -z "$rtt" ] || [ -z "$mbps" ] || [ "$mbps" = null ]; then return 1; fi
  printf '{"rtt_avg_ms": %s, "throughput_mbps": %s}\n' "${rtt:-null}" "${mbps:-null}" > "$out"
}

if [ "$KIND" = "e0_measure" ]; then
  printf '{"run_id": "%s", "kind": "e0_measure", "git_head": "%s"}\n' "$RUN_ID" "$GIT_HEAD" > "$OUT/run_meta.json"
  # ホスト間で干渉しないよう，1 台ずつ順に測る
  for host in $E0_HOSTS; do
    log "measuring $host"
    measure_e0_host "$host"
  done
  for pair in $E0_PAIRS; do
    log "measuring network $pair"
    mkdir -p "$OUT/e0/net"
    try_step "$OUT/e0/net" "network $pair" measure_pair "${pair%,*}" "${pair#*,}"
  done
  log "e0 done: $OUT"
  exit 0
fi

# ── E1 以降 ────────────────────────────────────────────────────────────────
# 実験の前に，配った設定とイメージが今のものと同じかを確かめる（deploy が途中で失敗したまま古い設定や
# 古いイメージで実験が走るのを防ぐ．2026-10-06 に実際に起きた）
PLACEMENT_TSV="artifacts/$DATASET/placement.tsv"
local_sum=$(sha256sum < config.yaml)
for host in "$REQUESTER" $(cut -f1 "$PLACEMENT_TSV"); do
  remote_sum=$(nssh "$host" "sha256sum < $REMOTE_DIR/config.yaml" || echo missing)
  if [ "$local_sum" != "$remote_sum" ]; then
    log "config.yaml on $host differs from the current one; run 'mise run deploy' first" >&2
    exit 1
  fi
done
image_head=$(nssh "$REQUESTER" "docker run --rm --entrypoint cat $IMAGE_FULL /etc/atrium-git-head")
if [ "$image_head" != "$GIT_HEAD" ]; then
  log "requester image is built from $image_head, not $GIT_HEAD; run 'mise run deploy' first" >&2
  exit 1
fi
# 追加の質問者（cluster.extra_requesters．d0009 A1）は atrium-node-embed で動く．専門家の PC に置くので，config.yaml は専門家として
# 上の確認を通っている．イメージのコミットだけここで確かめる
IMAGE_EXTRA="localhost:$REGISTRY_PORT/atrium-node-embed:latest"
for host in $EXTRA_REQUESTERS; do
  extra_head=$(nssh "$host" "docker run --rm --entrypoint cat $IMAGE_EXTRA /etc/atrium-git-head")
  if [ "$extra_head" != "$GIT_HEAD" ]; then
    log "requester image on $host is built from $extra_head, not $GIT_HEAD; run 'mise run deploy' first" >&2
    exit 1
  fi
done
cp "artifacts/$DATASET/placement.json" "$OUT/placement.json"

if [ -z "$EXTRA_REQUESTERS" ]; then
  NAME="atrium-run-$RUN_ID"
  RDIR="$REMOTE_DIR/results/$RUN_ID"
  if ! nssh "$REQUESTER" "docker ps -aq -f name=^$NAME\$" | grep -q .; then
    log "starting $NAME on $REQUESTER"
    # LLM を別の GPU PC で動かすときは，質問者の側の Ollama を起動しない（--no-deps）
    deps_flag=""
    if [ -n "$REQUESTER_LLM" ]; then deps_flag="--no-deps"; fi
    nssh "$REQUESTER" "cd $REMOTE_DIR && docker compose --profile run run -d $deps_flag --name $NAME \
      requester atrium --config /app/config.yaml run \
      --data-dir /data --placement /app/placement.json --out-dir /app/results/$RUN_ID \
      --ollama-url $REQUESTER_OLLAMA_URL" > /dev/null
  fi

  # 待機中の SSH の失敗では止めない（数時間の実験を，1 回の接続の失敗で置き去りにしないため）．
  # 状態が false と確かめられたときだけ待機を抜け，失敗が続いたときだけ実験を残して止まる
  MAX_POLL_FAILURES=30
  poll_failures=0
  while true; do
    state=$(nssh "$REQUESTER" "docker inspect -f '{{.State.Running}}' $NAME" 2> /dev/null || echo unknown)
    case "$state" in
      true)
        poll_failures=0
        done_count=$(nssh "$REQUESTER" "wc -l < $RDIR/results.jsonl 2>/dev/null || echo 0" || echo "?")
        log "running: $done_count questions done"
        ;;
      false) break ;;
      *)
        poll_failures=$((poll_failures + 1))
        log "could not check $NAME ($poll_failures/$MAX_POLL_FAILURES)"
        if [ "$poll_failures" -ge "$MAX_POLL_FAILURES" ]; then
          log "giving up waiting; $NAME may still be running on $REQUESTER" >&2
          exit 1
        fi
        ;;
    esac
    sleep "$POLL_INTERVAL_S"
  done
  EXIT_CODE=$(retry 3 nssh "$REQUESTER" "docker inspect -f '{{.State.ExitCode}}' $NAME")
  nssh "$REQUESTER" "docker logs $NAME" > "$OUT/requester.log" 2>&1 || true
  nssh "$REQUESTER" "docker rm $NAME" > /dev/null || true
  # 質問者が制御点自身なら，結果は既に $OUT にある
  if ! is_control_host "$REQUESTER"; then
    retry 3 nrsync -a "$(ssh_dest "$REQUESTER"):$RDIR/" "$OUT/"
  fi
else
  # ── 複数の質問者（cluster.extra_requesters．d0009 A1）──────────────────────────
  # 質問者 i（0 が cluster.requester，1 以降が extra_requesters）が，並べた順の問の i mod N を同時の数の合計の 1/N で流す．
  # 各質問者は準備（シャードの発見・ルーターの読み込み）の後，全員が同じ時刻（START_AT）まで待ってから流し始める．
  # 結果は質問者ごとに results/<run_id>/req<i>/ へ書き，終わってから 1 つの実行（results.jsonl・run_meta.json）にまとめる．
  # 同じ run_id で再実行すると，各質問者の req<i>/ の成功済みの問を飛ばして失敗した問だけを流す．
  MAX_POLL_FAILURES=30
  START_DELAY_S=180
  FAULT_PLAN="$OUT/fault_plan.txt"
  read -r -a REQ_HOSTS <<< "$REQUESTER $EXTRA_REQUESTERS"
  N_REQ=${#REQ_HOSTS[@]}
  RDIR="$REMOTE_DIR/results/$RUN_ID"

  # req_name <i>: 質問者 i のコンテナ名
  req_name() { echo "atrium-run-$RUN_ID-r$1"; }

  # req_launch <i> <start_at>: 質問者 i のコンテナを起動する（動いている・残っているコンテナがあれば起動し直さず待ち直す）
  req_launch() {
    local i=$1 start_at=$2 host=${REQ_HOSTS[$1]} name
    name=$(req_name "$1")
    if nssh "$host" "docker ps -aq -f name=^$name\$" | grep -q .; then return 0; fi
    local args="atrium --config /app/config.yaml run --data-dir /data --placement /app/placement.json \
      --out-dir /app/results/$RUN_ID/req$i --ollama-url $REQUESTER_OLLAMA_URL \
      --requester-index $i --requester-count $N_REQ --requester-host $host --start-at $start_at"
    log "starting $name on $host"
    if [ "$i" = 0 ]; then
      local deps_flag=""
      if [ -n "$REQUESTER_LLM" ]; then deps_flag="--no-deps"; fi
      nssh "$host" "cd $REMOTE_DIR && mkdir -p $RDIR/req$i && docker compose --profile run run -d $deps_flag --name $name requester $args" > /dev/null
    else
      nssh "$host" "cd $REMOTE_DIR && mkdir -p $RDIR/req$i && docker compose -f compose.requester_extra.yml -p atrium-requester \
        --profile run run -d --name $name requester $args" > /dev/null
    fi
  }

  # 障害の記録（H-A1F．判定に使わない）．$FAULT_PLAN（"<止める秒> <戻す秒> <host>..."）があれば，START_AT から <止める秒> 後にそれらの
  # ホストの node のコンテナを止め，<戻す秒> 後に戻す（データ・image は消さない）．止めた時刻と戻した時刻は $OUT/fault_log.txt に残す．
  # 流し直しの start で二重に止めないよう，計画は読んだら fault_plan.used.txt へ移す．背景で動かし，終了の後始末でも必ず戻す
  FAULT_STOP_S="" FAULT_RESTORE_S="" FAULT_HOSTS=""
  if [ -f "$FAULT_PLAN" ]; then
    read -r FAULT_STOP_S FAULT_RESTORE_S FAULT_HOSTS < "$FAULT_PLAN"
    mv "$FAULT_PLAN" "$OUT/fault_plan.used.txt"
  fi
  fault_inject() {
    local start_at=$1 wait_s
    wait_s=$(( start_at + FAULT_STOP_S - $(date +%s) ))
    if [ "$wait_s" -gt 0 ]; then sleep "$wait_s"; fi
    for h in $FAULT_HOSTS; do
      nssh "$h" "cd $REMOTE_DIR && docker compose stop node" > /dev/null 2>&1 || true
      echo "$(date +%s.%N) stop $h" >> "$OUT/fault_log.txt"
    done
    sleep "$((FAULT_RESTORE_S - FAULT_STOP_S))"
    fault_restore
  }
  # 止めたノードを戻す（二重に呼んでも害は無い）
  fault_restore() {
    local h
    for h in $FAULT_HOSTS; do
      nssh "$h" "cd $REMOTE_DIR && docker compose start node" > /dev/null 2>&1 || log "FAILED to restore node on $h (run: cd $REMOTE_DIR && docker compose start node)"
      echo "$(date +%s.%N) start $h" >> "$OUT/fault_log.txt"
    done
  }

  START_AT=$(( $(date +%s) + START_DELAY_S ))
  for i in "${!REQ_HOSTS[@]}"; do req_launch "$i" "$START_AT"; done
  FAULT_PID=""
  if [ -n "$FAULT_HOSTS" ]; then
    trap 'rc=$?; fault_restore; echo $rc > "$OUT/.exit"' EXIT
    fault_inject "$START_AT" &
    FAULT_PID=$!
  fi

  # 待機中の SSH の失敗では止めない（単一の質問者と同じ）．全質問者のコンテナが止まったと確かめられたときだけ待機を抜ける
  declare -A POLL_FAILURES=()
  while true; do
    running=0
    for i in "${!REQ_HOSTS[@]}"; do
      host=${REQ_HOSTS[$i]}
      state=$(nssh "$host" "docker inspect -f '{{.State.Running}}' $(req_name "$i")" 2> /dev/null || echo unknown)
      case "$state" in
        true)
          running=$((running + 1))
          POLL_FAILURES[$i]=0
          done_count=$(nssh "$host" "wc -l < $RDIR/req$i/results.jsonl 2>/dev/null || echo 0" || echo "?")
          log "req$i ($host) running: $done_count questions done"
          ;;
        false) ;;
        *)
          running=$((running + 1))
          POLL_FAILURES[$i]=$(( ${POLL_FAILURES[$i]:-0} + 1 ))
          log "could not check $(req_name "$i") (${POLL_FAILURES[$i]}/$MAX_POLL_FAILURES)"
          if [ "${POLL_FAILURES[$i]}" -ge "$MAX_POLL_FAILURES" ]; then
            log "giving up waiting; $(req_name "$i") may still be running on $host" >&2
            exit 1
          fi
          ;;
      esac
    done
    if [ "$running" = 0 ]; then break; fi
    sleep "$POLL_INTERVAL_S"
  done
  if [ -n "$FAULT_PID" ]; then wait "$FAULT_PID" || true; fi

  # 各質問者の終了コードの最大（2 は一部の問の失敗，1 は実験自体の失敗）・ログ・結果を回収する
  EXIT_CODE=0
  for i in "${!REQ_HOSTS[@]}"; do
    host=${REQ_HOSTS[$i]}
    name=$(req_name "$i")
    code=$(retry 3 nssh "$host" "docker inspect -f '{{.State.ExitCode}}' $name")
    case "$code" in 0) ;; 2) [ "$EXIT_CODE" = 1 ] || EXIT_CODE=2 ;; *) EXIT_CODE=1 ;; esac
    mkdir -p "$OUT/req$i"
    nssh "$host" "docker logs $name" > "$OUT/req$i/requester.log" 2>&1 || true
    nssh "$host" "docker rm $name" > /dev/null || true
    if ! is_control_host "$host"; then
      retry 3 nrsync -a "$(ssh_dest "$host"):$RDIR/req$i/" "$OUT/req$i/"
    fi
  done
  docker run --rm --user "$HOST_UID:$HOST_GID" -v "$PWD:/work" -w /work "$IMAGE_FULL" \
    atrium --config config.yaml merge-requesters --run-dir "results/$RUN_ID"
fi
# 専門家のログはこの実行の直後に集める（後で deploy すると --force-recreate で消えるため）
mkdir -p "$OUT/logs"
while IFS=$'\t' read -r host _; do
  nssh "$host" "cd $REMOTE_DIR && docker compose logs --no-color node" > "$OUT/logs/$host.log" 2>&1 \
    || log "could not collect logs from $host"
done < "$PLACEMENT_TSV"

# 終了コード 2 は「一部の質問が失敗した」（結果は残っている）．それ以外の非 0 は実験自体の失敗
case "$EXIT_CODE" in
  0) log "finished: $OUT" ;;
  # 終了コード 2 を .exit に残すので，同じ run_id で `mise run start` し直すと，失敗した質問だけをやり直す
  2) log "finished with failed questions (see error fields in $OUT/results.jsonl)"; exit 2 ;;
  *) log "requester exited with $EXIT_CODE (see $OUT/requester.log)" >&2; exit 1 ;;
esac
