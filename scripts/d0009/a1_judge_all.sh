#!/usr/bin/env bash
# d0009 A1（Iteration 23）の採点：N1・N3 の 4 回（n1_r1・n1_r2・n3_r1・n3_r2）の回答と全候補を，qwen3:8b（gpu4 の container．127.0.0.1:11436）で採点する．
# 使い方: bash scripts/d0009/a1_judge_all.sh [ollama の URL...]（リポジトリの直下で．URL を省くと gpu4 の container）．結果が出た回だけ採点する．
# 同じ (質問, 回答) は --share-progress で Iteration 22 の P（d0009_a3_p1〜p3）と A1 の他の回と共有し，1 回だけ採点する（運用ルール 14：
# 実験の実行中の wafl500〜509 は使わない．gpu4 の GPU は他の研究者と共有なので，先に nvidia-smi で空きを確かめる）．
cd /home/ktakahashi/workspace/atrium || exit 1
J=artifacts/enronqa/judge_raw
RUNS="n1_r1 n3_r1 n3_r2 n1_r2"
urls=()
for u in "${@:-http://127.0.0.1:11436}"; do urls+=(--ollama-url "$u"); done
for r in $RUNS; do
  run=results/d0009_a1_$r
  [ -f "$run/run_meta.json" ] || { echo "skip $r (not finished)"; continue; }
  # 連続実行の中の回は，流し直しと analyze まで済んでから採点する（CHAIN_LOG に "<run> analyze=" の行ができたら）
  if [ -n "${CHAIN_LOG:-}" ] && ! grep -q "d0009_a1_$r analyze=" "$CHAIN_LOG"; then echo "skip $r (chain still running it)"; continue; fi
  [ -s "$run/candidate_judgements.jsonl" ] && grep -q DONE "$run/.judged" 2>/dev/null && { echo "skip $r (judged)"; continue; }
  share=()
  for o in p1 p2 p3; do share+=(--share-progress "$J/d0009_a3_$o.qwen3_8b.progress.jsonl"); done
  for o in $RUNS; do
    [ "$o" != "$r" ] && share+=(--share-progress "$J/d0009_a1_$o.qwen3_8b.progress.jsonl")
  done
  until uv run atrium judge --data-dir artifacts "${urls[@]}" --run-dir "$run" --resume --with-candidates "${share[@]}"; do
    rc=$?
    echo "judge $r exit $rc; retry in 60 s"
    [ "$rc" -ne 3 ] && exit "$rc"
    sleep 60
  done
  echo DONE > "$run/.judged"
  echo "JUDGED $r $(date +%s)"
done
echo "ALL JUDGED $(date +%s)"
