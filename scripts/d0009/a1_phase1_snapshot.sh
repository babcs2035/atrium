#!/usr/bin/env bash
# d0009 A1（Iteration 23）：失敗した問の流し直し（同じ run_id での 2 回目の start）は finished_at を上書きするので，1 回目の終わりの記録を残す．
# 各回で，results/<run_id>/run_meta.json が最初に現れた（1 回目の start が終わって回収された）ときに phase1_run_meta.json へ写す（上書きしない）．
#   (nohup setsid bash scripts/d0009/a1_phase1_snapshot.sh n3_r2 n1_r2 n3_hi > /dev/null 2>&1 < /dev/null &)
cd /home/ktakahashi/workspace/atrium || exit 1
for tag in "$@"; do
  run=results/d0009_a1_$tag
  until [ -f "$run/run_meta.json" ]; do sleep 3; done
  [ -f "$run/phase1_run_meta.json" ] || cp "$run/run_meta.json" "$run/phase1_run_meta.json"
  wc -l < "$run/results.jsonl" > "$run/phase1_n_rows.txt"
done
