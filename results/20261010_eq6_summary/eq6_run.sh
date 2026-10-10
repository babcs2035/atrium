#!/usr/bin/env bash
# EQ6：条件ごとに 1 回 deploy し，同じ設定で 3 回続けて実行する（start → analyze）．config.yaml は各条件の後と終了時に元へ戻す．
#   bash eq6_run.sh <log> "<条件> key=value ..." ...
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO"
LOG=$1; shift
REPEATS=3
trap 'git checkout -- config.yaml' EXIT
setcfg() {
  python3 - "$@" <<'PY'
import re, sys, pathlib
p = pathlib.Path("config.yaml"); t = p.read_text()
for kv in sys.argv[1:]:
    k, v = kv.split("=", 1)
    t, n = re.subn(rf"(?m)^(  {k}: ).*$", rf"\g<1>{v}", t, count=1); assert n == 1, k
p.write_text(t)
PY
}
for spec in "$@"; do
  read -r -a parts <<< "$spec"
  cond=${parts[0]}
  echo "[$(date +%F_%T)] BEGIN $cond ${parts[*]:1}" >> "$LOG"
  setcfg "${parts[@]:1}"
  if ! mise run deploy > "$LOG.$cond.deploy" 2>&1; then
    echo "[$(date +%T)] $cond DEPLOY FAILED" >> "$LOG"; git checkout -- config.yaml; continue
  fi
  echo "[$(date +%T)] $cond deployed" >> "$LOG"
  for rep in $(seq 1 "$REPEATS"); do
    run=20261010_eq6_${cond}_r$rep
    mise run start "$run" > "$LOG.$run.start" 2>&1; rc=$?
    # mise run の終了コードは内側の start.sh の失敗（exit 2 = 失敗した問が残る）を反映しないことがあるため，ログから拾う
    inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$run.start" | tail -1 | tr -dc '0-9')
    echo "[$(date +%T)] $run start=$rc inner=${inner:-?}" >> "$LOG"
    mise run analyze "$run" > "$LOG.$run.analyze" 2>&1; echo "[$(date +%T)] $run analyze=$?" >> "$LOG"
    grep -E 'e2e' "$LOG.$run.analyze" | head -2 >> "$LOG"
  done
  git checkout -- config.yaml
done
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
