#!/usr/bin/env bash
# 制御点での実験実行．結果を制御点の results/<run_id>/ に集める．
#
# 使い方（操作端末の `mise run start` から呼ばれる）:
#   bash scripts/remote/start.sh <run_id>
#
# e1_routing: 質問者でコンテナ atrium-run-<run_id> を起動し，終わるまで進み具合を表示して待つ．
#   SSH が切れてもコンテナは動き続ける．同じ run_id で再度実行すると，動いているコンテナを待ち直す．
# e0_measure: 対象ホストで メモリ構成・ストレージ・llama-bench・FAISS・MedCPT・RTT・iperf3 を測る．
source "$(dirname "$0")/lib.sh"

RUN_ID=${1:?usage: start.sh <run_id>}
OUT="results/$RUN_ID"
POLL_INTERVAL_S=60
LLAMA_CPP_IMAGE=ghcr.io/ggml-org/llama.cpp:full
IPERF_IMAGE=networkstatic/iperf3
# llama-bench のスレッド数（i5-8250U / 8350U の物理コア数）
LLAMA_THREADS=4
mkdir -p "$OUT"

# ── E0 ───────────────────────────────────────────────────────────────────────
measure_e0_host() {
  local host=$1
  local dir="$OUT/e0/$host"
  local rdir="$REMOTE_DIR/results/$RUN_ID"
  mkdir -p "$dir"
  nssh "$host" "sudo -n dmidecode -t memory" \
    | awk -F': ' '/^\tSize:/ && $2 !~ /No Module/ {s=$2} /^\tLocator:/ {l=$2} /^\tConfigured Memory Speed:/ && s {print l" "s" @"$2; s=""}' \
    > "$dir/dimm.txt"
  nssh "$host" "free -b | awk '/^Mem:/ {print \$2}'; df -B1 --output=avail / | tail -1; lsblk -d -o NAME,SIZE,ROTA,MODEL" > "$dir/host.raw"
  awk 'NR==1 {m=$1} NR==2 {a=$1} END {printf "{\"mem_total_gb\": %.1f, \"root_avail_gb\": %.1f}\n", m/1e9, a/1e9}' "$dir/host.raw" > "$dir/host.json"
  for spec in $E0_GGUF; do
    IFS='|' read -r name _ file <<< "$spec"
    nssh "$host" "docker run --rm -v $REMOTE_DIR/gguf:/models:ro --entrypoint /app/llama-bench $LLAMA_CPP_IMAGE \
      -m /models/$file -p 512,4096 -n 128 -t $LLAMA_THREADS -o json" > "$dir/llama-bench-$name.json"
  done
  nssh "$host" "mkdir -p $rdir && docker run --rm --user $HOST_UID:$HOST_GID -e HOME=/tmp -e HF_HOME=/cache \
    -v $REMOTE_DIR/config.yaml:/app/config.yaml:ro -v $REMOTE_DIR/hf-cache:/cache -v $rdir:/out $IMAGE_FULL \
    sh -c 'atrium --config /app/config.yaml e0 faiss --out /out/faiss.json && atrium --config /app/config.yaml e0 medcpt --out /out/medcpt.json'"
  nrsync -a "$SSH_USER@$host:$rdir/faiss.json" "$SSH_USER@$host:$rdir/medcpt.json" "$dir/"
}

measure_pair() {
  local a=$1 b=$2
  local out="$OUT/e0/net/${a}_${b}.json"
  mkdir -p "$OUT/e0/net"
  nssh "$b" "docker rm -f atrium-iperf >/dev/null 2>&1; docker run -d --rm --name atrium-iperf -p 5201:5201 $IPERF_IMAGE -s" > /dev/null
  sleep 2
  local rtt mbps
  rtt=$(nssh "$a" "ping -c 20 -q $b" | awk -F'/' '/^rtt|^round-trip/ {print $5}')
  mbps=$(nssh "$a" "docker run --rm $IPERF_IMAGE -c $b -t 10 -J" | jq '.end.sum_received.bits_per_second / 1e6 | floor')
  nssh "$b" "docker stop atrium-iperf" > /dev/null
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
    measure_pair "${pair%,*}" "${pair#*,}"
  done
  log "e0 done: $OUT"
  exit 0
fi

# ── E1 以降 ────────────────────────────────────────────────────────────────
NAME="atrium-run-$RUN_ID"
RDIR="$REMOTE_DIR/results/$RUN_ID"
if ! nssh "$REQUESTER" "docker ps -aq -f name=^$NAME\$" | grep -q .; then
  log "starting $NAME on $REQUESTER"
  nssh "$REQUESTER" "cd $REMOTE_DIR && docker compose --profile run run -d --name $NAME \
    -e ATRIUM_GIT_HEAD=$GIT_HEAD requester atrium --config /app/config.yaml run \
    --data-dir /data --placement /app/placement.json --out-dir /app/results/$RUN_ID \
    --ollama-url http://ollama:11434" > /dev/null
fi

while [ "$(nssh "$REQUESTER" "docker inspect -f '{{.State.Running}}' $NAME")" = "true" ]; do
  done_count=$(nssh "$REQUESTER" "wc -l < $RDIR/results.jsonl 2>/dev/null || echo 0")
  log "running: $done_count questions done"
  sleep "$POLL_INTERVAL_S"
done
EXIT_CODE=$(nssh "$REQUESTER" "docker inspect -f '{{.State.ExitCode}}' $NAME")
nssh "$REQUESTER" "docker logs $NAME" > "$OUT/requester.log" 2>&1 || true
nssh "$REQUESTER" "docker rm $NAME" > /dev/null
nrsync -a "$SSH_USER@$REQUESTER:$RDIR/" "$OUT/"

# 終了コード 2 は「一部の質問が失敗した」（結果は残っている）．それ以外の非 0 は実験自体の失敗
case "$EXIT_CODE" in
  0) log "finished: $OUT" ;;
  2) log "finished with failed questions (see error fields in $OUT/results.jsonl)" ;;
  *) log "requester exited with $EXIT_CODE (see $OUT/requester.log)" >&2; exit 1 ;;
esac
