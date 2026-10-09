#!/usr/bin/env bash
# 制御点から EnronQA の回答の採点（判定モデル．p0004 §8.6）を行う．判定モデルの Ollama を制御点と
# gpu_workers（wafl500〜509）の GPU で動かし，制御点のコンテナの `atrium judge*` から並列に要求を送る．
#
# 使い方（操作端末の `bash scripts/tasks/judge.sh` から呼ばれる）:
#   bash scripts/remote/judge.sh up <model>                 # 判定モデルの Ollama を全 GPU で起動する
#   bash scripts/remote/judge.sh run <run_id>...            # 実行の回答を採点し，results/<run_id>/judgements.jsonl を書く
#   bash scripts/remote/judge.sh validate <out_name>        # 付属の別解・誤答で判定モデルを検証する（results/<out_name>）
#   bash scripts/remote/judge.sh kappa <out_name> <run_id>... # 参照モデルで採点し直して κ を出す（先に up で参照モデルを起動する）
#   bash scripts/remote/judge.sh down                       # 判定モデルの Ollama を止めて消す
#
# GPU の PC は専門家（experts）でもあるので，up はその PC の専門家のコンテナを止める．実験の後に使う．
source "$(dirname "$0")/lib.sh"

ACTION=${1:?usage: judge.sh up|run|validate|kappa|down ...}
shift
NAME=atrium-judge-ollama
IMAGE="localhost:$REGISTRY_PORT/mirror/ollama:$OLLAMA_TAG"
# 制御点の Ollama は質問者の compose（11434 は compose の中だけ）と別に，127.0.0.1 の 11435 番で公開する
CONTROL_PORT=11435
WORKER_PORT=11434
JUDGE_PARALLEL=2

ollama_urls() {
  local args="--ollama-url http://127.0.0.1:$CONTROL_PORT"
  for host in $GPU_WORKERS; do args="$args --ollama-url http://$host:$WORKER_PORT"; done
  echo "$args"
}

# ollama_run_cmd <models_dir> <publish>: GPU で Ollama を起動するコマンドを出す（制御点では eval，GPU の PC では nssh で実行する）
ollama_run_cmd() {
  local models_dir=$1 publish=$2
  echo "docker rm -f $NAME > /dev/null 2>&1; docker run -d --name $NAME --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all \
    --user \$(id -u):\$(id -g) -e HOME=/ollama -e OLLAMA_MODELS=/ollama/models -e OLLAMA_KEEP_ALIVE=-1 \
    -e OLLAMA_NUM_PARALLEL=$JUDGE_PARALLEL -p $publish:11434 -v $models_dir:/ollama $IMAGE > /dev/null"
}

preload_cmd() {
  local port=$1 model=$2
  echo "for _ in \$(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:$port/api/tags && break; sleep 2; done; \
    curl -fsS -o /dev/null http://127.0.0.1:$port/api/generate -d '{\"model\": \"$model\", \"keep_alive\": -1, \"options\": {\"num_ctx\": $LLM_NUM_CTX}}'"
}

# up_worker <host>: GPU の PC で判定モデル（$MODEL）の Ollama を起動する（run_parallel から呼ぶ）
up_worker() {
  local host=$1 model=$MODEL
  ensure_tunnel "$host"
  # 専門家のコンテナ（GPU の Ollama を含む）を止めて GPU を空ける
  nssh "$host" "[ -f $REMOTE_DIR/compose.yml ] && cd $REMOTE_DIR && docker compose --profile run stop || true"
  send_ollama_model "$host" "$model"
  retry 3 nssh "$host" "docker pull -q $IMAGE > /dev/null"
  nssh "$host" "$(ollama_run_cmd "$REMOTE_DIR/ollama" "$WORKER_PORT")"
  retry 3 nssh "$host" "$(preload_cmd "$WORKER_PORT" "$model")"
}

judge_container() {
  docker run --rm --network host --user "$HOST_UID:$HOST_GID" -e HOME=/tmp \
    -v "$DATA_DIR:/data" -v "$PWD/results:/results" -v "$PWD/config.yaml:/app/config.yaml:ro" \
    "$IMAGE_FULL" atrium --config /app/config.yaml "$@"
}

case "$ACTION" in
  up)
    MODEL=${1:?usage: judge.sh up <model>}
    # 制御点：質問者の Ollama を止めて GPU を空け，$DATA_DIR/ollama のモデルをそのまま使う
    (cd "$REMOTE_DIR" && docker compose stop ollama > /dev/null 2>&1) || true
    eval "$(ollama_run_cmd "$DATA_DIR/ollama" "127.0.0.1:$CONTROL_PORT")"
    eval "$(preload_cmd "$CONTROL_PORT" "$MODEL")"
    # shellcheck disable=SC2086
    run_parallel judge-up up_worker $GPU_WORKERS
    ;;
  run)
    [ "$#" -gt 0 ] || { echo "usage: judge.sh run <run_id>..." >&2; exit 1; }
    runs=()
    for run in "$@"; do runs+=(--run-dir "/results/$run"); done
    # shellcheck disable=SC2046
    judge_container judge --data-dir /data $(ollama_urls) "${runs[@]}"
    ;;
  validate)
    OUT=${1:?usage: judge.sh validate <out_name>}
    # shellcheck disable=SC2046
    judge_container judge-validate --data-dir /data $(ollama_urls) --out-dir "/results/$OUT"
    ;;
  kappa)
    OUT=${1:?usage: judge.sh kappa <out_name> <run_id>...}
    shift
    runs=()
    for run in "$@"; do runs+=(--run-dir "/results/$run"); done
    # shellcheck disable=SC2046
    judge_container judge-kappa --data-dir /data $(ollama_urls) --out-dir "/results/$OUT" "${runs[@]}"
    ;;
  down)
    docker rm -f "$NAME" > /dev/null 2>&1 || true
    for host in $GPU_WORKERS; do nssh "$host" "docker rm -f $NAME > /dev/null 2>&1 || true"; done
    ;;
  *)
    echo "unknown action: $ACTION" >&2
    exit 1
    ;;
esac
