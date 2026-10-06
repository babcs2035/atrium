#!/usr/bin/env bash
# 制御点（wafl-ctrl5）側の共通処理．scripts/remote/*.sh が source する．
#
# artifacts/cluster.env（操作端末が config.yaml から作ったもの）を読み込み，
# 各ノードへの SSH・rsync・registry の逆トンネル・並列実行の関数を定義する．
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck source=/dev/null
source artifacts/cluster.env

# 以下の 3 つは source した側のスクリプトで使う
# shellcheck disable=SC2034
IMAGE_FULL="localhost:$REGISTRY_PORT/atrium-full:latest"
# shellcheck disable=SC2034
HOST_UID=$(id -u)
# shellcheck disable=SC2034
HOST_GID=$(id -g)
# 新しいノードへの初回接続ではホスト鍵を known_hosts に追記する（以降は鍵の変化を検出する）
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)

log() { echo "[$(date +%H:%M:%S)] $*"; }

nssh() {
  local host=$1
  shift
  ssh "${SSH_OPTS[@]}" "$SSH_USER@$host" "$@"
}

# retry <回数> <コマンド...>: 失敗したら 30 秒待って繰り返す（registry が混み合ってイメージの取得が
# 一時的に失敗することがあったため．2026-10-06，シャードの配布と同時に 16 台が取得した）
retry() {
  local n=$1
  shift
  for attempt in $(seq 1 "$n"); do
    if "$@"; then return 0; fi
    log "retrying ($attempt/$n): $*"
    sleep 30
  done
  return 1
}

nrsync() {
  rsync -e "ssh ${SSH_OPTS[*]}" "$@"
}

# ノードの localhost:$REGISTRY_PORT を制御点の registry へつなぐ．
# docker は localhost の registry だけを TLS なしで許すため，各ノードから見て localhost になるようにする
ensure_tunnel() {
  local host=$1
  if nssh "$host" "curl -fsS -o /dev/null http://localhost:$REGISTRY_PORT/v2/" 2>/dev/null; then
    return 0
  fi
  ssh "${SSH_OPTS[@]}" -fNT -o ExitOnForwardFailure=yes \
    -R "$REGISTRY_PORT:localhost:$REGISTRY_PORT" "$SSH_USER@$host"
}

# run_parallel <task> <func> <host>...: func host をホストごとに並列に実行する．
# 出力は artifacts/logs/<task>/<host>.log に分け，1 台でも失敗すれば失敗を返す
run_parallel() {
  local task=$1 func=$2
  shift 2
  local logdir="artifacts/logs/$task"
  mkdir -p "$logdir"
  local -a pids=() hosts=()
  for host in "$@"; do
    ("$func" "$host") > "$logdir/$host.log" 2>&1 &
    pids+=("$!")
    hosts+=("$host")
  done
  local failed=0
  for i in "${!pids[@]}"; do
    if wait "${pids[$i]}"; then
      log "ok: ${hosts[$i]}"
    else
      log "FAILED: ${hosts[$i]} (log: $logdir/${hosts[$i]}.log)"
      tail -n 5 "$logdir/${hosts[$i]}.log" | sed 's/^/    /'
      failed=1
    fi
  done
  return "$failed"
}

# 予約されたまま使われていない hugepages を解放し，索引と LLM が通常のメモリを使えるようにする．
# 専門家ノード（i5-8350U，16 GB）では起動時の設定で 1 GB × 13 が予約され，通常のメモリが約 1.7 GB しか
# 残っていなかった（2026-10-06．解放はユーザーが許可）．実行時の値だけを変えるので，再起動すると戻る
# config.yaml の cluster.release_hugepages が true のときだけ解放する
release_hugepages() {
  local host=$1
  if [ "$RELEASE_HUGEPAGES" = 1 ]; then
    nssh "$host" "sudo -n sysctl -q -w vm.nr_hugepages=0"
  fi
}

# 以前 root で動いていた Ollama が作ったファイルの所有者を SSH のユーザーへ戻す（自分の成果物だけが対象）
own_remote_dir() {
  local host=$1
  nssh "$host" "mkdir -p $REMOTE_DIR/ollama && sudo -n chown -R \$(id -u):\$(id -g) $REMOTE_DIR/ollama"
}

# 置き換わって参照されなくなった自前のイメージ（ラベル org.atrium.project=atrium）だけを消す．
# 19 GB の atrium-full が更新のたびに残り，専門家のディスクが 95% まで埋まったため（2026-10-06）
prune_old_images() {
  local host=$1
  nssh "$host" "docker image prune -f --filter label=org.atrium.project=atrium > /dev/null"
}

# Ollama のモデル 1 個（例: qwen3:0.6b）のマニフェストと blob を，$DATA_DIR/ollama/models からの相対パスで出す
ollama_model_files() {
  local model=$1
  local name=${model%%:*} tag=${model#*:}
  local manifest="manifests/registry.ollama.ai/library/$name/$tag"
  echo "$manifest"
  jq -r '.config.digest, .layers[].digest' "$DATA_DIR/ollama/models/$manifest" | sed 's|^sha256:|blobs/sha256-|'
}

# Ollama のモデルを 1 個だけノードへ配る（モデルのディレクトリ全体を送ると，質問者用の 8B まで配ってしまう）
send_ollama_model() {
  local host=$1 model=$2
  local list
  list=$(mktemp)
  ollama_model_files "$model" > "$list"
  nssh "$host" "mkdir -p $REMOTE_DIR/ollama/models"
  nrsync -a --files-from="$list" "$DATA_DIR/ollama/models/" "$SSH_USER@$host:$REMOTE_DIR/ollama/models/"
  rm -f "$list"
}

dataset_dir() {
  echo "$DATA_DIR/$DATASET"
}
