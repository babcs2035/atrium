#!/usr/bin/env bash
# 制御点のスクリプトを 1 つ呼ぶだけのタスク（stop / clean / data-status）．
#   bash scripts/tasks/remote.sh <script> [args...]
source "$(dirname "$0")/lib.sh"

sync_to_control
remote "$@"
