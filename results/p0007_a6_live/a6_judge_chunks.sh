#!/usr/bin/env bash
# p0007 A6：全候補の採点を，結果の行を N 個に分けて行う（分けた単位ごとに判定モデルの Ollama を起動し直し，落ちた単位は 1 回だけ流し直す）．
# dev の採点で，制御点の判定モデルの Ollama が 6,800/8,516 件の時点で応答しなくなり（A5 と同じ現象），`atrium judge` は途中の結果を
# 残さないため．採点のモデル・設定・プロンプトは a6_judge_ctrl.sh（scripts/remote/judge.sh の制御点の部分）と同じで，同じ質問の同じ回答を
# 1 回だけ採点する規則も質問の単位なので，行を分けても採点する項目は変わらない．
#   ssh wafl-ctrl5 bash /home/denjo/workspace/ktakahashi/atrium/a6_judge_chunks.sh <run_id> [N]
# 出力は results/<run_id>/judgements.jsonl・candidate_judgements.jsonl（元の行の順）．分けた単位の一時の置き場所は最後に消す．
set -uo pipefail
RUN=${1:?usage: a6_judge_chunks.sh <run_id> [N]}
N=${2:-6}
cd /home/denjo/workspace/ktakahashi/atrium || exit 1
IMAGE=localhost:5000/mirror/ollama:0.35.1
FULL=localhost:5000/atrium-full:latest
python3 - "$RUN" "$N" <<'PY'
import pathlib, sys
run, n = sys.argv[1], int(sys.argv[2])
lines = pathlib.Path(f"results/{run}/results.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
size = -(-len(lines) // n)
for k in range(n):
    part = pathlib.Path(f"results/{run}__part{k}")
    part.mkdir(exist_ok=True)
    (part / "results.jsonl").write_text("".join(lines[k * size:(k + 1) * size]), encoding="utf-8")
print(f"split {len(lines)} rows into {n} parts of {size}")
PY
judge_part() {
  local part=$1
  docker compose stop ollama > /dev/null 2>&1 || true
  docker rm -f atrium-judge-ollama > /dev/null 2>&1 || true
  docker run -d --name atrium-judge-ollama --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user $(id -u):$(id -g) -e HOME=/ollama -e OLLAMA_MODELS=/ollama/models -e OLLAMA_KEEP_ALIVE=-1 -e OLLAMA_NUM_PARALLEL=2 -p 127.0.0.1:11435:11434 -v /home/denjo/atrium-data/ollama:/ollama $IMAGE > /dev/null
  for _ in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:11435/api/tags && break; sleep 2; done
  curl -fsS -o /dev/null http://127.0.0.1:11435/api/generate -d '{"model": "qwen3:8b", "keep_alive": -1, "options": {"num_ctx": 16384}}'
  docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -v /home/denjo/atrium-data:/data -v $PWD/results:/results -v $PWD/config.yaml:/app/config.yaml:ro $FULL atrium --config /app/config.yaml judge --data-dir /data --ollama-url http://127.0.0.1:11435 --run-dir /results/$part --with-candidates
  local rc=$?
  docker logs atrium-judge-ollama > results/$part.judge_ctrl.ollama.log 2>&1
  docker rm -f atrium-judge-ollama > /dev/null 2>&1
  return $rc
}
ok=1
for k in $(seq 0 $((N - 1))); do
  part=${RUN}__part$k
  if judge_part "$part"; then echo "PART $k rc=0"; continue; fi
  echo "PART $k failed; retrying once"
  if judge_part "$part"; then echo "PART $k rc=0 (retry)"; else echo "PART $k FAILED"; ok=0; fi
done
if [ "$ok" = 1 ]; then
  for name in judgements.jsonl candidate_judgements.jsonl; do
    : > results/$RUN/$name
    for k in $(seq 0 $((N - 1))); do cat results/${RUN}__part$k/$name >> results/$RUN/$name; done
  done
  for k in $(seq 0 $((N - 1))); do rm -rf "results/${RUN}__part$k"; done
  echo "JUDGE_CHUNKS_DONE rc=0 $(wc -l < results/$RUN/judgements.jsonl) $(wc -l < results/$RUN/candidate_judgements.jsonl)"
else
  echo "JUDGE_CHUNKS_DONE rc=1"
fi
