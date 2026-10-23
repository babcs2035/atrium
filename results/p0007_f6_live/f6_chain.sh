#!/usr/bin/env bash
# p0007 F-6（cross-encoder を実機の経路に載せた費用の測定）の連続実行．d_chain.sh から，エネルギーの記録を除き，制御点の GPU の使用メモリの記録を足し，
# deploy を最初の 1 回だけにしたもの．答えの統合（answer.aggregate）と save_candidates は質問者だけが読み，ノードは読まないので
# （src/atrium/node.py は cfg.answer を使わない），2 回目以降は config.yaml の配り直し（sync_config）だけにする．
#   bash f6_chain.sh <log> <specs>
# specs の各行は "<run_id> <section>.<key>=<value> ..."（make_specs.py が作る）．results/<run_id>/run_meta.json がある回は飛ばす．
# 実行の前後に，GPU の PC の他のプロセスの使用状況（gpu_apps_snapshot.sh）を残す．config.yaml は各回の後と終了時に HEAD の版へ戻す．実行の間は commit しない．
# start が失敗したら（終了コードが 0 でない・結果が無い），ノードを止めずにそこで終わる（原因を調べて，同じ specs で再開できる）．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO" || exit 1
LOG=$1
SPECS=$2
D=results/p0007_f6_live
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
deployed=0
while read -r run kvs <&3; do
  [[ -z "$run" || "$run" == \#* ]] && continue
  read -r -a parts <<< "$kvs"
  if [ -f "results/$run/run_meta.json" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; continue
  fi
  echo "[$(date +%F_%T)] BEGIN $run ${parts[*]}" >> "$LOG"
  if ! setcfg "${parts[@]}" 2>> "$LOG"; then
    echo "[$(date +%T)] $run SETCFG FAILED" >> "$LOG"; exit 1
  fi
  if [ "$deployed" = 0 ]; then
    if ! retry "$LOG.deploy" mise run deploy; then
      echo "[$(date +%T)] $run DEPLOY FAILED" >> "$LOG"; exit 1
    fi
    deployed=1
    echo "[$(date +%T)] $run deployed" >> "$LOG"
  else
    if ! retry "$LOG.$run.sync" bash scripts/tasks/remote.sh sync_config; then
      echo "[$(date +%T)] $run SYNC FAILED" >> "$LOG"; exit 1
    fi
    echo "[$(date +%T)] $run config synced" >> "$LOG"
  fi
  mkdir -p "results/$run"
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_pre.txt" 2>&1 < /dev/null
  bash $D/gpu_mem_sampler.sh start "$run" > "$LOG.$run.gpu_mem_start" 2>&1 < /dev/null
  mise run start "$run" > "$LOG.$run.start" 2>&1 < /dev/null; rc=$?
  inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$run.start" | tail -1 | tr -dc '0-9')
  bash $D/gpu_mem_sampler.sh stop "$run" > "$LOG.$run.gpu_mem_stop" 2>&1 < /dev/null
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_post.txt" 2>&1 < /dev/null
  echo "[$(date +%T)] $run start=$rc inner=${inner:-?}" >> "$LOG"
  if [ "$rc" != 0 ] || [ ! -f "results/$run/run_meta.json" ]; then
    echo "[$(date +%T)] $run START FAILED (nodes are left running; inspect $LOG.$run.start)" >> "$LOG"; exit 1
  fi
  mise run analyze "$run" > "$LOG.$run.analyze" 2>&1 < /dev/null; echo "[$(date +%T)] $run analyze=$?" >> "$LOG"
  git checkout -- config.yaml
done 3< "$SPECS"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
