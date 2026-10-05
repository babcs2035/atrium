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

latest_run() {
  find results -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | tail -1
}
