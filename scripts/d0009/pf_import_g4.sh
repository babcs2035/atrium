#!/usr/bin/env bash
# d0009 Iteration 27 の診断：Gemma 4 12B（G4）を gpu4 の判定の container に取り込み，プロンプトの形の診断（pf_diag.py）に使えるようにする．
#
# 使い方（リポジトリの直下．gpu4 で `sg docker -c "bash scripts/d0009/pf_import_g4.sh"`）:
#   container atrium-judge-ollama-local（Ollama 0.35.1．127.0.0.1:11436．volume atrium-judge-ollama）を，
#   J1 と G4 の GGUF（hf の既定のキャッシュ．運用ルール 15）を読み取り専用で mount して作り直し，G4 を `ollama create` で volume へ取り込む．
#   volume の既存のモデル（qwen3:8b・qwen3:14b-q8_0）は残る．他の研究者の GPU プロセスには触れない．
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
NAME=atrium-judge-ollama-local
IMAGE="ollama/ollama:0.35.1"
PORT=11436
HUB="$HOME/.cache/huggingface/hub"
J1_BLOB=$(readlink -f "$HUB/models--Qwen--Qwen3-14B-GGUF/snapshots/530227a7d994db8eca5ab5ced2fb692b614357fd/Qwen3-14B-Q8_0.gguf")
G4_BLOB=$(readlink -f "$HUB"/models--google--gemma-4-12B-it-qat-q4_0-gguf/snapshots/*/gemma-4-12b-it-qat-q4_0.gguf)
G4_MODEL="gemma4-12b-it-qat"

docker rm -f "$NAME" > /dev/null 2>&1 || true
LIBDIR=/usr/lib/x86_64-linux-gnu
DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:11434" \
  --device /dev/nvidia0 --device /dev/nvidiactl --device /dev/nvidia-uvm \
  -v "$LIBDIR/libcuda.so.$DRIVER_VERSION:/usr/lib/x86_64-linux-gnu/libcuda.so.1:ro" \
  -v "$LIBDIR/libnvidia-ml.so.$DRIVER_VERSION:/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1:ro" \
  -v "$LIBDIR/libnvidia-ptxjitcompiler.so.$DRIVER_VERSION:/usr/lib/x86_64-linux-gnu/libnvidia-ptxjitcompiler.so.1:ro" \
  -e OLLAMA_NUM_PARALLEL=2 -e OLLAMA_VULKAN=0 -e OLLAMA_KEEP_ALIVE=-1 \
  -v atrium-judge-ollama:/root/.ollama \
  -v "$J1_BLOB:/gguf/Qwen3-14B-Q8_0.gguf:ro" \
  -v "$G4_BLOB:/gguf/gemma-4-12b-it-qat-q4_0.gguf:ro" "$IMAGE" > /dev/null
until curl -s "localhost:$PORT/api/version" > /dev/null; do sleep 2; done
docker exec "$NAME" sh -c "printf 'FROM /gguf/gemma-4-12b-it-qat-q4_0.gguf\n' > /tmp/Modelfile && ollama create $G4_MODEL -f /tmp/Modelfile" | tail -1
docker exec "$NAME" ollama show --modelfile "$G4_MODEL" | grep -v '^#' | grep -E 'PARAMETER|FROM' || true
# G-M の記録の欠け（B98 (e)）を補う：G4 の GGUF の revision と sha256
echo "g4_revision $(ls "$HUB"/models--google--gemma-4-12B-it-qat-q4_0-gguf/snapshots/) g4_sha256 $(sha256sum "$G4_BLOB" | cut -c1-64)"
