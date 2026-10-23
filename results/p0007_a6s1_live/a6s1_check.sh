#!/usr/bin/env bash
# p0007 A6-S1：`atrium judge --resume` の実データでの確認．p0007_a6_test の先頭 300 行（候補つき）を，(1) 再開なし，(2) 再開あり（採点の途中で
# 判定モデルの Ollama を止め，終了コード 3 を確かめてから起動し直して再実行）の 2 通りで採点し，出力が一致することを確かめる．
# 制御点の GPU だけを使う（wafl500〜509 には触れない）．コードは制御点へ rsync した src を PYTHONPATH で重ねて使う（イメージは作り直さない）．
#   ssh wafl-ctrl5 bash /home/denjo/workspace/ktakahashi/atrium/a6s1_check.sh
set -uo pipefail
cd /home/denjo/workspace/ktakahashi/atrium || exit 1
IMAGE=localhost:5000/mirror/ollama:0.35.1
FULL=localhost:5000/atrium-full:latest
N_ROWS=300
STOP_AFTER_S=240
start_ollama() {
  docker compose stop ollama > /dev/null 2>&1 || true
  docker rm -f atrium-judge-ollama > /dev/null 2>&1 || true
  docker run -d --name atrium-judge-ollama --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user $(id -u):$(id -g) -e HOME=/ollama -e OLLAMA_MODELS=/ollama/models -e OLLAMA_KEEP_ALIVE=-1 -e OLLAMA_NUM_PARALLEL=2 -p 127.0.0.1:11435:11434 -v /home/denjo/atrium-data/ollama:/ollama $IMAGE > /dev/null
  for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:11435/api/tags && break; sleep 2; done
  curl -fsS -o /dev/null http://127.0.0.1:11435/api/generate -d '{"model": "qwen3:8b", "keep_alive": -1, "options": {"num_ctx": 16384}}'
}
judge() {
  docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -e PYTHONPATH=/overlay/src -v $PWD:/overlay:ro -v /home/denjo/atrium-data:/data -v $PWD/results:/results -v $PWD/config.yaml:/app/config.yaml:ro $FULL atrium --config /app/config.yaml judge --data-dir /data --ollama-url http://127.0.0.1:11435 --with-candidates "$@"
}
for name in a6s1_ref a6s1_resume; do
  mkdir -p results/$name
  # 失敗した行・同じ質問の重複を除いた先頭 N_ROWS 行
  python3 - $N_ROWS results/$name/results.jsonl <<'PY'
import json, sys
n, out = int(sys.argv[1]), sys.argv[2]
seen, keep = set(), []
for line in open("results/p0007_a6_test/results.jsonl", encoding="utf-8"):
    row = json.loads(line)
    if row.get("error") is None and row["qid"] not in seen:
        seen.add(row["qid"]); keep.append(line)
    if len(keep) == n:
        break
open(out, "w", encoding="utf-8").write("".join(keep))
PY
done
rm -f /home/denjo/atrium-data/enronqa/judge_raw/a6s1_*
echo "[$(date +%T)] reference (no resume)"
start_ollama
judge --run-dir /results/a6s1_ref
echo "REF rc=$?"
echo "[$(date +%T)] resume, first pass (Ollama is stopped after ${STOP_AFTER_S}s)"
( sleep $STOP_AFTER_S; docker stop atrium-judge-ollama > /dev/null ) &
judge --resume --request-timeout-s 120 --run-dir /results/a6s1_resume
echo "RESUME_PASS1 rc=$?"
wait
echo "progress lines after pass 1: $(wc -l < /home/denjo/atrium-data/enronqa/judge_raw/a6s1_resume.qwen3_8b.progress.jsonl)"
ls results/a6s1_resume
start_ollama
echo "[$(date +%T)] resume, second pass"
judge --resume --request-timeout-s 120 --run-dir /results/a6s1_resume
echo "RESUME_PASS2 rc=$?"
for f in judgements.jsonl candidate_judgements.jsonl; do
  if cmp -s results/a6s1_ref/$f results/a6s1_resume/$f; then echo "IDENTICAL $f"; else echo "DIFFERENT $f: $(diff results/a6s1_ref/$f results/a6s1_resume/$f | grep -c '^<')"; fi
done
docker rm -f atrium-judge-ollama > /dev/null 2>&1
echo "CHECK_DONE"
