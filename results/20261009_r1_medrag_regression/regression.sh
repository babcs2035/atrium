#!/usr/bin/env bash
# 退行ゲート R1（MedRAG）・R2（FeB4RAG）を実行する（p0004 §2.2）．config.yaml は終了時に元へ戻す
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO"
LOG=$1
trap 'git checkout -- config.yaml' EXIT
setcfg() {
  python3 - "$@" <<'PY'
import re, sys, pathlib
p = pathlib.Path("config.yaml"); t = p.read_text()
for kv in sys.argv[1:]:
    k, v = kv.split("=")
    t, n = re.subn(rf"(?m)^(  {k}: ).*$", rf"\g<1>{v}", t, count=1); assert n == 1, k
p.write_text(t)
PY
}
gate() { # id run_id key=value...
  local id=$1 run=$2; shift 2
  echo "[$(date +%T)] BEGIN $id" >> "$LOG"
  setcfg "$@"
  mise run deploy > "$LOG.$id.deploy" 2>&1 || { echo "[$(date +%T)] $id DEPLOY FAILED" >> "$LOG"; return 1; }
  mise run start "$run" > "$LOG.$id.start" 2>&1; echo "[$(date +%T)] $id start=$?" >> "$LOG"
  mise run analyze "$run" > "$LOG.$id.analyze" 2>&1; echo "[$(date +%T)] $id analyze=$?" >> "$LOG"
  git checkout -- config.yaml
}
gate R1 20261009_r1_medrag_regression dataset=medrag routing=all answer_mode=retrieval_only merge=score question_limit=2
gate R2 20261009_r2_feb4rag_regression dataset=feb4rag routing=ragroute answer_mode=retrieval_only merge=qrels_oracle question_limit=2
echo "[$(date +%T)] DONE" >> "$LOG"
