#!/usr/bin/env bash
# d0009 B3（Iteration 41）：wafl502〜508 の GPU の PC の操作（採点の server・生成の node・Ollama の起動と停止，SSH の転送，GPU の記録）．
# 使い方（操作端末 gpu4 から．wafl500・501・509 と gpu4 の GPU は使わない．B120・B122・B123，運用ルール 8・14）:
#   bash scripts/d0009/b3ic_hosts.sh snap <out>          各機の GPU の状態（温度・使用率・メモリ・Slowdown・他の研究者のプロセスの数）を <out> に追記する
#   bash scripts/d0009/b3ic_hosts.sh free                他の研究者の GPU のプロセスが 0 の機だけを使えるか確かめる（1 台でもあれば終了コード 1）
#   bash scripts/d0009/b3ic_hosts.sh scorers-up|scorers-down   採点の server（atrium-scorer．fp16・最大長 1,024）の起動・停止
#   bash scripts/d0009/b3ic_hosts.sh gen-up              生成の Ollama と node を起動し，llama3.1:8b を num_ctx 16,384 で載せる（採点の server は止める）
#   bash scripts/d0009/b3ic_hosts.sh ollama-up <model>   node と採点の server を止め，Ollama に <model> だけを載せる（判定・検証の段階）
#   bash scripts/d0009/b3ic_hosts.sh all-down            採点の server・node・Ollama を止める（始めの状態．削除・image・モデルの削除はしない）
#   bash scripts/d0009/b3ic_hosts.sh tunnels-open <scorer|node|ollama> / tunnels-close   SSH の転送（gpu4 の localhost から各機へ）
#   bash scripts/d0009/b3ic_hosts.sh urls <scorer|node|ollama>   転送の URL を --scorer-url などの引数の形で出す
# 環境変数 B3IC_HOSTS（空白区切り）で使う機を絞れる（例: B3IC_HOSTS="wafl502 wafl503" bash ... urls scorer）．
# 転送の port：採点 182NN，node 181NN，Ollama 115NN（NN は機の番号の下 2 桁．wafl502 なら 18202・18102・11502）．
# 各機の container は Iteration 40 までに作ったもの（image・モデルは各機の ~/.cache/huggingface と ~/workspace/ktakahashi/atrium）．
set -uo pipefail
# 使う機（環境変数 B3IC_HOSTS に空白区切りで指定．既定は wafl502〜508 の全て）．wafl505 は 100% の負荷で数分のうちに 83 °C に達した（Iteration 41 の記録）
# ので，採点・生成・判定の本番では B3IC_HOSTS から外す．
read -r -a HOSTS <<< "${B3IC_HOSTS:-wafl502 wafl503 wafl504 wafl505 wafl506 wafl507 wafl508}"
SSH_B=(-q -o BatchMode=yes -o ConnectTimeout=15)
SCORER_PORT=8200
NODE_PORT=8100
OLLAMA_PORT=11434
GEN_MODEL=llama3.1:8b
GEN_NUM_CTX=16384
UP_WAIT_S=600
SOCK_DIR=/tmp/b3ic-tunnels
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mkdir -p "$SOCK_DIR"

num_of() { echo "${1#wafl5}"; } # wafl502 → 02
rssh() { # rssh <host> <command...>（ログインの案内の行を除く）
  local host=$1
  shift
  ssh "${SSH_B[@]}" "$host" "$@" 2> /dev/null | grep -v '^\*\*'
  return "${PIPESTATUS[0]}"
}
ollama_ip() { rssh "$1" 'docker inspect -f "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}" atrium-ollama-1 2> /dev/null'; }
ollama_curl() { # ollama_curl <host> <path> [<json>]
  local host=$1 path=$2 data=${3:-} ip
  ip=$(ollama_ip "$host")
  case "$ip" in "" | *[!0-9.]*) return 3 ;; esac
  if [ -n "$data" ]; then rssh "$host" "curl -fsS -m 900 http://$ip:$OLLAMA_PORT$path -d '$data'"; else rssh "$host" "curl -fsS -m 30 http://$ip:$OLLAMA_PORT$path"; fi
}
unload_all_models() { # 自分たちの Ollama が載せているモデルを全て降ろす
  local host=$1 name
  for name in $(ollama_curl "$host" /api/ps 2> /dev/null | python3 -c "import json,sys; print(' '.join(m['name'] for m in json.load(sys.stdin)['models']))" 2> /dev/null); do
    ollama_curl "$host" /api/generate "{\"model\":\"$name\",\"keep_alive\":0}" > /dev/null 2>&1
  done
}
wait_http() { # wait_http <host> <port> <what>（機の localhost の healthz）
  local host=$1 port=$2 what=$3 waited=0
  until rssh "$host" "curl -fsS -m 5 http://localhost:$port/healthz" > /dev/null 2>&1; do
    sleep 5
    waited=$((waited + 5))
    if [ "$waited" -ge "$UP_WAIT_S" ]; then echo "$host: $what did not become healthy" >&2; return 1; fi
  done
}
foreign_procs() { rssh "$1" 'bash -s' < "$HERE/b2co_gpu_snapshot.sh" | sed -n 's/^other_procs=//p'; }

cmd=${1:?usage: b3ic_hosts.sh snap|free|scorers-up|scorers-down|gen-up|ollama-up|all-down|tunnels-open|tunnels-close|urls ...}
shift
case "$cmd" in
  snap)
    out=${1:?usage: snap <out>}
    for h in "${HOSTS[@]}"; do
      { echo "## $h $(date +%FT%T)"; rssh "$h" 'bash -s' < "$HERE/b2co_gpu_snapshot.sh"; } >> "$out"
    done
    ;;
  free)
    rc=0
    for h in "${HOSTS[@]}"; do
      n=$(foreign_procs "$h")
      if [ "$n" != 0 ]; then echo "$h: $n GPU process(es) of other users" >&2; rc=1; fi
    done
    exit "$rc"
    ;;
  scorers-up)
    for h in "${HOSTS[@]}"; do
      [ "$(foreign_procs "$h")" = 0 ] || { echo "$h busy; not starting" >&2; exit 1; }
      rssh "$h" "docker stop atrium-node-1 atrium-ollama-1 > /dev/null 2>&1; docker start atrium-scorer > /dev/null" || exit 1
    done
    for h in "${HOSTS[@]}"; do wait_http "$h" "$SCORER_PORT" scorer || exit 1; done
    ;;
  scorers-down)
    for h in "${HOSTS[@]}"; do rssh "$h" "docker stop atrium-scorer > /dev/null 2>&1; true"; done
    ;;
  gen-up)
    for h in "${HOSTS[@]}"; do
      [ "$(foreign_procs "$h")" = 0 ] || { echo "$h busy; not starting" >&2; exit 1; }
      rssh "$h" "docker stop atrium-scorer > /dev/null 2>&1; docker start atrium-ollama-1 atrium-node-1 > /dev/null" || exit 1
    done
    for h in "${HOSTS[@]}"; do
      wait_http "$h" "$NODE_PORT" node || exit 1
      ollama_curl "$h" /api/generate "{\"model\":\"$GEN_MODEL\",\"keep_alive\":-1,\"options\":{\"num_ctx\":$GEN_NUM_CTX}}" > /dev/null || { echo "$h: could not load $GEN_MODEL" >&2; exit 1; }
    done
    ;;
  ollama-up)
    model=${1:?usage: ollama-up <model>}
    for h in "${HOSTS[@]}"; do
      [ "$(foreign_procs "$h")" = 0 ] || { echo "$h busy; not starting" >&2; exit 1; }
      rssh "$h" "docker stop atrium-scorer atrium-node-1 > /dev/null 2>&1; docker start atrium-ollama-1 > /dev/null" || exit 1
    done
    for h in "${HOSTS[@]}"; do
      until ollama_curl "$h" /api/tags > /dev/null 2>&1; do sleep 3; done
      unload_all_models "$h"
      ollama_curl "$h" /api/generate "{\"model\":\"$model\",\"keep_alive\":-1,\"options\":{\"num_ctx\":$GEN_NUM_CTX}}" > /dev/null || { echo "$h: could not load $model" >&2; exit 1; }
    done
    ;;
  all-down)
    for h in "${HOSTS[@]}"; do rssh "$h" "docker stop atrium-scorer atrium-node-1 atrium-ollama-1 > /dev/null 2>&1; true"; done
    ;;
  tunnels-open)
    kind=${1:?usage: tunnels-open <scorer|node|ollama>}
    for h in "${HOSTS[@]}"; do
      n=$(num_of "$h")
      sock="$SOCK_DIR/$kind-$h.sock"
      [ -S "$sock" ] && ssh -S "$sock" -O exit "$h" > /dev/null 2>&1
      case "$kind" in
        scorer) forward="182$n:localhost:$SCORER_PORT" ;;
        node) forward="181$n:localhost:$NODE_PORT" ;;
        ollama)
          ip=$(ollama_ip "$h")
          case "$ip" in "" | *[!0-9.]*) echo "$h: ollama container is not running" >&2; exit 1 ;; esac
          forward="115$n:$ip:$OLLAMA_PORT"
          ;;
        *) echo "unknown kind $kind" >&2; exit 2 ;;
      esac
      ssh "${SSH_B[@]}" -f -N -M -S "$sock" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L "$forward" "$h" || exit 1
    done
    ;;
  tunnels-close)
    for sock in "$SOCK_DIR"/*.sock; do
      [ -S "$sock" ] && ssh -S "$sock" -O exit dummy > /dev/null 2>&1
    done
    ;;
  urls)
    kind=${1:?usage: urls <scorer|node|ollama>}
    for h in "${HOSTS[@]}"; do
      n=$(num_of "$h")
      case "$kind" in
        scorer) printf -- '--scorer-url http://127.0.0.1:182%s ' "$n" ;;
        node) printf -- '--node-url http://127.0.0.1:181%s ' "$n" ;;
        ollama) printf -- '--ollama-url http://127.0.0.1:115%s ' "$n" ;;
      esac
    done
    echo
    ;;
  *)
    echo "unknown command $cmd" >&2
    exit 2
    ;;
esac
