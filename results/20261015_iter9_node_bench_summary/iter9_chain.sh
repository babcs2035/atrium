#!/usr/bin/env bash
# Iteration 9：専門家ノード 1 台への閉じた系の probe の負荷試験の連続実行（journal の Iteration 9 の事前登録の手順 1）．
#   bash iter9_chain.sh <log> <specs>   # specs は iter9_specs.template.txt の <date> を実行日にしたもの．絶対パスで渡す
# specs の各行は次の 2 種類（# で始まる行と空行は飛ばす）．
#   anchor <run_id> key=value ...           → iter8_chain.sh に 1 行の仕様として渡す（config.yaml を変えて deploy → start → analyze）
#   bench <run_id> <placement_run> <target_host> <concurrency>
#                                           → 制御点で `atrium node-bench` を流し（scripts/remote/node_bench.sh），results/<run_id> を取得する
# bench は直前の anchor の deploy で起動したノードに送る（bench の間は deploy し直さない）．results/<run_id> がある回は飛ばす．
# bench が失敗したら 1 回だけ 60 秒おいてやり直し，それでも失敗すれば記録して次へ進む．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO" || exit 1
LOG=$1
SPECS=$2
ITER8_CHAIN=$REPO/results/20261015_iter8_load_summary/iter8_chain.sh
CONTROL=$(uv run --quiet atrium config get cluster.control)
REMOTE_DIR=$(uv run --quiet atrium config get cluster.remote_dir)
BENCH_RETRY_WAIT_S=60

run_bench() {
  local run=$1 placement_run=$2 target=$3 order=$4
  bash scripts/tasks/remote.sh node_bench "$run" "$placement_run" "$target" "$order" \
    > "$LOG.$run.bench" 2>&1 < /dev/null \
    && mkdir -p "results/$run" \
    && rsync -az "$CONTROL:$REMOTE_DIR/results/$run/" "results/$run/"
}

while read -r kind run rest <&3; do
  [[ -z "$kind" || "$kind" == \#* ]] && continue
  if [ -d "results/$run" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; continue
  fi
  case "$kind" in
    anchor)
      spec=$(mktemp)
      echo "$run $rest" > "$spec"
      bash "$ITER8_CHAIN" "$LOG" "$spec"
      rm -f "$spec"
      ;;
    bench)
      read -r placement_run target order <<< "$rest"
      echo "[$(date +%F_%T)] BEGIN $run target=$target order=$order placement=$placement_run" >> "$LOG"
      if run_bench "$run" "$placement_run" "$target" "$order"; then
        echo "[$(date +%T)] $run bench=0" >> "$LOG"
      else
        echo "[$(date +%T)] $run bench failed; retrying in ${BENCH_RETRY_WAIT_S}s" >> "$LOG"
        rm -rf "results/$run"
        sleep "$BENCH_RETRY_WAIT_S"
        if run_bench "$run" "$placement_run" "$target" "$order"; then
          echo "[$(date +%T)] $run bench=0 (retry)" >> "$LOG"
        else
          echo "[$(date +%T)] $run BENCH FAILED" >> "$LOG"
        fi
      fi
      ;;
    *)
      echo "[$(date +%T)] unknown kind: $kind" >> "$LOG"
      ;;
  esac
done 3< "$SPECS"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
