#!/usr/bin/env bash
# p0005 T4：負荷試験の連続実行．各回の前に deploy し直し（KV キャッシュ・接続を空にする），start → analyze する．
#   bash t4_chain.sh <log> <specs>
# specs の各行は "<run_id> key=value ..."（# で始まる行と空行は飛ばす）．キーは experiment の直下のキー．
# 待ち時間の測定なので，失敗した問は流し直さない（EQ10 と同じ）．同じ routing・parallel の 3 回とも失敗率が 10% を超えたら，
# その routing の，より大きい parallel は飛ばして記録する（p0005 §6.4）．results/<run_id> がある回は飛ばす．
# config.yaml は各回の後と終了時に元へ戻す．eq8_chain.sh（EQ8〜EQ10）と同じ形．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO" || exit 1
LOG=$1
SPECS=$2
FAIL_RATE_LIMIT=0.10
trap 'git checkout -- config.yaml' EXIT
setcfg() {
  python3 - "$@" <<'PY'
import re, sys, pathlib
p = pathlib.Path("config.yaml"); t = p.read_text()
for kv in sys.argv[1:]:
    k, v = kv.split("=", 1)
    t, n = re.subn(rf"(?m)^(  {k}: ).*$", rf"\g<1>{v}", t, count=1)
    assert n == 1, k
p.write_text(t)
PY
}
# retry <out> <cmd...>：一時的なネットワーク障害で残りの回を失わないよう，5 分おいて 3 回まで試す
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
# fail_rate <run_id>：results.jsonl の（最後の行を採った）失敗率
fail_rate() {
  python3 - "results/$1/results.jsonl" <<'PY'
import json, sys
rows = {}
for line in open(sys.argv[1]):
    r = json.loads(line)
    rows[r["qid"]] = r
print(sum(r["error"] is not None for r in rows.values()) / max(1, len(rows)))
PY
}
declare -A over_limit   # "<routing>_p<parallel>" → 失敗率が上限を超えた回の数
declare -A skip_above   # routing → この parallel より大きい条件は飛ばす
while read -r run kvs <&3; do
  [[ -z "$run" || "$run" == \#* ]] && continue
  read -r -a parts <<< "$kvs"
  routing=""; parallel=""
  for kv in "${parts[@]}"; do
    case "$kv" in routing=*) routing=${kv#routing=} ;; parallel=*) parallel=${kv#parallel=} ;; esac
  done
  if [ -d "results/$run" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; continue
  fi
  if [ -n "${skip_above[$routing]:-}" ] && [ "$parallel" -gt "${skip_above[$routing]}" ]; then
    echo "[$(date +%T)] $run SKIPPED (failure rate > $FAIL_RATE_LIMIT in all 3 runs at parallel ${skip_above[$routing]})" >> "$LOG"; continue
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
  if [ -f "results/$run/results.jsonl" ]; then
    rate=$(fail_rate "$run")
    echo "[$(date +%T)] $run failure_rate=$rate" >> "$LOG"
    if python3 -c "import sys; sys.exit(0 if float('$rate') > $FAIL_RATE_LIMIT else 1)"; then
      key="${routing}_p${parallel}"
      over_limit[$key]=$(( ${over_limit[$key]:-0} + 1 ))
      if [ "${over_limit[$key]}" -ge 3 ]; then
        skip_above[$routing]=$parallel
        echo "[$(date +%T)] $routing: failure rate > $FAIL_RATE_LIMIT in 3 runs at parallel $parallel; skipping larger parallel" >> "$LOG"
      fi
    fi
  fi
done 3< "$SPECS"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
