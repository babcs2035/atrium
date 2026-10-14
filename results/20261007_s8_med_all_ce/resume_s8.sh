#!/usr/bin/env bash
# p0003 s8 の再開：MedRAG・routing=all・snippet_return・cross_encoder の全問．同じ run_id（制御点に 1,191 問が残っている）で再開する．
# 質問者の LLM は wafl500（cluster.requester_llm）に分ける（p0004 §14）．config.yaml は終了時に必ず元へ戻す．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
LOG=/home/ktakahashi/.claude/jobs/0b6384d1/tmp/s8_run.log
RUN=20261007_s8_med_all_ce
cd "$REPO" || exit 1
trap 'git checkout -- config.yaml; echo "[$(date +%F_%T)] EXIT" >> "$LOG"' EXIT
echo "[$(date +%F_%T)] BEGIN s8 resume" >> "$LOG"

python3 - <<'PY'
import re, pathlib
p = pathlib.Path("config.yaml"); t = p.read_text()
for k, v in (("answer_mode", "snippet_return"), ("merge", "cross_encoder")):
    t, n = re.subn(rf"(?m)^(  {k}: ).*$", rf"\g<1>{v}", t, count=1)
    assert n == 1, k
t, n = re.subn(r'(?m)^(  requester: \{host: "wafl-ctrl5"\}\n)', r'\1  requester_llm: {host: "192.168.15.100"}\n', t, count=1)
assert n == 1, "requester_llm"
# wafl500 は質問者の LLM に専念させる（役割は 1 つだけ）．MedRAG は専門家の先頭 16 台しか使わないので，
# experts と gpu_workers から外しても配置は変わらない
t, n = re.subn(r'(?m)^    - \{host: "192\.168\.15\.100"\}\n', "", t)
assert n == 2, n
p.write_text(t)
PY
git diff --stat >> "$LOG"

mise run deploy > "$LOG.deploy" 2>&1 < /dev/null; rc=$?
echo "[$(date +%T)] deploy=$rc" >> "$LOG"
[ "$rc" = 0 ] || exit 4
# start は，失敗した問があると exit 2 を返す．同じ run_id で再実行すると，残りと失敗した問だけを流す
for attempt in 1 2 3; do
  mise run start "$RUN" > "$LOG.start$attempt" 2>&1 < /dev/null; rc=$?
  echo "[$(date +%T)] start attempt $attempt rc=$rc" >> "$LOG"
  [ "$rc" = 0 ] && break
done
mise run analyze "$RUN" > "$LOG.analyze" 2>&1 < /dev/null
echo "[$(date +%T)] analyze=$?" >> "$LOG"
mise run stop > "$LOG.stop" 2>&1 < /dev/null
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
