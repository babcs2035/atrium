#!/usr/bin/env bash
# p0007 A6-S1：判定モデルの Ollama が応答しなくなった（プロセスが固まった）ときの `atrium judge --resume` の打ち切りの確認．
# 採点の途中で Ollama を `docker pause` で固め，要求の待ち時間（--request-timeout-s 20）と失敗した要求の連続回数（6 回）で打ち切られ，
# 終了コード 3 で終わること（採点済みの件は残る）を確かめる．制御点の GPU だけを使う．a6s1_check.sh と同じ前提．
#   ssh wafl-ctrl5 bash /home/denjo/workspace/ktakahashi/atrium/a6s1_hang_check.sh
set -uo pipefail
cd /home/denjo/workspace/ktakahashi/atrium || exit 1
IMAGE=localhost:5000/mirror/ollama:0.35.1
FULL=localhost:5000/atrium-full:latest
PAUSE_AFTER_S=60
mkdir -p results/a6s1_hang
cp results/a6s1_resume/results.jsonl results/a6s1_hang/results.jsonl
rm -f /home/denjo/atrium-data/enronqa/judge_raw/a6s1_hang*
docker compose stop ollama > /dev/null 2>&1 || true
docker rm -f atrium-judge-ollama > /dev/null 2>&1 || true
docker run -d --name atrium-judge-ollama --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user $(id -u):$(id -g) -e HOME=/ollama -e OLLAMA_MODELS=/ollama/models -e OLLAMA_KEEP_ALIVE=-1 -e OLLAMA_NUM_PARALLEL=2 -p 127.0.0.1:11435:11434 -v /home/denjo/atrium-data/ollama:/ollama $IMAGE > /dev/null
for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:11435/api/tags && break; sleep 2; done
curl -fsS -o /dev/null http://127.0.0.1:11435/api/generate -d '{"model": "qwen3:8b", "keep_alive": -1, "options": {"num_ctx": 16384}}'
( sleep $PAUSE_AFTER_S; docker pause atrium-judge-ollama > /dev/null; echo "[$(date +%T)] paused" ) &
start=$(date +%s)
docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -e PYTHONPATH=/overlay/src -v $PWD:/overlay:ro -v /home/denjo/atrium-data:/data -v $PWD/results:/results -v $PWD/config.yaml:/app/config.yaml:ro $FULL atrium --config /app/config.yaml judge --data-dir /data --ollama-url http://127.0.0.1:11435 --with-candidates --resume --request-timeout-s 20 --run-dir /results/a6s1_hang
echo "HANG_RC=$? elapsed_s=$(( $(date +%s) - start ))"
wait
echo "progress lines: $(wc -l < /home/denjo/atrium-data/enronqa/judge_raw/a6s1_hang.qwen3_8b.progress.jsonl)"
ls results/a6s1_hang
docker unpause atrium-judge-ollama > /dev/null 2>&1
docker rm -f atrium-judge-ollama > /dev/null 2>&1
echo "HANG_CHECK_DONE"
