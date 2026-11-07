#!/usr/bin/env bash
# d0009 A3 の P の 3 回について，cross-encoder の得点（select-features）と R3′ の当てはめ（select-eval）を行う．
# 使い方: bash scripts/d0009/a3_select_all.sh（リポジトリの直下で．採点（judge --with-candidates）の後）．
# gpu4 の GPU は他の研究者のプロセスと共有で，空きが足りずに OOM になることがあるので，成功するまで 120 秒おきに繰り返す
# （他のプロセスには触れない）．
cd /home/ktakahashi/workspace/atrium || exit 1
for r in p1 p2 p3; do
  run=results/d0009_a3_$r
  until [ -s "$run/ce_features.jsonl" ] && grep -q DONE "$run/.select_features.done" 2>/dev/null; do
    if uv run atrium select-features --run-dir "$run" --data-dir artifacts; then
      echo DONE > "$run/.select_features.done"
    else
      echo "select-features $r failed; retry in 120 s ($(date +%s))"
      sleep 120
    fi
  done
  uv run atrium select-eval --run-dir "$run" --out-dir "results/d0009_a3_${r}_select" \
    --apply R3 --ignore-unknown --derived-run "results/d0009_a3_${r}_R3p" || exit 1
  echo "SELECTED $r $(date +%s)"
done
echo "ALL SELECTED $(date +%s)"
