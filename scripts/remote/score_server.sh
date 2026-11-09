#!/usr/bin/env bash
# 制御点から GPU の PC（wafl502〜509 など）で cross-encoder の採点の server（`atrium score-server`．d0009 B2）の container を起動・停止する．
#
# 使い方（操作端末から `bash scripts/tasks/remote.sh score_server start <host>...`．host は 192.168.15.10N の形）:
#   bash scripts/remote/score_server.sh start <host>...   image・モデルを用意して container atrium-scorer を起動し，/healthz が 200 になるまで待つ
#   bash scripts/remote/score_server.sh stop <host>...    container atrium-scorer を止める（削除・image・モデルの削除はしない）
# - image は制御点の registry の atrium-full（CUDA 版の torch を含む．deploy 済みでなければ publish が要る）．
# - モデルは各機の ~/.cache/huggingface/hub に hf CLI で取得したものを，読み取り専用で mount する（運用ルール 15．無ければ container の hf で取得する）．
# - GPU を使う自分たちの Ollama（container atrium-ollama-1）が llama3.1:8b を載せていたら，keep_alive: 0 で VRAM から降ろす（共存できないため．
#   モデルのファイル・container は消さない．他の研究者のプロセスには触れない）．
source "$(dirname "$0")/lib.sh"

USAGE="usage: score_server.sh <start|stop> <host>..."
ACTION=${1:?$USAGE}
shift
[ "$#" -gt 0 ] || { echo "$USAGE" >&2; exit 2; }
SCORER_CONTAINER=atrium-scorer
SCORER_PORT=8200
HEALTH_WAIT_S=600
MODEL=BAAI/bge-reranker-v2-m3
# 固定の revision は src/atrium/hf_revisions.py が唯一の定義（制御点には atrium の python のパッケージが無いので文字列として読む）
REVISION=$(grep -o "\"$MODEL\": \"[0-9a-f]*\"" src/atrium/hf_revisions.py | grep -o '[0-9a-f]\{40\}')
[ -n "$REVISION" ] || { echo "no pinned revision for $MODEL" >&2; exit 1; }
HF_HUB_HOST_DIR='$HOME/.cache/huggingface/hub'
MODEL_SNAPSHOT="$HF_HUB_HOST_DIR/models--${MODEL/\//--}/snapshots/$REVISION"

start_host() {
  local host=$1 uidgid
  ensure_tunnel "$host"
  retry 3 nssh "$host" "docker pull -q $IMAGE_FULL > /dev/null"
  uidgid=$(nssh "$host" 'echo $(id -u):$(id -g)')
  if ! nssh "$host" "[ -d $MODEL_SNAPSHOT ]"; then
    log "$host: fetching $MODEL@$REVISION with hf"
    retry 3 nssh "$host" "mkdir -p $HF_HUB_HOST_DIR && docker run --rm --user $uidgid -e HOME=/tmp \
      -v $HF_HUB_HOST_DIR:/cache/huggingface/hub $IMAGE_FULL hf download $MODEL --revision $REVISION > /dev/null"
  fi
  nssh "$host" "[ -d $MODEL_SNAPSHOT ]"
  nssh "$host" "docker rm -f $SCORER_CONTAINER > /dev/null 2>&1 || true"
  # 自分たちの Ollama が載せている llama3.1:8b（約 6.7 GB）を降ろす（container が無ければ何もしない）
  nssh "$host" 'ip=$(docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}" atrium-ollama-1 2> /dev/null); \
    if [ -n "$ip" ]; then curl -s -m 30 http://$ip:11434/api/generate -d "{\"model\":\"llama3.1:8b\",\"keep_alive\":0}" > /dev/null || true; fi'
  nssh "$host" "docker run -d --name $SCORER_CONTAINER --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user $uidgid -e HOME=/tmp \
    -e HF_HUB_OFFLINE=1 -v $HF_HUB_HOST_DIR:/cache/huggingface/hub:ro -v $REMOTE_DIR/config.yaml:/app/config.yaml:ro \
    -p $SCORER_PORT:$SCORER_PORT $IMAGE_FULL atrium --config /app/config.yaml score-server --port $SCORER_PORT > /dev/null"
  local waited=0
  until nssh "$host" "curl -fsS -m 5 http://localhost:$SCORER_PORT/healthz" > /dev/null 2>&1; do
    sleep 5
    waited=$((waited + 5))
    if [ "$waited" -ge "$HEALTH_WAIT_S" ]; then
      log "$host: scorer did not become healthy" >&2
      nssh "$host" "docker logs --tail 20 $SCORER_CONTAINER" >&2 || true
      return 1
    fi
  done
  log "$host: scorer healthy after ${waited}s"
}

stop_host() {
  nssh "$1" "docker stop $SCORER_CONTAINER > /dev/null 2>&1 || true"
}

case "$ACTION" in
  start) run_parallel score_server_start start_host "$@" ;;
  stop) run_parallel score_server_stop stop_host "$@" ;;
  *) echo "$USAGE" >&2; exit 2 ;;
esac
