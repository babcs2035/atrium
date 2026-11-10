#!/usr/bin/env bash
# d0009 B3（Iteration 41・42）：両腕（<B3IC_ARM_PREFIX>{base,new}）の採点・検証と，V2 の選択・作り話の判定・集計（wafl の Ollama．gpu4 の GPU は使わない）．
# 環境変数（省略時は Iteration 41 の値）．Iteration 42 の例：
#   B3IC_HOSTS="wafl500 ... wafl509" B3IC_ARM_PREFIX=results/d0009_b3ic42_q_ B3IC_OUT=results/d0009_b3_inbox_context_bm25 \
#   B3IC_PLAN_DIR=results/d0009_b3ic42_plan B3IC_SEED=42 B3IC_LABEL="Iteration 42" B3IC_HYPOTHESIS=H-IC42 \
#   B3IC_COMPARE_PREFIX=results/d0009_b3ic_q_ B3IC_COMPARE_PLAN_DIR=results/d0009_b3ic_plan bash scripts/d0009/b3ic_post.sh <段階>
# B3IC_ARM_PREFIX・B3IC_OUT・B3IC_PLAN_DIR・B3IC_SEED・B3IC_LABEL・B3IC_HYPOTHESIS・B3IC_COMPARE_* は b3ic_verdict.py も読む（export する）．
#   bash scripts/d0009/b3ic_post.sh judge    # qwen3:8b で候補・最終回答の正誤（同じ (質問，回答) は保存した進捗から使い回し，新しい組だけ送る）
#   bash scripts/d0009/b3ic_post.sh verify   # gemma4-12b-it-qat で作り直した候補の検証の得点 v（文脈・回答が保存値と同じ候補は種として写し済み）
#   bash scripts/d0009/b3ic_post.sh derive   # V2・R3′ の選んだ最終回答の派生の実行（b3ic_verdict.py derive）
#   bash scripts/d0009/b3ic_post.sh abstain  # 派生の V2 の不正解の回答の棄権の判定（qwen3:8b）
#   bash scripts/d0009/b3ic_post.sh report   # b3ic_verdict.py report（verdict.json・inbox_context.json・analysis_report.md）
# 各段階は b3ic_hosts.sh で Ollama を起動して SSH の転送を開いてから流す（judge・abstain は qwen3:8b，verify は gemma4-12b-it-qat を載せる）．
# 失敗したら 60 秒おきに繰り返す（resume なので続きから）．
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
STAGE=${1:?usage: b3ic_post.sh judge|verify|derive|abstain|report}
HOSTS=scripts/d0009/b3ic_hosts.sh
export B3IC_ARM_PREFIX=${B3IC_ARM_PREFIX:-results/d0009_b3ic_q_}
export B3IC_OUT=${B3IC_OUT:-results/d0009_b3_inbox_context}
export B3IC_PLAN_DIR=${B3IC_PLAN_DIR:-results/d0009_b3ic_plan}
ARM_PREFIX=$B3IC_ARM_PREFIX
OUT=$B3IC_OUT
OLLAMA_URLS=$(bash $HOSTS urls ollama)
JUDGE_MODEL=qwen3:8b
VERIFY_MODEL=gemma4-12b-it-qat
RETRY_WAIT_S=60

case "$STAGE" in
  judge)
    share=()
    for p in artifacts/enronqa/judge_raw/*.qwen3_8b.progress.jsonl; do share+=(--share-progress "$p"); done
    for arm in base new; do
      until uv run atrium judge --data-dir artifacts $OLLAMA_URLS --run-dir ${ARM_PREFIX}$arm --resume --with-candidates "${share[@]}"; do
        rc=$?
        echo "judge $arm exit $rc; retry in ${RETRY_WAIT_S}s"
        [ "$rc" -ne 3 ] && exit "$rc"
        sleep "$RETRY_WAIT_S"
      done
    done
    ;;
  verify)
    for arm in base new; do
      until uv run atrium verify-features --run-dir ${ARM_PREFIX}$arm --out-dir ${ARM_PREFIX}$arm --data-dir artifacts \
        --model "$VERIFY_MODEL" ${OLLAMA_URLS//--ollama-url/--url} --resume > "${ARM_PREFIX}$arm/verify.log" 2>&1; do
        echo "verify-features $arm failed; retry in ${RETRY_WAIT_S}s ($(date +%s))"
        sleep "$RETRY_WAIT_S"
      done
      tail -1 "${ARM_PREFIX}$arm/verify.log"
    done
    ;;
  derive)
    uv run python scripts/d0009/b3ic_verdict.py derive
    ;;
  abstain)
    until uv run atrium abstain-judge --data-dir artifacts $OLLAMA_URLS --run-dir ${ARM_PREFIX}base_V2 --run-dir ${ARM_PREFIX}new_V2 \
      --out-dir $OUT/labels --resume; do
      rc=$?
      echo "abstain-judge exit $rc; retry in ${RETRY_WAIT_S}s"
      [ "$rc" -ne 3 ] && exit "$rc"
      sleep "$RETRY_WAIT_S"
    done
    ;;
  report)
    uv run python scripts/d0009/b3ic_verdict.py report
    ;;
  *)
    echo "unknown stage $STAGE" >&2
    exit 2
    ;;
esac
