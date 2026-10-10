#!/usr/bin/env bash
# E1 の全問実行を順に行う（deploy → start → analyze）．各段の結果は FULL_DIR/summary.tsv に追記する
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
FULL_DIR=$1
cd "$REPO"
cp config.yaml "$FULL_DIR/config.yaml.orig"
trap 'cp "$FULL_DIR/config.yaml.orig" "$REPO/config.yaml"' EXIT

setcfg() { # dataset routing answer_mode merge question_limit
  python3 - "$@" <<'PY'
import re, sys, pathlib
dataset, routing, mode, merge, limit = sys.argv[1:6]
p = pathlib.Path("config.yaml"); t = p.read_text()
for key, val in (("dataset", dataset), ("routing", routing), ("answer_mode", mode), ("merge", merge), ("question_limit", limit)):
    t, n = re.subn(rf"(?m)^(  {key}: ).*$", rf"\g<1>{val}", t, count=1)
    assert n == 1, key
p.write_text(t)
PY
}

step() { # id dataset routing answer_mode merge question_limit
  local id=$1; shift
  local run="20261007_${id}"
  echo "[$(date +%F_%T)] BEGIN $id: $*" | tee -a "$FULL_DIR/progress.log"
  setcfg "$@"
  mise run deploy > "$FULL_DIR/$id.deploy.log" 2>&1 || { echo "[$(date +%T)] DEPLOY FAILED $id" | tee -a "$FULL_DIR/progress.log"; echo -e "$id\tdeploy_failed" >> "$FULL_DIR/summary.tsv"; return 1; }
  mise run start "$run" > "$FULL_DIR/$id.start.log" 2>&1
  local code=$?
  echo "[$(date +%T)] start exit=$code" | tee -a "$FULL_DIR/progress.log"
  mise run analyze "$run" > "$FULL_DIR/$id.analyze.log" 2>&1
  echo -e "$id\tstart=$code\tanalyze=$?" >> "$FULL_DIR/summary.tsv"
  grep -E '^\| (all|test) ' "$FULL_DIR/$id.analyze.log" >> "$FULL_DIR/progress.log" || true
}

step s1_feb_all      feb4rag all      retrieval_only qrels_oracle null
step s2_feb_ragroute feb4rag ragroute retrieval_only qrels_oracle null
step s3_med_all_ret  medrag  all      retrieval_only score null
step s4_med_all_snip medrag  all      snippet_return score null
step s5_med_ragroute medrag  ragroute snippet_return score null
step s6_med_random   medrag  random   snippet_return score null
step s7_med_none     medrag  none     snippet_return score null
step s8_med_all_ce   medrag  all      snippet_return cross_encoder null
echo "[$(date +%F_%T)] ALL DONE" | tee -a "$FULL_DIR/progress.log"
