#!/usr/bin/env bash
# p0007 F-7：制御点の GPU だけで，採った回答（final_answer）を `atrium judge --resume` で採点する（wafl500〜509 の他の研究者の学習には触れない）．
# 判定モデル qwen3:8b の Ollama が止まって終了コード 3 になったら，Ollama を起動し直して同じコマンドを再実行する（採点済みは飛ばす．最大 MAX_PASSES 回）．
#   ssh wafl-ctrl5 bash /home/denjo/workspace/ktakahashi/atrium/f7_judge_ctrl.sh <run_id>...
# 実験の後（mise run stop の後）に使う．採点の設定は scripts/remote/judge.sh の resume（制御点の部分）と同じ．
set -uo pipefail
cd /home/denjo/workspace/ktakahashi/atrium || exit 1
IMAGE=localhost:5000/mirror/ollama:0.35.1
FULL=localhost:5000/atrium-full:latest
MAX_PASSES=6
REQUEST_TIMEOUT_S=300
start_ollama() {
  docker compose stop ollama > /dev/null 2>&1 || true
  docker rm -f atrium-judge-ollama > /dev/null 2>&1 || true
  docker run -d --name atrium-judge-ollama --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user $(id -u):$(id -g) -e HOME=/ollama -e OLLAMA_MODELS=/ollama/models -e OLLAMA_KEEP_ALIVE=-1 -e OLLAMA_NUM_PARALLEL=2 -p 127.0.0.1:11435:11434 -v /home/denjo/atrium-data/ollama:/ollama $IMAGE > /dev/null
  for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:11435/api/tags && break; sleep 2; done
  curl -fsS -o /dev/null http://127.0.0.1:11435/api/generate -d '{"model": "qwen3:8b", "keep_alive": -1, "options": {"num_ctx": 16384}}'
}
judge_once() {
  local runs=()
  for run in "$@"; do runs+=(--run-dir "/results/$run"); done
  docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -v /home/denjo/atrium-data:/data -v $PWD/results:/results -v $PWD/config.yaml:/app/config.yaml:ro $FULL atrium --config /app/config.yaml judge --data-dir /data --ollama-url http://127.0.0.1:11435 --resume --request-timeout-s $REQUEST_TIMEOUT_S "${runs[@]}"
}
for pass in $(seq 1 $MAX_PASSES); do
  echo "[$(date +%T)] pass $pass: restarting the judge Ollama"
  start_ollama
  judge_once "$@"
  rc=$?
  echo "JUDGE_PASS $pass rc=$rc"
  if [ "$rc" = 0 ]; then echo "JUDGE_DONE rc=0"; docker rm -f atrium-judge-ollama > /dev/null 2>&1; exit 0; fi
  if [ "$rc" != 3 ]; then break; fi
done
docker rm -f atrium-judge-ollama > /dev/null 2>&1
echo "JUDGE_DONE rc=1"
exit 1
