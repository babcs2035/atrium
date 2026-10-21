#!/usr/bin/env bash
# p0007 A5 の C1-RRF-CE（central_local・fusion rrf・merge cross_encoder）を，質問者の LLM を別の GPU PC に分けて流す．
#   bash ce_chain.sh <log> <specs>
# 再ランク（bge-reranker-v2-m3）と 8B の Ollama が制御点の RTX 3060（12 GB）を取り合うと，CUDA のメモリ不足で問が失敗する
# （動作確認 p0007_a5_c1_rrf_ce_smoke_dev_r2 で 150 問中 21 問）．docs/d0002 の cluster.requester_llm の手順どおり，
# 質問者の LLM を wafl509（192.168.15.109）で動かし，その PC を experts・gpu_workers から外す（役割は 1 つだけ）．
# central_local はノードに問い合わせないので，専門家が 29 台になっても結果は変わらない．
# 前後に `mise run stop` を流し，wafl509 の専門家のコンテナ（8B）と質問者の LLM のコンテナが同じ GPU に同時に載らないようにする．
# config.yaml の書き換えは a5_chain.sh の終了時の `git checkout -- config.yaml` で元に戻る．specs は 1 行だけにする．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO" || exit 1
LOG=$1
SPECS=$2
LLM_HOST=192.168.15.109
echo "[$(date +%F_%T)] ce_chain: stop before patching" >> "$LOG"
mise run stop > "$LOG.stop_before" 2>&1 < /dev/null
python3 - "$LLM_HOST" <<'PY'
import pathlib, sys
host = sys.argv[1]
p = pathlib.Path("config.yaml")
lines = p.read_text().split("\n")
entry = f'    - {{host: "{host}"}}'
removed = sum(1 for line in lines if line == entry)
if removed != 2:
    raise SystemExit(f"expected {host} in experts and gpu_workers, found {removed}")
lines = [line for line in lines if line != entry]
anchor = lines.index('  requester: {host: "wafl-ctrl5"}')
lines.insert(anchor + 1, f'  requester_llm: {{host: "{host}"}}')
p.write_text("\n".join(lines))
PY
echo "[$(date +%T)] ce_chain: requester_llm=$LLM_HOST, removed from experts and gpu_workers" >> "$LOG"
bash results/p0007_a5_live/a5_chain.sh "$LOG" "$SPECS"
echo "[$(date +%T)] ce_chain: stop after the run" >> "$LOG"
mise run stop > "$LOG.stop_after" 2>&1 < /dev/null
git checkout -- config.yaml
echo "[$(date +%F_%T)] ce_chain: done" >> "$LOG"
