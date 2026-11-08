#!/usr/bin/env bash
# d0009 M1（Iteration 26）：判定モデル J1（Qwen3 14B Q8_0）を gpu4 の判定の container に取り込み，G-L・G-V を確かめる．
#
# 使い方（リポジトリの直下．gpu4 で `sg docker -c "bash scripts/d0009/m1_import_judge.sh"`）:
#   1. `hf download`（既定のキャッシュ．運用ルール 15．revision を固定）で GGUF を取得し，sha256 を記録する．
#   2. container atrium-judge-ollama-local（Ollama 0.35.1．127.0.0.1:11436．volume atrium-judge-ollama）を，GPU 0 だけに繋いで起動する．
#      この端末の docker には nvidia runtime が無いので，デバイスとホストのドライバのライブラリを読み取り専用で mount する（backlog B-A2 の方式）．
#      キャッシュの GGUF も読み取り専用で mount し，container 内の `ollama create` は volume への取り込みだけを行う（運用ルール 15 が許す取り込み）．
#   3. `ollama show`・think:false の 3 問の smoke（G-L）と，`ollama ps`（G-V．num_ctx 16384・並列 2）を results/d0009_m1_generator/ へ書く．
# 他の研究者の GPU プロセスには触れない．既に container があれば作り直さず，モデルだけを足す（qwen3:8b も volume に残る）．
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
OUT=results/d0009_m1_generator
REPO_ID="Qwen/Qwen3-14B-GGUF"
GGUF_FILE="Qwen3-14B-Q8_0.gguf"
REVISION="530227a7d994db8eca5ab5ced2fb692b614357fd"
MODEL="qwen3:14b-q8_0"
NAME=atrium-judge-ollama-local
IMAGE="ollama/ollama:0.35.1"
PORT=11436
NUM_CTX=16384
mkdir -p "$OUT"

hf download "$REPO_ID" "$GGUF_FILE" --revision "$REVISION" > "$OUT/hf_download_j1.log" 2>&1
BLOB=$(readlink -f "$HOME/.cache/huggingface/hub/models--Qwen--Qwen3-14B-GGUF/snapshots/$REVISION/$GGUF_FILE")
echo "{\"repo\": \"$REPO_ID\", \"file\": \"$GGUF_FILE\", \"revision\": \"$REVISION\", \"sha256\": \"$(sha256sum "$BLOB" | cut -c1-64)\", \"bytes\": $(stat -c %s "$BLOB"), \"downloaded_at\": \"$(date -Iseconds)\"}" > "$OUT/j1_gguf.json"

docker rm -f "$NAME" > /dev/null 2>&1 || true
LIBDIR=/usr/lib/x86_64-linux-gnu
DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:11434" \
  --device /dev/nvidia0 --device /dev/nvidiactl --device /dev/nvidia-uvm \
  -v "$LIBDIR/libcuda.so.$DRIVER_VERSION:/usr/lib/x86_64-linux-gnu/libcuda.so.1:ro" \
  -v "$LIBDIR/libnvidia-ml.so.$DRIVER_VERSION:/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1:ro" \
  -v "$LIBDIR/libnvidia-ptxjitcompiler.so.$DRIVER_VERSION:/usr/lib/x86_64-linux-gnu/libnvidia-ptxjitcompiler.so.1:ro" \
  -e OLLAMA_NUM_PARALLEL=2 -e OLLAMA_VULKAN=0 -e OLLAMA_KEEP_ALIVE=-1 \
  -v atrium-judge-ollama:/root/.ollama -v "$BLOB:/gguf/$GGUF_FILE:ro" "$IMAGE" > /dev/null
until curl -s "localhost:$PORT/api/version" > /dev/null; do sleep 2; done
docker exec "$NAME" sh -c "printf 'FROM /gguf/$GGUF_FILE\n' > /tmp/Modelfile && ollama create $MODEL -f /tmp/Modelfile" | tail -1
docker exec "$NAME" ollama show "$MODEL" > "$OUT/j1_ollama_show.txt"

# G-L：think:false の 3 問の smoke（思考の文・空の出力が無いか）
for q in "What is the capital of France?" \
  "Summarize in one sentence: The meeting is moved from Monday to Wednesday at 3pm in room 12." \
  "Answer using only the email. Email: 'Please send the Q3 report to Dana by Friday.' Question: Who should receive the Q3 report, and by when?"; do
  curl -s "localhost:$PORT/api/chat" -d "$(jq -n --arg m "$MODEL" --arg q "$q" --argjson c "$NUM_CTX" \
    '{model: $m, messages: [{role: "user", content: $q}], stream: false, think: false, options: {num_ctx: $c, temperature: 0, num_predict: 512}}')" \
    | jq '{q: $q, content: .message.content, thinking: .message.thinking, eval_count}' --arg q "$q"
done > "$OUT/smoke_j1.json"
# G-V：num_ctx 16384・並列 2 で 100% GPU か
curl -s "localhost:$PORT/api/generate" -d "{\"model\": \"$MODEL\", \"keep_alive\": -1, \"options\": {\"num_ctx\": $NUM_CTX}}" > /dev/null
docker exec "$NAME" ollama ps > "$OUT/j1_ollama_ps.txt"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv > "$OUT/j1_nvidia_smi.txt"
cat "$OUT/j1_gguf.json" "$OUT/j1_ollama_ps.txt" "$OUT/smoke_j1.json"
