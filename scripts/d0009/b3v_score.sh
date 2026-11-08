#!/usr/bin/env bash
# d0009 B3（Iteration 28）：保存した候補を検証の LLM（gemma4-12b-it-qat．gpu4 の判定 container．127.0.0.1:11436）で確かめ，V1・V2 の選択を評価する連続実行（実機は使わない）．
#   bash scripts/d0009/b3v_score.sh dev    # GPU の記録 → dev の検証の得点 → G-R・G-V・G-D（不通過なら棄却の verdict.json）．通過したらここで止まる（終了コード 10）
#   bash scripts/d0009/b3v_score.sh hh2    # G-D′ の commit・push の後．H・H2 の検証の得点（H2 の候補の採点を含む）→ 選んだ規則の当てはめ（Q）
# 途中で止まっても同じコマンドで再開できる（verify-features は --resume，judge も --resume）．他の研究者のプロセスには触れない（nvidia-smi・ollama ps の記録だけ）．
# 検証の得点（verify_features.jsonl）に回答・メールの本文は含まれない．H2 の写し（results/d0009_b3v_h2/）の results.jsonl は回答を含むので，
# commit の前に scripts/tasks/sanitize_results.py を通す．
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
STAGE=${1:?usage: b3v_score.sh dev|hh2}
URL=http://127.0.0.1:11436
MODEL=gemma4-12b-it-qat
OUT=results/d0009_b3_verify
mkdir -p "$OUT"

snapshot() {
  { date +%FT%T; nvidia-smi; echo "--- compute apps"; nvidia-smi --query-compute-apps=pid,used_memory --format=csv
    echo "--- ollama ps ($URL)"; curl -s "$URL/api/ps" | python3 -c "import json,sys;[print(m['name'], m['size_vram']) for m in json.load(sys.stdin)['models']]"; } > "$1" 2>&1
}

# 検証の得点を求める．同じ入力は 1 回だけ（--resume）．失敗したら 120 秒おきに繰り返す（Ollama の一時的な停止）
verify() {
  local run=$1 out=$2
  until uv run atrium verify-features --run-dir "$run" --out-dir "$out" --data-dir artifacts --model "$MODEL" --url "$URL" --resume > "$out.log" 2>&1; do
    echo "verify-features $run failed; retry in 120 s ($(date +%s))"; sleep 120
  done
  tail -1 "$out.log"
}

if [ "$STAGE" = dev ]; then
  [ -f "$OUT/gpu_pre.txt" ] || snapshot "$OUT/gpu_pre.txt"
  mkdir -p results/d0009_b3v_dev
  verify results/d0009_b3s_dev results/d0009_b3v_dev
  uv run atrium answer-select-eval --stage dev --verify-features results/d0009_b3v_dev/verify_features.jsonl --out-dir "$OUT" > "$OUT/verify_dev.stdout" 2>&1 || exit 1
  snapshot "$OUT/gpu_post_dev.txt"
  uv run python scripts/d0009/b3v_verdict.py dev
  exit $?
fi

# hh2：H（d0009_a3_p1．候補は採点済み）と H2（d0009_m1_h2_base の写し．候補を採点する）の検証の得点 → 選んだ規則の当てはめ
snapshot "$OUT/gpu_pre_hh2.txt"
H2=results/d0009_b3v_h2
mkdir -p results/d0009_b3v_h "$H2"
# H2 の写し：results.jsonl・ce_features.jsonl だけ（元の judgements.jsonl を再採点で書き換えないため．元は変えない）
for f in results.jsonl ce_features.jsonl; do [ -f "$H2/$f" ] || cp "results/d0009_m1_h2_base/$f" "$H2/$f"; done
share=()
for p in artifacts/enronqa/judge_raw/*.qwen3_8b.progress.jsonl; do share+=(--share-progress "$p"); done
until uv run atrium judge --data-dir artifacts --ollama-url "$URL" --run-dir "$H2" --resume --with-candidates "${share[@]}"; do
  rc=$?; echo "judge exit $rc; retry in 60 s"; [ "$rc" -ne 3 ] && exit "$rc"; sleep 60
done
verify results/d0009_a3_p1 results/d0009_b3v_h
verify "$H2" "$H2"
snapshot "$OUT/gpu_post_hh2.txt"
uv run atrium answer-select-eval --stage apply --out-dir "$OUT" \
  --q-run results/d0009_a3_p1 --q-run "$H2" \
  --verify-features results/d0009_b3v_h/verify_features.jsonl --verify-features "$H2/verify_features.jsonl" \
  > "$OUT/apply.stdout" 2>&1 || exit 1
echo "APPLIED $(date +%s)"
