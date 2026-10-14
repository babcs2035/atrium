#!/usr/bin/env bash
# p0003 S6：E3 の宿る型（local_answer）のパイロット．smoke（10 問）→ pilot（100 問）→ 基準を満たせば pilot50（250 問）．
# 条件は dataset medrag，routing all，answer_mode local_answer，merge score．config.yaml は終了時に必ず元へ戻す．
# smoke で失敗した問が残ったら，そこで止まる（コードを直すかどうかは人が見て決める．p0003 §4 S6 の 1）．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
LOG=/home/ktakahashi/.claude/jobs/0b6384d1/tmp/s6_run.log
D=${S6_DATE:-20261012}
cd "$REPO" || exit 1
trap 'git checkout -- config.yaml; echo "[$(date +%F_%T)] EXIT" >> "$LOG"' EXIT
echo "[$(date +%F_%T)] BEGIN S6" >> "$LOG"

setcfg() {
  python3 - "$@" <<'PY'
import re, sys, pathlib
p = pathlib.Path("config.yaml"); t = p.read_text()
for kv in sys.argv[1:]:
    k, v = kv.split("=", 1)
    # experiment・retrieval の直下（字下げ 2 個）のキーだけを書き換える
    t, n = re.subn(rf"(?m)^(  {k}: ).*$", rf"\g<1>{v}", t, count=1)
    assert n == 1, k
p.write_text(t)
PY
}

# §6 の 4：MedRAG の専門家 16 台と制御点のディスクが 90% を超えたら止める
hosts="$(for i in 0 1 2 3 4 5 6 7 8 9; do echo 192.168.13.10$i; done; for i in 0 1 2 3 4 5; do echo 192.168.14.10$i; done)"
over=0
while read -r h; do
  u=$(ssh -n wafl-ctrl5 "ssh -n -o ConnectTimeout=10 denjo@$h \"df --output=pcent / | tail -1\"" 2>&1 | tr -dc '0-9')
  echo "disk $h ${u:-?}%" >> "$LOG"
  if [ -z "$u" ] || [ "$u" -gt 90 ]; then over=1; fi
done <<< "$hosts"
u=$(ssh -n wafl-ctrl5 "df --output=pcent / | tail -1" | tr -dc '0-9')
echo "disk wafl-ctrl5 ${u}%" >> "$LOG"
[ "$u" -gt 90 ] && over=1
if [ "$over" = 1 ]; then
  echo "[$(date +%T)] STOP: disk over 90% or unreachable (p0003 §6.4)" >> "$LOG"; exit 3
fi

# one <run_id> <question_limit>: deploy → start → analyze．失敗した問数を $nfail，所要秒を $dur に入れる
one() {
  local run=$1 lim=$2 t0 rc
  git checkout -- config.yaml
  setcfg routing=all answer_mode=local_answer merge=score "question_limit=$lim" || { echo "[$(date +%T)] SETCFG FAILED" >> "$LOG"; exit 5; }
  echo "[$(date +%F_%T)] BEGIN $run (question_limit=$lim)" >> "$LOG"
  mise run deploy > "$LOG.$run.deploy" 2>&1 < /dev/null; rc=$?
  echo "[$(date +%T)] $run deploy=$rc" >> "$LOG"
  [ "$rc" = 0 ] || exit 4
  t0=$(date +%s)
  mise run start "$run" > "$LOG.$run.start" 2>&1 < /dev/null; rc=$?
  dur=$(( $(date +%s) - t0 ))
  echo "[$(date +%T)] $run start=$rc dur_s=$dur" >> "$LOG"
  mise run analyze "$run" > "$LOG.$run.analyze" 2>&1 < /dev/null
  echo "[$(date +%T)] $run analyze=$?" >> "$LOG"
  nfail=$(python3 -c "import json,sys;rs={};[rs.__setitem__(r['qid'],r) for r in map(json.loads,open(sys.argv[1]))];print(sum(r['error'] is not None for r in rs.values()),len(rs))" "results/$run/results.jsonl" 2>/dev/null)
  echo "[$(date +%T)] $run failed/total=${nfail:-?}" >> "$LOG"
  nfail=${nfail%% *}
}

one "${D}_e3_smoke" 2
if [ "${nfail:-x}" != 0 ]; then
  echo "[$(date +%T)] STOP: smoke has failed questions (p0003 §4 S6 の 1)" >> "$LOG"
  mise run stop > "$LOG.stop" 2>&1 < /dev/null; exit 6
fi
one "${D}_e3_pilot" 20
# C1 の基準：パイロットの失敗率が 0 で，所要時間が 3 時間以内なら 250 問を 1 回だけ追加する
if [ "${nfail:-x}" = 0 ] && [ "$dur" -le 10800 ]; then
  one "${D}_e3_pilot50" 50
else
  echo "[$(date +%T)] pilot50 skipped (failed=${nfail:-?} dur_s=$dur)" >> "$LOG"
fi
mise run stop > "$LOG.stop" 2>&1 < /dev/null
echo "[$(date +%T)] stop=$?" >> "$LOG"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
