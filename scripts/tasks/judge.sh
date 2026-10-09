#!/usr/bin/env bash
# EnronQA の回答の採点（p0004 §8.6）．制御点の scripts/remote/judge.sh を呼び，結果を操作端末の results/ へ取得する．
#
#   bash scripts/tasks/judge.sh up <model>                  # 判定モデル（または参照モデル）の Ollama を全 GPU で起動する
#   bash scripts/tasks/judge.sh run <run_id>...             # 採点して results/<run_id>/judgements.jsonl を取得する
#   bash scripts/tasks/judge.sh validate <out_name>         # 判定モデルの検証（results/<out_name>）
#   bash scripts/tasks/judge.sh kappa <out_name> <run_id>... # 参照モデルとの一致（results/<out_name>）
#   bash scripts/tasks/judge.sh down                        # Ollama を止める
# 採点の後に `mise run analyze <run_id>` で正答率を出す．
source "$(dirname "$0")/lib.sh"

ACTION=${1:?usage: judge.sh up|run|validate|kappa|down ...}
sync_to_control
if [ "$ACTION" = up ]; then
  # 採点のコードを今の作業ツリーにそろえる
  open_registry_tunnel
  publish_images
  ssh "$CONTROL" "docker pull -q localhost:$REGISTRY_PORT/atrium-full:latest > /dev/null"
fi
remote judge "$@"
case "$ACTION" in
  run)
    shift
    for run in "$@"; do
      rsync -az "$CONTROL:$REMOTE_DIR/results/$run/judgements.jsonl" "results/$run/"
    done
    ;;
  validate | kappa)
    rsync -az "$CONTROL:$REMOTE_DIR/results/$2/" "results/$2/"
    ;;
esac
