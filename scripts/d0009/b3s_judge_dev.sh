#!/usr/bin/env bash
# d0009 B3（Iteration 25）：dev（p0007_a6_dev の写し results/d0009_b3s_dev/）の回答と全候補を，H と同じ判定モデル（qwen3:8b．gpu4 の container．127.0.0.1:11436）で採点し直す．
# 使い方: bash scripts/d0009/b3s_judge_dev.sh [ollama の URL...]（リポジトリの直下で．URL を省くと gpu4 の container）．
# 先に nvidia-smi で GPU の空きを確かめ，container（atrium-judge-ollama-local）を起動しておく（他の研究者のプロセスには触れない）．
cd /home/ktakahashi/workspace/atrium || exit 1
run=results/d0009_b3s_dev
urls=()
for u in "${@:-http://127.0.0.1:11436}"; do urls+=(--ollama-url "$u"); done
until uv run atrium judge --data-dir artifacts "${urls[@]}" --run-dir "$run" --resume --with-candidates; do
  rc=$?
  echo "judge exit $rc; retry in 60 s"
  [ "$rc" -ne 3 ] && exit "$rc"
  sleep 60
done
echo DONE > "$run/.judged"
echo "JUDGED $(date +%s)"
