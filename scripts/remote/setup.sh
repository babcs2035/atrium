#!/usr/bin/env bash
# 制御点での環境構築（操作端末の `mise run setup` から呼ばれる）．
#
#   bash scripts/remote/setup.sh registry          # registry を起動する（イメージの push の前に呼ぶ）
#   bash scripts/remote/setup.sh start [dataset...] # push されたイメージを取得し，データ準備を始める
#
# イメージは操作端末で build し，SSH の転送を通してこの registry へ push する（scripts/tasks/setup.sh）．
# 制御点の回線はデータ準備の取得で混み合い，ここで build すると PyPI や Docker Hub からの取得が失敗しやすいため．
#
# データ準備（コーパスの取得・埋め込み・ラベル・ルーター学習）は時間がかかるため，
# scripts/remote/prepare_data.sh としてバックグラウンドで動かし，このスクリプトはすぐに戻る．
# 埋め込みは制御点と cluster.gpu_workers の GPU で分担する．各段は冪等なので，
# 途中で止まっても再度 setup を実行すれば続きから再開する．進み具合は `mise run data-status` で確認する．
source "$(dirname "$0")/lib.sh"

ACTION=${1:?usage: setup.sh registry|start [dataset...]}
shift
REGISTRY_NAME=atrium-registry

if [ "$ACTION" = registry ]; then
  # 制御点の 127.0.0.1 だけに公開する．wafl500〜509 は既存の SSH 転送で，それ以外のノードは
  # deploy・prepare_data が張る SSH の逆トンネルで使う
  if docker ps -q -f "name=^${REGISTRY_NAME}$" | grep -q .; then
    log "registry already running"
  elif docker ps -aq -f "name=^${REGISTRY_NAME}$" | grep -q .; then
    docker start "$REGISTRY_NAME"
  else
    docker run -d --name "$REGISTRY_NAME" --restart=always -p "127.0.0.1:${REGISTRY_PORT}:5000" registry:2
  fi
  exit 0
fi

# ── push されたイメージを制御点の docker へ取得する（データ準備のコンテナはこのタグを使う）
for target in node full; do
  docker pull -q "localhost:$REGISTRY_PORT/atrium-$target:latest"
done

# ── データ準備（scripts/remote/prepare_data.sh を SSH が切れても続くように起動する）
DATASETS=${*:-"feb4rag medrag"}
mkdir -p "$DATA_DIR/logs"
PID_FILE="$DATA_DIR/logs/prepare.pid"
if prepare_running; then
  log "data preparation is already running (see: mise run data-status)"
  exit 0
fi
# shellcheck disable=SC2086
setsid nohup bash scripts/remote/prepare_data.sh $DATASETS >> "$DATA_DIR/logs/prepare.log" 2>&1 < /dev/null &
echo $! > "$PID_FILE"
log "started data preparation for: $DATASETS (see: mise run data-status)"
