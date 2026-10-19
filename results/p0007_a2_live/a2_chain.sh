#!/usr/bin/env bash
# p0007 A2 の連続実行（t4_chain.sh と同じ形）．各回の前に deploy し直し，start → analyze する．
#   bash a2_chain.sh <log> <specs>
# specs の各行は "<run_id> <section>.<key>=<value> ..."（# で始まる行と空行は飛ばす．section は experiment・answer・retrieval）．
# results/<run_id> がある回は飛ばす．config.yaml は各回の後と終了時に HEAD の版へ戻す．実行の間は commit しない．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO" || exit 1
LOG=$1
SPECS=$2
trap 'git checkout -- config.yaml' EXIT
setcfg() {
  python3 - "$@" <<'PY'
import re, sys, pathlib
p = pathlib.Path("config.yaml"); lines = p.read_text().split("\n")
for kv in sys.argv[1:]:
    path, v = kv.split("=", 1)
    section, key = path.split(".", 1)
    start = lines.index(f"{section}:")
    end = next((i for i in range(start + 1, len(lines)) if lines[i] and not lines[i].startswith((" ", "#"))), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"^  {key}: ", lines[i]):
            lines[i] = f"  {key}: {v}"
            break
    else:
        raise SystemExit(f"key not found: {path}")
p.write_text("\n".join(lines))
PY
}
retry() {
  local out=$1 i
  shift
  for i in 1 2 3; do
    "$@" > "$out" 2>&1 < /dev/null && return 0
    if [ "$i" -lt 3 ]; then
      echo "[$(date +%T)] attempt $i failed: $(basename "$out"); retrying in 300s" >> "$LOG"
      sleep 300
    fi
  done
  return 1
}
while read -r run kvs <&3; do
  [[ -z "$run" || "$run" == \#* ]] && continue
  read -r -a parts <<< "$kvs"
  if [ -d "results/$run" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; continue
  fi
  echo "[$(date +%F_%T)] BEGIN $run ${parts[*]}" >> "$LOG"
  if ! setcfg "${parts[@]}" 2>> "$LOG"; then
    echo "[$(date +%T)] $run SETCFG FAILED" >> "$LOG"; git checkout -- config.yaml; continue
  fi
  if ! retry "$LOG.$run.deploy" mise run deploy; then
    echo "[$(date +%T)] $run DEPLOY FAILED" >> "$LOG"; git checkout -- config.yaml; continue
  fi
  echo "[$(date +%T)] $run deployed" >> "$LOG"
  mise run start "$run" > "$LOG.$run.start" 2>&1 < /dev/null; rc=$?
  inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$run.start" | tail -1 | tr -dc '0-9')
  echo "[$(date +%T)] $run start=$rc inner=${inner:-?}" >> "$LOG"
  mise run analyze "$run" > "$LOG.$run.analyze" 2>&1 < /dev/null; echo "[$(date +%T)] $run analyze=$?" >> "$LOG"
  git checkout -- config.yaml
done 3< "$SPECS"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
