#!/usr/bin/env bash
# d0009 A6 参加（Iteration 31）：主構成 P の参加の測定を実機で流す連続実行（表の確かめ → deploy 1 回 → smoke → 3 ラウンド．各ラウンドは join-run 1 回）．
#   (nohup setsid sg docker -c "bash scripts/d0009/a6j_chain.sh results/d0009_a6_join/chain.log" > /dev/null 2>&1 < /dev/null &)
#   引数 2 個目以降に実行の名前（smoke・r1・r2・r3）を並べると，その順に流す（省略時は smoke r1 r2 r3）．
# 各ラウンドの前に wafl500〜509 の GPU が空くのを最大 2 時間待ち（空かなければ交絡として記録して流す），前後に GPU・ollama ps を記録し，
# 後に 30 台の node の /v1/profile が 200 であること（G-R）を確かめる．止まったままのノードがあれば再開してから記録し，直らなければそこで終わる．
# 本番のラウンドの後で，G-J（加わるホスト・受信箱が p0007 E2 と同じ）・G-A（公開情報の組み立てと候補の一致）・表に無い組の失敗 0（G-TB の本番）を確かめ，
# 満たさなければそこで終わる（smoke は実行の成否と表に無い組だけ）．results/<run_id>/join.json がある回は飛ばす．
# 実行の間は commit しない．config.yaml は終了時に元へ戻す．
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
LOG=$1
shift
D=scripts/d0009
FREE_MIB=1000
NEED_CONSEC=3
INTERVAL_S=60
MAX_WAIT_S=7200
MIN_CANDIDATE_AGREEMENT=0.90
NODE_PORT=8100
CONTROL=wafl-ctrl5
REMOTE_DIR=/home/denjo/workspace/ktakahashi/atrium
SSH_USER=denjo
DATA=/home/denjo/atrium-data
OWNER_TABLE=enronqa/ce_table/holdout_owner_join.jsonl
CENTRAL_TABLE=enronqa/ce_table/holdout_join.jsonl
# join-run の引数（--delays= は空の文字列．live_run.sh は引数を ssh の 1 つの文字列に展開するので，空の値は = で結ぶ）
COMMON_ARGS=(--n-devices 3 --delays= --max-tries-per-inbox 5 --central-mode c1_rrf_ce
  --owner-table "/data/$OWNER_TABLE" --central-table "/data/$CENTRAL_TABLE")
ARGS_smoke=(--n-devices 1 --delays= --summary-max-docs 200 --max-tries-per-inbox 2 --central-mode c1_rrf_ce
  --owner-table "/data/$OWNER_TABLE" --central-table "/data/$CENTRAL_TABLE")
ARGS_r1=("${COMMON_ARGS[@]}")
ARGS_r2=("${COMMON_ARGS[@]}")
ARGS_r3=("${COMMON_ARGS[@]}")
ORDER=(smoke r1 r2 r3)
if [ "$#" -gt 0 ]; then ORDER=("$@"); fi
trap 'git checkout -- config.yaml' EXIT
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

# 他の研究者の GPU の使用が無い（自分たちの Ollama の llama3.1:8b 以外のプロセスのメモリが FREE_MIB 未満）状態が NEED_CONSEC 回続くのを最大 MAX_WAIT_S 秒待つ．
# 戻り値 0：空いた／1：空かなかった（a6f_chain.sh と同じ）
OWN_MODEL_BLOB=667b0c1932bc6ffc
wait_for_free_gpus() {
  local consec=0 waited=0 used
  while [ "$waited" -lt "$MAX_WAIT_S" ]; do
    used=$(for i in 0 1 2 3 4 5 6 7 8 9; do
      ssh -n -o BatchMode=yes -o ConnectTimeout=15 "wafl50$i" "total=0; for pid in \$(nvidia-smi --query-compute-apps=pid --format=csv,noheader); do \
        case \"\$(tr '\\0' ' ' < /proc/\$pid/cmdline)\" in *$OWN_MODEL_BLOB*) ;; *) m=\$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits | grep \"^\$pid,\" | cut -d, -f2 | tr -d ' '); total=\$((total + \${m:-0}));; esac; done; echo \$total" 2> /dev/null | grep -v '^\*\*'
    done | sort -n | tail -1)
    echo "[$(date +%T)] max other-process GPU memory on wafl500-509: ${used:-?} MiB" >> "$LOG"
    if [ -n "${used:-}" ] && [ "$used" -lt "$FREE_MIB" ]; then consec=$((consec + 1)); else consec=0; fi
    if [ "$consec" -ge "$NEED_CONSEC" ]; then return 0; fi
    sleep "$INTERVAL_S"
    waited=$((waited + INTERVAL_S))
  done
  return 1
}

# 制御点から見た placement の全ノードの /v1/profile の状態を，応答が 200 でないホストの一覧として標準出力へ出す（空なら全て動いている）
down_hosts() {
  ssh -n -o BatchMode=yes "$CONTROL" "cd $REMOTE_DIR && for h in \$(cut -f1 artifacts/enronqa/placement.tsv); do \
    c=\$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://\$h:$NODE_PORT/v1/profile); [ \"\$c\" = 200 ] || echo \"\$h\"; done"
}

# G-R：30 台が全て動いていること．止まったノードは availability で戻し，だめなら node サービスだけを再開する（データ・image は触らない）
ensure_all_nodes_up() {
  local run=$1 down
  down=$(down_hosts)
  if [ -n "$down" ]; then
    echo "[$(date +%T)] $run G-R: not serving: $(echo $down)" >> "$LOG"
    for h in $down; do
      ssh -n -o BatchMode=yes "$CONTROL" "curl -s -o /dev/null --max-time 10 -X POST -H 'Content-Type: application/json' -d '{\"up\": true}' http://$h:$NODE_PORT/v1/admin/availability"
    done
    down=$(down_hosts)
  fi
  if [ -n "$down" ]; then
    for h in $down; do
      ssh -n -o BatchMode=yes "$CONTROL" "ssh -n -o BatchMode=yes $SSH_USER@$h 'cd $REMOTE_DIR && docker compose start node'" >> "$LOG" 2>&1
    done
    sleep 60
    down=$(down_hosts)
  fi
  echo "${down:-none}" > "results/$run/g_r_not_serving_after.txt"
  [ -z "$down" ]
}

run_one() {
  local tag=$1 run="d0009_a6_join_${1}" args_name="ARGS_${1}"
  local -n extra=$args_name
  if [ -f "results/$run/join.json" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; return 0
  fi
  echo "[$(date +%F_%T)] BEGIN $run" >> "$LOG"
  if wait_for_free_gpus; then confound=none; else confound="GPU not free after ${MAX_WAIT_S}s; run anyway"; fi
  echo "[$(date +%T)] $run confound=$confound" >> "$LOG"
  mkdir -p "results/$run"
  echo "$confound" > "results/$run/gpu_wait_result.txt"
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_pre.txt" 2>&1 < /dev/null
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_start.txt" 2>&1 < /dev/null
  bash scripts/tasks/live_run.sh join_run "$run" "${extra[@]}" > "$LOG.$run.live" 2>&1 < /dev/null
  local rc=$?
  bash $D/ollama_ps_snapshot.sh > "results/$run/ollama_ps_end.txt" 2>&1 < /dev/null
  bash $D/gpu_apps_snapshot.sh > "results/$run/gpu_apps_post.txt" 2>&1 < /dev/null
  echo "[$(date +%T)] $run live_run=$rc" >> "$LOG"
  if ! ensure_all_nodes_up "$run"; then
    echo "[$(date +%T)] $run G-R FAILED (some nodes still not serving; see results/$run/g_r_not_serving_after.txt)" >> "$LOG"; exit 1
  fi
  echo "[$(date +%T)] $run G-R ok (all nodes serving)" >> "$LOG"
  if [ ! -f "results/$run/join.json" ]; then
    echo "[$(date +%T)] $run FAILED (no join.json; inspect $LOG.$run.live and results/$run/start.log)" >> "$LOG"; exit 1
  fi
  # G-TB（本番）：測定の試行（P2P・中央）の中に，表に無い組による失敗が 1 件でもあれば，次へ進まずに止めて表を直す
  misses=$(jq '[(.p2p.devices[].answers[].rows[]), (.central.devices[].answers[].rows[])] | map(select(.failure_kind == "table_miss")) | length' "results/$run/join.json")
  echo "[$(date +%T)] $run table_miss=$misses" >> "$LOG"
  if [ "$misses" != 0 ]; then
    echo "[$(date +%T)] $run G-TB FAILED (table_miss=$misses); fix the table before the next run" >> "$LOG"; exit 1
  fi
  if [ "$tag" = smoke ]; then return 0; fi
  # G-J：加わるホスト・受信箱が p0007 E2 と同じ
  same_plan=$(jq -n --slurpfile a "results/$run/join.json" --slurpfile b results/p0007_e2_join/join.json \
    '($a[0].plan.joiner_hosts == $b[0].plan.joiner_hosts) and ($a[0].plan.joiner_inboxes == $b[0].plan.joiner_inboxes)')
  echo "[$(date +%T)] $run G-J same_plan=$same_plan" >> "$LOG"
  # G-A：公開情報の組み立て（Bm25Stats がオフラインと一致）と，候補の一致の割合
  g_a=$(jq --argjson min "$MIN_CANDIDATE_AGREEMENT" \
    '(.bm25_stats_vs_offline.g_a_stats_match == true) and ((.candidate_agreement_with_offline_summary // 0) >= $min)' "results/$run/sim_vs_live.json")
  echo "[$(date +%T)] $run G-A ok=$g_a agreement=$(jq .candidate_agreement_with_offline_summary "results/$run/sim_vs_live.json")" >> "$LOG"
  if [ "$same_plan" != true ] || [ "$g_a" != true ]; then
    echo "[$(date +%T)] $run G-J/G-A FAILED (same_plan=$same_plan g_a=$g_a); stop before the next run" >> "$LOG"; exit 1
  fi
}

# G-TB（表）：制御点の 2 つの表が gpu4 の表（a6j_tables.py の出力）と同じであること（deploy が制御点の表を質問者へ配る）
for table in "$OWNER_TABLE" "$CENTRAL_TABLE"; do
  local_sha=$(sha256sum "artifacts/$table" | cut -d' ' -f1)
  control_sha=$(ssh -n -o BatchMode=yes "$CONTROL" "sha256sum $DATA/$table" 2> /dev/null | cut -d' ' -f1)
  echo "[$(date +%T)] table $table sha256 gpu4=$local_sha control=$control_sha" >> "$LOG"
  if [ "$local_sha" != "$control_sha" ]; then echo "[$(date +%T)] TABLE MISMATCH between gpu4 and control: $table" >> "$LOG"; exit 1; fi
done
# 前の実験のノードを止め，config を作って deploy する（deploy は最初に 1 回．config は全ラウンドで同じ）
bash scripts/tasks/remote.sh stop > "$LOG.pre_stop" 2>&1 < /dev/null
mkdir -p results/d0009_a6_join
bash $D/gpu_apps_snapshot.sh > results/d0009_a6_join/gpu_apps_pre_deploy.txt 2>&1 < /dev/null
if ! uv run python $D/a6j_config.py >> "$LOG" 2>&1; then echo "[$(date +%T)] SETCFG FAILED" >> "$LOG"; exit 1; fi
if ! retry "$LOG.deploy" mise run deploy; then echo "[$(date +%T)] DEPLOY FAILED" >> "$LOG"; exit 1; fi
echo "[$(date +%T)] deployed" >> "$LOG"
# ノードの BM25 の索引の作成の時間（ノードの起動のログ．測定の窓に入らない．記述として残す．加わる 3 台と，比べに別の 2 台）
for h in 192.168.13.103 192.168.13.105 192.168.15.106 192.168.13.100 192.168.15.100; do
  echo "== $h" >> results/d0009_a6_join/node_bm25_build.txt
  ssh -n -o BatchMode=yes "$CONTROL" "ssh -n -o BatchMode=yes $SSH_USER@$h 'cd $REMOTE_DIR && docker compose logs node 2>&1 | grep -a \"bm25 build\" | cut -c1-200'" >> results/d0009_a6_join/node_bm25_build.txt 2>&1
done

for tag in "${ORDER[@]}"; do
  run_one "$tag"
done
bash scripts/tasks/remote.sh stop > "$LOG.final_stop" 2>&1 < /dev/null
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
