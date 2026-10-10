#!/usr/bin/env bash
# EnronQA の実機の実行を順に行う（deploy → start → analyze）．config.yaml は各段の後と終了時に元へ戻す．
#   bash eq_run.sh <log> "<id> <run_id> key=value ..." ...
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO"
LOG=$1; shift
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
  id=${parts[0]}; run=${parts[1]}
  echo "[$(date +%F_%T)] BEGIN $id $run ${parts[*]:2}" >> "$LOG"
  setcfg "${parts[@]:2}"
  if ! mise run deploy > "$LOG.$id.deploy" 2>&1; then
    echo "[$(date +%T)] $id DEPLOY FAILED" >> "$LOG"; git checkout -- config.yaml; continue
  fi
  mise run start "$run" > "$LOG.$id.start" 2>&1; rc=$?
  # mise run の終了コードは内側の start.sh の失敗（exit 2 = 失敗した問が残る）を反映しないことがあるため，ログから拾う
  inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$id.start" | tail -1 | tr -dc '0-9')
  echo "[$(date +%T)] $id start=$rc inner=${inner:-?}" >> "$LOG"
  mise run analyze "$run" > "$LOG.$id.analyze" 2>&1; echo "[$(date +%T)] $id analyze=$?" >> "$LOG"
  grep -E '^\| (all|test) ' "$LOG.$id.analyze" | head -2 >> "$LOG"
  git checkout -- config.yaml
done
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
