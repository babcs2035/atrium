#!/usr/bin/env bash
# d0009 M1（Iteration 26）：派生の実行（results/d0009_m1_q_{base,g4}_R3p_j1）の最終回答を J1（qwen3:14b-q8_0．gpu4 の container．127.0.0.1:11436）で採点する．
# 使い方: bash scripts/d0009/m1_judge_j1.sh <派生の実行の名前>...（リポジトリの直下で．m1_import_judge.sh の後）．
# 判定モデルだけを置き換えた設定（results/d0009_m1_generator/config_j1.yaml．config.yaml の judge_model だけが違う）を --config で渡す．
# 止まっても同じコマンドで再開できる（採点済みを飛ばす．終了コード 3 は未採点が残ったとき）．
cd /home/ktakahashi/workspace/atrium || exit 1
for name in "$@"; do
  until uv run atrium --config results/d0009_m1_generator/config_j1.yaml judge --data-dir artifacts \
      --ollama-url http://127.0.0.1:11436 --run-dir "results/$name" --resume; do
    rc=$?
    echo "judge exit $rc; retry in 60 s"
    [ "$rc" -ne 3 ] && exit "$rc"
    sleep 60
  done
  echo "JUDGED $name $(date +%s)"
done
