#!/usr/bin/env bash
# p0007 A6：制御点で，全候補の採点（判定モデル qwen3:8b を制御点の GPU だけで動かす）と cross-encoder の得点（atrium select-features）を求める．
# wafl500〜509 では他の研究者の学習が GPU を使っており，A5 では判定モデルの Ollama が落ちたため（a5_judge_ctrl.sh と同じ回避）．
# 採点は a6_judge_chunks.sh で行を分けて行う（dev の 1 回目の採点で，制御点の判定モデルの Ollama も途中で応答しなくなったため）．
#   ssh wafl-ctrl5 bash /home/denjo/workspace/ktakahashi/atrium/a6_judge_ctrl.sh <run_id>
# 制御点の実験（mise run stop の後）で使う．a6_judge_chunks.sh を同じ場所に置いておく．採点の設定は scripts/remote/judge.sh の制御点の部分と同じ．
set -uo pipefail
RUN=${1:?usage: a6_judge_ctrl.sh <run_id>}
cd /home/denjo/workspace/ktakahashi/atrium || exit 1
FULL=localhost:5000/atrium-full:latest
bash a6_judge_chunks.sh "$RUN"
# cross-encoder（bge-reranker-v2-m3．A5 と同じ版・同じ「題 + 改行 + 本文」・batch 4）を制御点の GPU で求める
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user $(id -u):$(id -g) -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 -v /home/denjo/atrium-data:/data -v $PWD/results:/results -v $PWD/config.yaml:/app/config.yaml:ro $FULL atrium --config /app/config.yaml select-features --run-dir /results/$RUN --data-dir /data
echo "SELECT_FEATURES_DONE rc=$?"
