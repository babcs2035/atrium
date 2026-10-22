#!/usr/bin/env bash
# p0007 A5：wafl500〜509 を他の研究者の学習が使っているので，判定モデルを制御点の GPU だけで動かして採点する（scripts/remote/judge.sh の up・run・down の制御点の部分と同じ）．
set -uo pipefail
cd /home/denjo/workspace/ktakahashi/atrium || exit 1
IMAGE=localhost:5000/mirror/ollama:0.35.1
docker compose stop ollama > /dev/null 2>&1 || true
docker rm -f atrium-judge-ollama > /dev/null 2>&1 || true
docker run -d --name atrium-judge-ollama --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user $(id -u):$(id -g) -e HOME=/ollama -e OLLAMA_MODELS=/ollama/models -e OLLAMA_KEEP_ALIVE=-1 -e OLLAMA_NUM_PARALLEL=2 -p 127.0.0.1:11435:11434 -v /home/denjo/atrium-data/ollama:/ollama $IMAGE > /dev/null
for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:11435/api/tags && break; sleep 2; done
curl -fsS -o /dev/null http://127.0.0.1:11435/api/generate -d '{"model": "qwen3:8b", "keep_alive": -1, "options": {"num_ctx": 16384}}'
docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -v /home/denjo/atrium-data:/data -v $PWD/results:/results -v $PWD/config.yaml:/app/config.yaml:ro localhost:5000/atrium-full:latest atrium --config /app/config.yaml judge --data-dir /data --ollama-url http://127.0.0.1:11435 --run-dir /results/p0007_a5_c1_rrf_ce --run-dir /results/p0007_a5_oracle_rrf
rc=$?
docker logs atrium-judge-ollama > results/p0007_a5_judge_ctrl.ollama.log 2>&1
docker rm -f atrium-judge-ollama > /dev/null 2>&1
echo "JUDGE_CTRL_DONE rc=$rc"
