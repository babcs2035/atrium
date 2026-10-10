#!/usr/bin/env bash
# s8 を，質問者の LLM を別の GPU（cluster.requester_llm）に分けた構成で，最初からやり直す（run_full.sh の s8 と同じ条件）
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
P=$1
cd "$REPO"
trap 'git show HEAD:config.yaml > "$REPO/config.yaml"' EXIT
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("config.yaml"); t = p.read_text()
for k, v in (("dataset", "medrag"), ("routing", "all"), ("answer_mode", "snippet_return"), ("merge", "cross_encoder"), ("question_limit", "null")):
    t, n = re.subn(rf"(?m)^(  {k}: ).*$", rf"\g<1>{v}", t, count=1); assert n == 1
p.write_text(t)
PY
id=s8_med_all_ce; run=20261007_$id
echo "[$(date +%F_%T)] BEGIN $id (rerun, requester_llm): medrag all snippet_return cross_encoder null" | tee -a "$P/progress.log"
mise run deploy > "$P/$id.rerun.deploy.log" 2>&1 || { echo "[$(date +%T)] DEPLOY FAILED $id" | tee -a "$P/progress.log"; echo -e "$id\tdeploy_failed" >> "$P/summary.tsv"; exit 1; }
mise run start "$run" > "$P/$id.rerun.start.log" 2>&1
code=$?
echo "[$(date +%T)] start exit=$code" | tee -a "$P/progress.log"
mise run analyze "$run" > "$P/$id.rerun.analyze.log" 2>&1
echo -e "$id\tstart=$code\tanalyze=$?" >> "$P/summary.tsv"
grep -E '^\| (all|test) ' "$P/$id.rerun.analyze.log" >> "$P/progress.log" || true
echo "[$(date +%F_%T)] ALL DONE" | tee -a "$P/progress.log"
