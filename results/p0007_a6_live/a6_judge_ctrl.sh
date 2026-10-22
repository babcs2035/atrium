#!/usr/bin/env bash
# p0007 A6：制御点で，全候補の採点（判定モデル qwen3:8b を制御点の GPU だけで動かす）と cross-encoder の得点（atrium select-features）を求める．
# wafl500〜509 では他の研究者の学習が GPU を使っており，A5 では判定モデルの Ollama が落ちたため（a5_judge_ctrl.sh と同じ回避）．
#   ssh wafl-ctrl5 bash /home/denjo/workspace/ktakahashi/atrium/a6_judge_ctrl.sh <run_id>
# 制御点の実験（mise run stop の後）で使う．採点の設定は scripts/remote/judge.sh の制御点の部分と同じ．
set -uo pipefail
RUN=${1:?usage: a6_judge_ctrl.sh <run_id>}
cd /home/denjo/workspace/ktakahashi/atrium || exit 1
IMAGE=localhost:5000/mirror/ollama:0.35.1
FULL=localhost:5000/atrium-full:latest
docker compose stop ollama > /dev/null 2>&1 || true
docker rm -f atrium-judge-ollama > /dev/null 2>&1 || true
docker run -d --name atrium-judge-ollama --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user $(id -u):$(id -g) -e HOME=/ollama -e OLLAMA_MODELS=/ollama/models -e OLLAMA_KEEP_ALIVE=-1 -e OLLAMA_NUM_PARALLEL=2 -p 127.0.0.1:11435:11434 -v /home/denjo/atrium-data/ollama:/ollama $IMAGE > /dev/null
for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:11435/api/tags && break; sleep 2; done
curl -fsS -o /dev/null http://127.0.0.1:11435/api/generate -d '{"model": "qwen3:8b", "keep_alive": -1, "options": {"num_ctx": 16384}}'
docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -v /home/denjo/atrium-data:/data -v $PWD/results:/results -v $PWD/config.yaml:/app/config.yaml:ro $FULL atrium --config /app/config.yaml judge --data-dir /data --ollama-url http://127.0.0.1:11435 --run-dir /results/$RUN --with-candidates
rc=$?
docker logs atrium-judge-ollama > results/$RUN.judge_ctrl.ollama.log 2>&1
docker rm -f atrium-judge-ollama > /dev/null 2>&1
echo "JUDGE_CTRL_DONE rc=$rc"
# cross-encoder（bge-reranker-v2-m3．A5 と同じ版・同じ「題 + 改行 + 本文」・batch 4）を制御点の GPU で求める
docker run --rm --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user $(id -u):$(id -g) -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface -e HF_HUB_OFFLINE=1 -v /home/denjo/atrium-data:/data -v $PWD/results:/results -v $PWD/config.yaml:/app/config.yaml:ro $FULL atrium --config /app/config.yaml select-features --run-dir /results/$RUN --data-dir /data
echo "SELECT_FEATURES_DONE rc=$?"
