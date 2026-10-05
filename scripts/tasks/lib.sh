#!/usr/bin/env bash
# mise タスクの共通処理（操作端末 gpu2 側）．各タスクのスクリプトが source する．
#
# 1. config.yaml から artifacts/cluster.env（シェル変数の定義）を作って読み込む
# 2. sync_to_control: リポジトリを制御点（cluster.control）の REMOTE_DIR へ同期する
# 3. remote <script> [args...]: 制御点で scripts/remote/<script>.sh を実行する
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$REPO_ROOT"
mkdir -p artifacts results

uv run --quiet atrium config env > artifacts/cluster.env
# 未コミットの変更がある状態で実験したことを結果から判別できるように -dirty を付ける
GIT_HEAD="$(git rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"
if ! git diff --quiet HEAD 2>/dev/null; then GIT_HEAD="$GIT_HEAD-dirty"; fi
echo "GIT_HEAD=$GIT_HEAD" >> artifacts/cluster.env
# shellcheck source=/dev/null
source artifacts/cluster.env

log() { echo "[$(date +%H:%M:%S)] $*"; }

sync_to_control() {
  ssh "$CONTROL" "mkdir -p $REMOTE_DIR/artifacts"
  # results/ と artifacts/ は制御点側で作られるので，--delete の対象から外す（除外した名前は消されない）
  rsync -az --delete \
    --exclude .git/ --exclude .venv/ --exclude .claude/ --exclude results/ --exclude artifacts/ \
    --exclude __pycache__/ --exclude .mypy_cache/ --exclude .ruff_cache/ --exclude .pytest_cache/ \
    ./ "$CONTROL:$REMOTE_DIR/"
  rsync -az artifacts/cluster.env "$CONTROL:$REMOTE_DIR/artifacts/cluster.env"
}

remote() {
  local script=$1
  shift
  # 引数は空白を含まない前提（実行 ID・データセット名・フラグのみを渡す）
  ssh "$CONTROL" "cd $REMOTE_DIR && bash scripts/remote/$script.sh $*"
}

# 操作端末から制御点の registry へつなぐローカルのポート（操作端末の 5000 番などと衝突しない番号）
LOCAL_REGISTRY_PORT=15000
PUSH_RETRY_WAIT_S=300
TUNNEL_SOCKET="$REPO_ROOT/artifacts/registry-tunnel.sock"

# 制御点の registry への SSH の転送を開き，スクリプトの終了時に閉じる
open_registry_tunnel() {
  ssh -fNT -M -S "$TUNNEL_SOCKET" -o ExitOnForwardFailure=yes \
    -L "$LOCAL_REGISTRY_PORT:localhost:$REGISTRY_PORT" "$CONTROL"
  trap 'ssh -S "$TUNNEL_SOCKET" -O exit "$CONTROL" 2> /dev/null || true' EXIT
}

# イメージ（node / full）を操作端末で build し，制御点の registry へ push する．setup と deploy の両方で呼び，
# イメージを常に今のコードと config.yaml の版にそろえる（古いイメージが新しい設定を読めずに失敗したため）．
# 変更が無ければ build も push もキャッシュで済む
publish_images() {
  for target in node full; do
    local image="localhost:$LOCAL_REGISTRY_PORT/atrium-$target:latest"
    log "building $image (git $GIT_HEAD)"
    docker build -q --target "$target" --build-arg GIT_HEAD="$GIT_HEAD" -t "$image" . > /dev/null
    # 数 GB の層は registry が digest を検証する間にクライアントが応答待ちで打ち切ることがある．
    # すぐ送り直すと検証中の upload と重なって blob が壊れたため，検証が終わるまで待ってから送り直す
    for attempt in 1 2 3; do
      if docker push -q "$image" > /dev/null; then break; fi
      if [ "$attempt" = 3 ]; then return 1; fi
      log "push failed; retrying after ${PUSH_RETRY_WAIT_S}s ($attempt/3)"
      sleep "$PUSH_RETRY_WAIT_S"
    done
  done
}

latest_run() {
  find results -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -1
}
