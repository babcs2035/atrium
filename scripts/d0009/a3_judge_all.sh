#!/usr/bin/env bash
# d0009 A3 の 6 回分の採点を順に行う（使い方: bash scripts/d0009/a3_judge_all.sh．リポジトリの直下で）．
# 判定の Ollama は wafl500・501・503・505〜509 の container（各台の 127.0.0.1:11436）を，gpu4 の 12500 + 台番号の
# ポートへ ssh -L で転送したものを使う．gpu4 の GPU は他の研究者の処理と共有で遅いので使わない．
# 同じ (質問, 回答) は --share-progress で使い回す．
cd /home/ktakahashi/workspace/atrium || exit 1
J=artifacts/enronqa/judge_raw
ALL="p1 p2 p3 c1 c2 c3"
urls=()
for n in 500 501 503 505 506 507 508 509; do urls+=(--ollama-url "http://127.0.0.1:$((12000 + n))"); done
for r in $ALL; do
  share=()
  for o in $ALL; do
    [ "$o" != "$r" ] && share+=(--share-progress "$J/d0009_a3_$o.qwen3_8b.progress.jsonl")
  done
  extra=()
  case $r in p*) extra+=(--with-candidates) ;; esac
  until uv run atrium judge --data-dir artifacts "${urls[@]}" \
      --run-dir results/d0009_a3_$r --resume "${extra[@]}" "${share[@]}"; do
    rc=$?
    echo "judge $r exit $rc; retry in 60 s"
    [ "$rc" -ne 3 ] && exit "$rc"
    sleep 60
  done
  echo "JUDGED $r $(date +%s)"
done
echo "ALL JUDGED $(date +%s)"
