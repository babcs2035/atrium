#!/usr/bin/env bash
# 制御点での実験準備：配置の決定・シャードと設定の配布・コンテナの起動・起動確認．
#
# 使い方（操作端末の `mise run deploy` から呼ばれる）:
#   bash scripts/remote/deploy.sh
#
# experiment.kind=e1_routing のとき:
#   1. データ準備が終わっているか確かめる（manifest・labels・split，ragroute なら router）
#   2. manifest からシャードの配置（artifacts/<dataset>/placement.json）を決める
#   3. 各専門家へシャード（rsync -L で symlink の実体を送る）・config・compose を配り，起動する
#   4. 質問者へ質問・クエリ埋め込み・ルーター・配置を配り，Ollama を起動して LLM を取得する
#   5. 全ノードの /healthz が応答するまで待つ
# experiment.kind=e0_measure のとき: 実測に使うイメージ・GGUF を対象ホストへ用意する．
source "$(dirname "$0")/lib.sh"

DS_DIR=$(dataset_dir)
LLAMA_CPP_IMAGE=ghcr.io/ggml-org/llama.cpp:full
IPERF_IMAGE=networkstatic/iperf3
HEALTH_RETRIES=60
HEALTH_INTERVAL_S=10

stop_node_services() {
  local host=$1
  nssh "$host" "[ -f $REMOTE_DIR/compose.yml ] && cd $REMOTE_DIR && docker compose stop || true"
}

# ── E0 ───────────────────────────────────────────────────────────────────────
deploy_e0_host() {
  local host=$1
  # 実測中に専門家のコンテナが CPU とメモリを使わないよう止める
  stop_node_services "$host"
  ensure_tunnel "$host"
  nssh "$host" "docker pull -q $IMAGE_FULL && docker pull -q $LLAMA_CPP_IMAGE && docker pull -q $IPERF_IMAGE"
  nssh "$host" "mkdir -p $REMOTE_DIR/gguf $REMOTE_DIR/hf-cache"
  for spec in $E0_GGUF; do
    IFS='|' read -r _ repo file <<< "$spec"
    nssh "$host" "if [ ! -s $REMOTE_DIR/gguf/$file ]; then \
      curl -fL --retry 3 -o $REMOTE_DIR/gguf/$file.part https://huggingface.co/$repo/resolve/main/$file \
      && mv -f $REMOTE_DIR/gguf/$file.part $REMOTE_DIR/gguf/$file; fi"
  done
  nrsync -az config.yaml "$SSH_USER@$host:$REMOTE_DIR/config.yaml"
}

if [ "$KIND" = "e0_measure" ]; then
  # shellcheck disable=SC2086
  run_parallel deploy deploy_e0_host $E0_HOSTS
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
  ensure_tunnel "$host"
  nssh "$host" "mkdir -p $REMOTE_DIR/shards"
  for sid in ${shard_ids//,/ }; do
    # 行の先頭位置のキャッシュはノード側で作るので，--delete の対象から外す
    nrsync -aL --delete --exclude '*.offsets.npy' "$DS_DIR/shards/$sid/" "$SSH_USER@$host:$REMOTE_DIR/shards/$sid/"
  done
  nrsync -az config.yaml "$SSH_USER@$host:$REMOTE_DIR/config.yaml"
  nrsync -az docker/compose.node.yml "$SSH_USER@$host:$REMOTE_DIR/compose.yml"
  # UID・GID はノード側で評価する（ノードの denjo の UID は制御点と同じとは限らない）
  nssh "$host" "printf 'REGISTRY_PORT=%s\nNODE_PORT=%s\nNODE_ID=%s\nSHARD_IDS=%s\nHOST_UID=%s\nHOST_GID=%s\n' \
    $REGISTRY_PORT $NODE_PORT $host $shard_ids \$(id -u) \$(id -g) > $REMOTE_DIR/.env"
  nssh "$host" "cd $REMOTE_DIR && docker compose pull -q && docker compose up -d --force-recreate"
  if [ "$ANSWER_MODE" = "local_answer" ]; then
    nssh "$host" "cd $REMOTE_DIR && docker compose exec -T ollama ollama pull $EXPERT_MODEL"
  fi
}

deploy_requester() {
  local host=$1
  ensure_tunnel "$host"
  nssh "$host" "mkdir -p $REMOTE_DIR/data/$DATASET $REMOTE_DIR/results $REMOTE_DIR/hf-cache"
  # 質問者が使うものだけを送る（シャードの本文と埋め込みは送らない）
  for item in benchmark manifest.json queries router qrels; do
    if [ -e "$DS_DIR/$item" ]; then
      nrsync -aL --delete "$DS_DIR/$item" "$SSH_USER@$host:$REMOTE_DIR/data/$DATASET/"
    fi
  done
  nrsync -az config.yaml "$SSH_USER@$host:$REMOTE_DIR/config.yaml"
  nrsync -az "artifacts/$DATASET/placement.json" "$SSH_USER@$host:$REMOTE_DIR/placement.json"
  nrsync -az docker/compose.requester.yml "$SSH_USER@$host:$REMOTE_DIR/compose.yml"
  nssh "$host" "printf 'REGISTRY_PORT=%s\nHOST_UID=%s\nHOST_GID=%s\n' $REGISTRY_PORT \$(id -u) \$(id -g) > $REMOTE_DIR/.env"
  nssh "$host" "cd $REMOTE_DIR && docker compose --profile run pull -q && docker compose up -d ollama"
  if [ "$ANSWER_MODE" = "snippet_return" ]; then
    nssh "$host" "cd $REMOTE_DIR && docker compose exec -T ollama ollama pull $REQUESTER_MODEL"
  fi
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
