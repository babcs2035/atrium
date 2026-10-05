#!/usr/bin/env bash
# 制御点での環境構築：registry の起動・イメージの build と push・データ準備の開始．
#
# 使い方（操作端末の `mise run setup` から呼ばれる）:
#   bash scripts/remote/setup.sh [dataset...]   # 省略時は feb4rag medrag の順に準備する
#
# データ準備（コーパスの取得・埋め込み・ラベル・ルーター学習）は数日かかりうるため，
# コンテナ atrium-data としてバックグラウンドで動かし，このスクリプトはすぐに戻る．
# 各段は冪等なので，途中で止まっても再度 setup を実行すれば続きから再開する．
# 進み具合は `mise run data-status` で確認する．
source "$(dirname "$0")/lib.sh"

DATASETS=${*:-"feb4rag medrag"}
REGISTRY_NAME=atrium-registry

# ── registry（制御点の 127.0.0.1 だけに公開し，各ノードからは SSH の逆トンネルで使う）
if docker ps -q -f "name=^${REGISTRY_NAME}$" | grep -q .; then
  log "registry already running"
elif docker ps -aq -f "name=^${REGISTRY_NAME}$" | grep -q .; then
  docker start "$REGISTRY_NAME"
else
  docker run -d --name "$REGISTRY_NAME" --restart=always -p "127.0.0.1:${REGISTRY_PORT}:5000" registry:2
fi

# ── イメージ
for target in node full; do
  image="localhost:$REGISTRY_PORT/atrium-$target:latest"
  log "building $image (git $GIT_HEAD)"
  docker build --target "$target" --build-arg GIT_HEAD="$GIT_HEAD" -t "$image" .
  docker push -q "$image"
done

# ── データ準備
mkdir -p "$DATA_DIR/logs" "$DATA_DIR/.cache/huggingface"
if docker ps -q -f "name=^atrium-data$" | grep -q .; then
  log "data preparation is already running (see: mise run data-status)"
  exit 0
fi
# 前回終了したコンテナ（ログは $DATA_DIR/logs に残してある）を片付けてから起動する
docker rm atrium-data > /dev/null 2>&1 || true
STEPS=""
for d in $DATASETS; do
  STEPS="$STEPS atrium --config /app/config.yaml data $d all --data-dir /data 2>&1 | tee -a /data/logs/data-$d.log;"
done
docker run -d --name atrium-data --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all \
  --user "$HOST_UID:$HOST_GID" -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface \
  -v "$DATA_DIR:/data" -v "$PWD/config.yaml:/app/config.yaml:ro" -w /data \
  "$IMAGE_FULL" bash -c "set -eo pipefail; $STEPS"
log "started data preparation for: $DATASETS (see: mise run data-status)"
