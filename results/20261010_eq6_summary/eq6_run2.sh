#!/usr/bin/env bash
# EQ6（手順の変更後）：各回の前に deploy し直し，KV キャッシュが空の状態から測る（start → analyze）．
#   bash eq6_run2.sh <log> "<条件> key=value ..." ...
# results/<run_id> がある回は飛ばす．制御点で実行中の回は deploy せずに終わりを待つ（deploy するとその回を壊すため）．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
CONTROL=wafl-ctrl5
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
  # start.sh は config.yaml を制御点へ同期するので，実行中の回を待つときも同じ条件の設定にしておく
  setcfg "${parts[@]:1}"
  for rep in $(seq 1 "$REPEATS"); do
    run=20261010_eq6_${cond}_r$rep
    if [ -d "results/$run" ]; then
      echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; continue
    fi
    # [s] で囲むのは，ssh が起こす bash -c のコマンドライン自身に pgrep が一致しないようにするため
    if ssh "$CONTROL" "pgrep -f '[s]cripts/remote/start.sh $run ' > /dev/null"; then
      echo "[$(date +%T)] $run already running; waiting without deploy" >> "$LOG"
    elif ! mise run deploy > "$LOG.$run.deploy" 2>&1; then
      echo "[$(date +%T)] $run DEPLOY FAILED" >> "$LOG"; continue
    else
      echo "[$(date +%T)] $run deployed" >> "$LOG"
    fi
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
