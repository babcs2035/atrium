#!/usr/bin/env bash
# failure_run.sh が source する，ノードのコンテナを止めて再開する頼みの受け口（p0007 I6）．
#
# 質問者のコンテナ（`atrium failure-run`）は ssh できないので，頼みを `<ctl>/req/<id>.json`（{"id","action","hosts"}）に書き，
# ここが ssh で `docker compose stop node` / `docker compose start node` だけを実行して `<ctl>/ack/<id>.json` を書く．
# 安全のための決まり：
#   - 実行するのは node サービスの stop と start だけ（down・rm・prune・イメージやデータの削除はしない）．
#   - 対象は control_init で渡した許可のホスト（placement のノード）だけ．それ以外・stop/start 以外の頼みは ok=false で断る．
#   - 止めたホストは <ctl>/stopped.txt に残し，start で消す．control_resume_stopped が，止めたままのホストを全て再開する
#     （failure_run.sh の EXIT の後始末が呼ぶ）．
# 呼び出し側が node_compose <host> <stop|start>（ssh でそのホストの `docker compose <action> node` を実行する）を定義する．
# 時刻は制御点の epoch 秒（date +%s.%N）．

CONTROL_POLL_S=0.1

control_log() {
  if declare -F log > /dev/null; then log "$*"; else echo "[$(date +%H:%M:%S)] $*"; fi
}

# control_init <ctl_dir> <許可のホスト...>
control_init() {
  CTL=$1
  shift
  ALLOWED_HOSTS=("$@")
  mkdir -p "$CTL/req" "$CTL/ack" "$CTL/tmp"
  : > "$CTL/stopped.txt"
}

control_host_allowed() {
  local host=$1 allowed
  for allowed in "${ALLOWED_HOSTS[@]}"; do
    if [ "$allowed" = "$host" ]; then return 0; fi
  done
  return 1
}

# control_run_host <id> <action> <host>: 1 台に実行し，{host: {rc, begin, end}} を <ctl>/tmp/<id>.<host>.json に書く
control_run_host() {
  local id=$1 action=$2 host=$3 begin end rc
  begin=$(date +%s.%N)
  node_compose "$host" "$action" > /dev/null 2>&1
  rc=$?
  end=$(date +%s.%N)
  jq -n --arg host "$host" --argjson rc "$rc" --argjson begin "$begin" --argjson end "$end" \
    '{($host): {rc: $rc, begin: $begin, end: $end}}' > "$CTL/tmp/$id.$host.json"
}

# control_write_ack <id> <ok> <host の結果ファイル...>
control_write_ack() {
  local id=$1 ok=$2
  shift 2
  local hosts_json='{}'
  if [ "$#" -gt 0 ]; then hosts_json=$(jq -s 'add' "$@"); fi
  jq -n --arg id "$id" --argjson ok "$ok" --argjson hosts "$hosts_json" '{id: $id, ok: $ok, hosts: $hosts}' \
    > "$CTL/ack/$id.json.tmp"
  mv "$CTL/ack/$id.json.tmp" "$CTL/ack/$id.json"
}

# control_handle_request <request_file>: 頼みを 1 件処理する（許可外・stop/start 以外は何も実行せず ok=false）
control_handle_request() {
  local file=$1 id action host
  local -a hosts=() results=()
  id=$(jq -r '.id' "$file")
  action=$(jq -r '.action' "$file")
  mapfile -t hosts < <(jq -r '.hosts[]' "$file")
  rm -f "$file"
  case "$action" in
    stop | start) ;;
    *)
      control_log "refused action: $action"
      control_write_ack "$id" false
      return 0
      ;;
  esac
  for host in "${hosts[@]}"; do
    if ! control_host_allowed "$host"; then
      control_log "refused host: $host"
      control_write_ack "$id" false
      return 0
    fi
  done
  control_log "$action node on ${hosts[*]}"
  for host in "${hosts[@]}"; do
    control_run_host "$id" "$action" "$host" &
  done
  wait
  local ok=true
  for host in "${hosts[@]}"; do
    results+=("$CTL/tmp/$id.$host.json")
    if [ "$(jq -r '.[] | .rc' "$CTL/tmp/$id.$host.json")" != 0 ]; then ok=false; fi
  done
  # 止めた記録は，実行の成否にかかわらず stop で足し（失敗しても止まっているかもしれない），start の成功で消す
  for host in "${hosts[@]}"; do
    if [ "$action" = stop ]; then
      echo "$host" >> "$CTL/stopped.txt"
    elif [ "$(jq -r '.[] | .rc' "$CTL/tmp/$id.$host.json")" = 0 ]; then
      grep -vxF "$host" "$CTL/stopped.txt" > "$CTL/stopped.tmp" || true
      mv "$CTL/stopped.tmp" "$CTL/stopped.txt"
    fi
  done
  control_write_ack "$id" "$ok" "${results[@]}"
}

# control_watch: <ctl>/done ができるまで，頼みのファイルを順に処理する
control_watch() {
  local req
  while [ ! -e "$CTL/done" ]; do
    for req in "$CTL"/req/*.json; do
      if [ -e "$req" ]; then control_handle_request "$req"; fi
    done
    sleep "$CONTROL_POLL_S"
  done
}

# control_resume_stopped: 止めたままのホスト（stopped.txt）を全て再開する．1 台でも再開できなければ 1 を返す
control_resume_stopped() {
  local host status=0
  if [ ! -s "$CTL/stopped.txt" ]; then return 0; fi
  while read -r host; do
    control_log "resuming $host"
    if ! node_compose "$host" start; then
      control_log "FAILED to resume $host; run on it: cd <remote_dir> && docker compose start node"
      status=1
    fi
  done < <(sort -u "$CTL/stopped.txt")
  return "$status"
}
