#!/usr/bin/env bash
# d0009 B3（Iteration 25）：H2 の実機の実行 d0009_b3s_h2_base の採点（回答と全候補．qwen3:8b）と，選択の特徴量（R3′ の ce_features.jsonl・
# 回答を入れた ce_answer_features.jsonl．gpu4 の GPU）を求める．
# 使い方: bash scripts/d0009/b3s_score_all.sh [ollama の URL...]（リポジトリの直下で．URL を省くと gpu4 の container．127.0.0.1:11436）．
# 同じ (質問, 回答) は --share-progress で H2 以外の採点（dev・H の P）と共有できるものは共有する．gpu4 の GPU は他の研究者と共有なので，
# 先に nvidia-smi で空きを確かめる．OOM などで失敗したら成功するまで 120 秒おきに繰り返す（他のプロセスには触れない）．
cd /home/ktakahashi/workspace/atrium || exit 1
J=artifacts/enronqa/judge_raw
run=results/d0009_b3s_h2_base
urls=()
for u in "${@:-http://127.0.0.1:11436}"; do urls+=(--ollama-url "$u"); done
share=()
for o in d0009_a3_p1 d0009_a3_p2 d0009_a3_p3 d0009_b3s_dev; do share+=(--share-progress "$J/$o.qwen3_8b.progress.jsonl"); done
until uv run atrium judge --data-dir artifacts "${urls[@]}" --run-dir "$run" --resume --with-candidates "${share[@]}"; do
  rc=$?
  echo "judge exit $rc; retry in 60 s"
  [ "$rc" -ne 3 ] && exit "$rc"
  sleep 60
done
echo DONE > "$run/.judged"
for kind in "" "--answer-aware"; do
  done_file="$run/.select_features${kind:+.answer}.done"
  until grep -q DONE "$done_file" 2>/dev/null; do
    # shellcheck disable=SC2086
    if uv run atrium select-features --run-dir "$run" --data-dir artifacts $kind; then
      echo DONE > "$done_file"
    else
      echo "select-features $kind failed; retry in 120 s ($(date +%s))"
      sleep 120
    fi
  done
done
echo "SCORED $(date +%s)"
