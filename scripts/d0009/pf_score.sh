#!/usr/bin/env bash
# d0009 PF（Iteration 27）：実行の R3′ の最終回答を qwen3:8b（gpu4 の判定の container．127.0.0.1:11436）で採点する．
# 使い方: bash scripts/d0009/pf_score.sh <run 名>...（リポジトリの直下で．例: d0009_pf_dev_direct d0009_m1_h2_base）
# 各 run：select-features（ce_max．gpu4 の GPU．済んでいれば飛ばす）→ m1_derive.py で results/<run>_R3p（R3′ の最終回答）→ judge --resume．
# 失敗したら成功するまで 120 秒おきに繰り返す（gpu4 の GPU は共有．他のプロセスには触れない）．
cd /home/ktakahashi/workspace/atrium || exit 1
for run in "$@"; do
  dir=results/$run
  until [ -s "$dir/ce_features.jsonl" ] && grep -q DONE "$dir/.select_features.done" 2>/dev/null; do
    if uv run atrium select-features --run-dir "$dir" --data-dir artifacts; then
      echo DONE > "$dir/.select_features.done"
    else
      echo "select-features $run failed; retry in 120 s ($(date +%s))"
      sleep 120
    fi
  done
  uv run python scripts/d0009/m1_derive.py "${dir}_R3p" "$dir" || exit 1
  until uv run atrium judge --data-dir artifacts --ollama-url http://127.0.0.1:11436 \
      --run-dir "${dir}_R3p" --resume; do
    rc=$?
    echo "judge exit $rc; retry in 60 s"
    [ "$rc" -ne 3 ] && exit "$rc"
    sleep 60
  done
  echo "SCORED $run $(date +%s)"
done
