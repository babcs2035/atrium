#!/usr/bin/env bash
# p0007 D（負荷・エネルギー）の連続実行．a4_chain.sh と同じ形に，GPU の消費電力の記録と，同じ条件の中の設定の配り直しを足したもの．
#   bash d_chain.sh <log> <specs>
# specs の各行は "<run_id> <section>.<key>=<value> ..."（# で始まる行と空行は飛ばす．make_specs.py が作る）．
# - 条件（run_id から _p<parallel> を除いた名前）が替わる行の前に `mise run deploy`（ノードを作り直す）．同じ条件の中では
#   config.yaml だけを配り直す（scripts/remote/sync_config.sh．parallel・question_limit はノードが読まない）．
# - 各実行の前後に約 20 秒，GPU を持つ 11 台の消費電力を記録して（scripts/remote/energy_log.sh）アイドル電力を取る．
#   記録は results/<run_id>/energy/<host>.csv に回収する．
# results/<run_id> がある回は飛ばす．config.yaml は各回の後と終了時に HEAD の版へ戻す．実行の間は commit しない．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO" || exit 1
LOG=$1
SPECS=$2
IDLE_S=20
ENERGY_HOSTS="wafl-ctrl5 192.168.15.100 192.168.15.101 192.168.15.102 192.168.15.103 192.168.15.104 192.168.15.105 192.168.15.106 192.168.15.107 192.168.15.108 192.168.15.109"
eval "$(uv run --quiet python src/atrium/shell_config.py config.yaml)"
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
last_group=""
while read -r run kvs <&3; do
  [[ -z "$run" || "$run" == \#* ]] && continue
  read -r -a parts <<< "$kvs"
  if [ -f "results/$run/run_meta.json" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; continue
  fi
  # 条件と実行の番号だけにする（例: p0007_d_p2p_ref_p30_r1 → p0007_d_p2p_ref_r1）．bash の置換の最長一致は p2p と p2p_ref を同じ名前にするので sed を使う
  group=$(echo "$run" | sed -E 's/_p[0-9]+_([rc][0-9]+)$/_\1/')
  echo "[$(date +%F_%T)] BEGIN $run ${parts[*]}" >> "$LOG"
  if ! setcfg "${parts[@]}" 2>> "$LOG"; then
    echo "[$(date +%T)] $run SETCFG FAILED" >> "$LOG"; git checkout -- config.yaml; continue
  fi
  if [ "$group" != "$last_group" ]; then
    if ! retry "$LOG.$group.deploy" mise run deploy; then
      echo "[$(date +%T)] $run DEPLOY FAILED" >> "$LOG"; git checkout -- config.yaml; last_group=""; continue
    fi
    last_group=$group
    echo "[$(date +%T)] $run deployed ($group)" >> "$LOG"
  else
    if ! retry "$LOG.$run.sync" bash scripts/tasks/remote.sh sync_config; then
      echo "[$(date +%T)] $run SYNC FAILED" >> "$LOG"; git checkout -- config.yaml; last_group=""; continue
    fi
    echo "[$(date +%T)] $run config synced" >> "$LOG"
  fi
  # 他の利用者の GPU の使用（同じ GPU の PC を別の研究が使うことがある）を，実行の前後の snapshot で残す
  mkdir -p "results/$run"
  bash results/p0007_d_live/gpu_apps_snapshot.sh > "results/$run/gpu_apps_pre.txt" 2>&1 < /dev/null
  # shellcheck disable=SC2086
  bash scripts/tasks/remote.sh energy_log start "$run" $ENERGY_HOSTS > "$LOG.$run.energy_start" 2>&1 < /dev/null
  sleep "$IDLE_S"
  mise run start "$run" > "$LOG.$run.start" 2>&1 < /dev/null; rc=$?
  inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$run.start" | tail -1 | tr -dc '0-9')
  echo "[$(date +%T)] $run start=$rc inner=${inner:-?}" >> "$LOG"
  bash results/p0007_d_live/gpu_apps_snapshot.sh > "results/$run/gpu_apps_post.txt" 2>&1 < /dev/null
  sleep "$IDLE_S"
  # shellcheck disable=SC2086
  bash scripts/tasks/remote.sh energy_log stop "$run" $ENERGY_HOSTS > "$LOG.$run.energy_stop" 2>&1 < /dev/null
  mkdir -p "results/$run/energy"
  rsync -az "$CONTROL:$REMOTE_DIR/results/$run/energy/" "results/$run/energy/" >> "$LOG.$run.energy_stop" 2>&1
  echo "[$(date +%T)] $run energy fetched" >> "$LOG"
  git checkout -- config.yaml
done 3< "$SPECS"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
