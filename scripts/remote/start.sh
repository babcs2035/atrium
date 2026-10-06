#!/usr/bin/env bash
# 制御点での実験実行．結果を制御点の results/<run_id>/ に集める．
#
# 使い方（操作端末の `mise run start` が setsid nohup で切り離して起動する）:
#   bash scripts/remote/start.sh <run_id>     # 出力は results/<run_id>/start.log，終了コードは results/<run_id>/.exit
#
# e1_routing: 質問者でコンテナ atrium-run-<run_id> を起動し，終わるまで進み具合を表示して待つ．
#   SSH が切れてもコンテナは動き続ける．同じ run_id で再度実行すると，動いているコンテナを待ち直す．
# e0_measure: 対象ホストで メモリ構成・ストレージ・llama-bench・FAISS・MedCPT・RTT・iperf3 を測る．
source "$(dirname "$0")/lib.sh"

RUN_ID=${1:?usage: start.sh <run_id>}
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
image_head=$(nssh "$REQUESTER" "docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' $IMAGE_FULL" \
  | sed -n 's/^ATRIUM_GIT_HEAD=//p')
if [ "$image_head" != "$GIT_HEAD" ]; then
  log "requester image is built from $image_head, not $GIT_HEAD; run 'mise run deploy' first" >&2
  exit 1
fi
cp "artifacts/$DATASET/placement.json" "$OUT/placement.json"

NAME="atrium-run-$RUN_ID"
RDIR="$REMOTE_DIR/results/$RUN_ID"
if ! nssh "$REQUESTER" "docker ps -aq -f name=^$NAME\$" | grep -q .; then
  log "starting $NAME on $REQUESTER"
  nssh "$REQUESTER" "cd $REMOTE_DIR && docker compose --profile run run -d --name $NAME \
    requester atrium --config /app/config.yaml run \
    --data-dir /data --placement /app/placement.json --out-dir /app/results/$RUN_ID \
    --ollama-url http://ollama:11434" > /dev/null
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
retry 3 nrsync -a "$(ssh_dest "$REQUESTER"):$RDIR/" "$OUT/"
# 専門家のログはこの実行の直後に集める（後で deploy すると --force-recreate で消えるため）
mkdir -p "$OUT/logs"
while IFS=$'\t' read -r host _; do
  nssh "$host" "cd $REMOTE_DIR && docker compose logs --no-color node" > "$OUT/logs/$host.log" 2>&1 \
    || log "could not collect logs from $host"
done < "$PLACEMENT_TSV"

# 終了コード 2 は「一部の質問が失敗した」（結果は残っている）．それ以外の非 0 は実験自体の失敗
case "$EXIT_CODE" in
  0) log "finished: $OUT" ;;
  2) log "finished with failed questions (see error fields in $OUT/results.jsonl)" ;;
  *) log "requester exited with $EXIT_CODE (see $OUT/requester.log)" >&2; exit 1 ;;
esac
