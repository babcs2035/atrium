#!/usr/bin/env bash
# 制御点での実験準備：配置の決定・シャードと設定の配布・コンテナの起動・起動確認．
#
# 使い方（操作端末の `mise run deploy` から呼ばれる）:
#   bash scripts/remote/deploy.sh
#
# experiment.kind=e1_routing のとき:
#   1. データ準備が終わっているか確かめる（manifest・labels・split，ragroute なら router）
#   2. manifest からシャードの配置（artifacts/<dataset>/placement.json）を決める
#   3. 各専門家の hugepages を解放し（cluster.release_hugepages），シャード（rsync -L で symlink の実体を送る）・
#      config・compose を配って起動する．local_answer なら Ollama のモデルも制御点から配る
#   4. 質問者へ質問・クエリ埋め込み・ルーター・配置・Ollama と HF のモデルを配り，Ollama を起動する
#   5. 全ノードの /healthz が応答するまで待つ
# experiment.kind=e0_measure のとき: 実測に使うイメージ・GGUF・MedCPT を対象ホストへ用意する．
# ノードはインターネットに出ず，イメージは registry，モデルとデータは制御点からの rsync で受け取る．
source "$(dirname "$0")/lib.sh"

DS_DIR=$(dataset_dir)
# 外部のイメージは registry のミラー（scripts/tasks/fetch_assets.sh が push する）から取得する
LLAMA_CPP_IMAGE="localhost:$REGISTRY_PORT/mirror/llama.cpp:full"
IPERF_IMAGE="localhost:$REGISTRY_PORT/mirror/iperf3:latest"
HEALTH_RETRIES=60
HEALTH_INTERVAL_S=10

stop_node_services() {
  local host=$1
  nssh "$host" "[ -f $REMOTE_DIR/compose.yml ] && cd $REMOTE_DIR && docker compose stop || true"
}

# 制御点でも最新のイメージを使う（配置の計算に atrium-full を使う）
docker pull -q "$IMAGE_FULL" > /dev/null

# ── E0 ───────────────────────────────────────────────────────────────────────
# iperf3 の相手になるだけのホストにも，イメージを用意する
deploy_iperf_peer() {
  local host=$1
  ensure_tunnel "$host"
  retry 3 nssh "$host" "docker pull -q $IPERF_IMAGE"
}

deploy_e0_host() {
  local host=$1
  # 実測中に専門家のコンテナが CPU とメモリを使わないよう止める
  stop_node_services "$host"
  release_hugepages "$host"
  ensure_tunnel "$host"
  retry 3 nssh "$host" "docker pull -q $IMAGE_FULL && docker pull -q $LLAMA_CPP_IMAGE && docker pull -q $IPERF_IMAGE"
  nssh "$host" "mkdir -p $REMOTE_DIR/gguf $REMOTE_DIR/hf-cache/hub"
  # GGUF と MedCPT のモデルは制御点から LAN で配る（ノードはインターネットに出ない）
  nrsync -a "$DATA_DIR/gguf/" "$SSH_USER@$host:$REMOTE_DIR/gguf/"
  nrsync -a "$DATA_DIR"/.cache/huggingface/hub/models--ncbi--MedCPT-* "$SSH_USER@$host:$REMOTE_DIR/hf-cache/hub/"
  nrsync -az config.yaml "$SSH_USER@$host:$REMOTE_DIR/config.yaml"
}

if [ "$KIND" = "e0_measure" ]; then
  # shellcheck disable=SC2086
  run_parallel deploy deploy_e0_host $E0_HOSTS
  peers=$(for pair in $E0_PAIRS; do echo "${pair%,*}"; echo "${pair#*,}"; done | sort -u)
  # shellcheck disable=SC2086
  run_parallel deploy deploy_iperf_peer $peers
  log "e0 deploy done"
  exit 0
fi

# ── E1 以降：データ準備の完了の確認 ────────────────────────────────────────
required=("$DS_DIR/manifest.json" "$DS_DIR/labels/labels.json" "$DS_DIR/labels/split.json")
if [ "$ROUTING" = "ragroute" ]; then required+=("$DS_DIR/router/router.pt"); fi
for f in "${required[@]}"; do
  if [ ! -e "$f" ]; then
    log "missing $f: data preparation has not finished (see: mise run data-status)" >&2
    exit 1
  fi
done

# ── 配置の決定 ──────────────────────────────────────────────────────────────
mkdir -p "artifacts/$DATASET"
docker run --rm --user "$HOST_UID:$HOST_GID" -v "$PWD:/work" -v "$DATA_DIR:/data:ro" -w /work \
  "$IMAGE_FULL" atrium --config config.yaml plan \
  --manifest "/data/$DATASET/manifest.json" --out "artifacts/$DATASET/placement.json" --tsv \
  > "artifacts/$DATASET/placement.tsv"
declare -A SHARDS_OF=()
while IFS=$'\t' read -r host shard_ids; do SHARDS_OF[$host]=$shard_ids; done < "artifacts/$DATASET/placement.tsv"
log "placement: ${#SHARDS_OF[@]} expert nodes"

deploy_expert() {
  local host=$1
  local shard_ids=${SHARDS_OF[$host]}
  release_hugepages "$host"
  ensure_tunnel "$host"
  # ./ollama を docker に自動で作らせると root の所有になり，モデルの rsync が書き込めなくなる
  nssh "$host" "mkdir -p $REMOTE_DIR/shards $REMOTE_DIR/ollama"
  own_remote_dir "$host"
  for sid in ${shard_ids//,/ }; do
    # 行の先頭位置のキャッシュはノード側で作るので，--delete の対象から外す
    nrsync -aL --delete --exclude '*.offsets.npy' "$DS_DIR/shards/$sid/" "$SSH_USER@$host:$REMOTE_DIR/shards/$sid/"
  done
  nrsync -az config.yaml "$SSH_USER@$host:$REMOTE_DIR/config.yaml"
  nrsync -az docker/compose.node.yml "$SSH_USER@$host:$REMOTE_DIR/compose.yml"
  # UID・GID はノード側で評価する（ノードの denjo の UID は制御点と同じとは限らない）
  nssh "$host" "printf 'REGISTRY_PORT=%s\nOLLAMA_TAG=%s\nNODE_PORT=%s\nNODE_ID=%s\nSHARD_IDS=%s\nHOST_UID=%s\nHOST_GID=%s\n' \
    $REGISTRY_PORT $OLLAMA_TAG $NODE_PORT $host $shard_ids \$(id -u) \$(id -g) > $REMOTE_DIR/.env"
  if [ "$ANSWER_MODE" = "local_answer" ]; then
    nrsync -a "$DATA_DIR/ollama/models" "$SSH_USER@$host:$REMOTE_DIR/ollama/"
  fi
  retry 3 nssh "$host" "cd $REMOTE_DIR && docker compose pull -q"
  nssh "$host" "cd $REMOTE_DIR && docker compose up -d --force-recreate"
}

deploy_requester() {
  local host=$1
  ensure_tunnel "$host"
  nssh "$host" "mkdir -p $REMOTE_DIR/data/$DATASET $REMOTE_DIR/results $REMOTE_DIR/hf-cache $REMOTE_DIR/ollama"
  own_remote_dir "$host"
  # 質問者が使うものだけを送る（シャードの本文と埋め込みは送らない）
  for item in benchmark manifest.json queries router qrels; do
    if [ -e "$DS_DIR/$item" ]; then
      nrsync -aL --delete "$DS_DIR/$item" "$SSH_USER@$host:$REMOTE_DIR/data/$DATASET/"
    fi
  done
  nrsync -az config.yaml "$SSH_USER@$host:$REMOTE_DIR/config.yaml"
  nrsync -az "artifacts/$DATASET/placement.json" "$SSH_USER@$host:$REMOTE_DIR/placement.json"
  nrsync -az docker/compose.requester.yml "$SSH_USER@$host:$REMOTE_DIR/compose.yml"
  nssh "$host" "printf 'REGISTRY_PORT=%s\nOLLAMA_TAG=%s\nREQUESTER_NUM_PARALLEL=%s\nHOST_UID=%s\nHOST_GID=%s\n' \
    $REGISTRY_PORT $OLLAMA_TAG $REQUESTER_NUM_PARALLEL \$(id -u) \$(id -g) > $REMOTE_DIR/.env"
  # 質問者のモデル（Ollama と，クエリ埋め込み・再ランクの HF のモデル）も制御点から配る
  nrsync -a "$DATA_DIR/ollama/models" "$SSH_USER@$host:$REMOTE_DIR/ollama/"
  nssh "$host" "mkdir -p $REMOTE_DIR/hf-cache/hub"
  nrsync -a "$DATA_DIR"/.cache/huggingface/hub/models--ncbi--MedCPT-* "$DATA_DIR"/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3 \
    "$SSH_USER@$host:$REMOTE_DIR/hf-cache/hub/"
  retry 3 nssh "$host" "cd $REMOTE_DIR && docker compose --profile run pull -q"
  nssh "$host" "cd $REMOTE_DIR && docker compose up -d --force-recreate ollama"
}

# 配置から外れた専門家は止める（前回の実験のシャードを抱えたままメモリを使い続けないように）
idle=()
for host in $EXPERTS; do
  if [ -z "${SHARDS_OF[$host]+x}" ]; then idle+=("$host"); fi
done

log "deploying experts"
run_parallel deploy deploy_expert "${!SHARDS_OF[@]}"
log "deploying requester $REQUESTER"
run_parallel deploy deploy_requester "$REQUESTER"
if [ "${#idle[@]}" -gt 0 ]; then
  log "stopping ${#idle[@]} unassigned experts"
  run_parallel deploy-stop stop_node_services "${idle[@]}" || true
fi

# ── 起動確認（PubMed のシャードは索引の組み立てに数分かかる）─────────────
wait_healthy() {
  local host=$1
  for _ in $(seq 1 "$HEALTH_RETRIES"); do
    if curl -fsS --max-time 5 "http://$host:$NODE_PORT/healthz"; then return 0; fi
    sleep "$HEALTH_INTERVAL_S"
  done
  nssh "$host" "cd $REMOTE_DIR && docker compose logs --tail 30 node"
  return 1
}
log "waiting for experts to become healthy"
run_parallel health wait_healthy "${!SHARDS_OF[@]}"
log "deploy done"
