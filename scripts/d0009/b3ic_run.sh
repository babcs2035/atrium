#!/usr/bin/env bash
# d0009 B3（Iteration 41・42）：`atrium inbox-context-eval` の長い段階（q-ce・q-generate・g-gen-generate）を，熱の運用つきで終わるまで繰り返し流す．
#   B3IC_HOSTS="wafl502 ..." [B3IC_PLAN_DIR=results/d0009_b3ic42_plan] bash scripts/d0009/b3ic_run.sh <q-ce|q-generate|g-gen-generate> [<追加の引数>...]
# B3IC_PLAN_DIR は計画のディレクトリ（既定 results/d0009_b3ic_plan．Iteration 42 は results/d0009_b3ic42_plan．追加の引数にも同じ --plan-dir を渡す）．
# 各回の前に，83 °C 以上（温度が読めない機も）の機を B3IC_HOSTS から外して流す（Iteration 42 の事前登録 (d)：新しい要求を送らない．未完了の要求は残りの機が取る）．外した機は log に残す．
# 各回の前に，使う機（B3IC_HOSTS）の温度が ≤ 60 °C・SW/HW Thermal Slowdown が Not Active になるのを最大 COOL_MAX_S 秒待ち，流す．
# 実行中は b3ic_monitor.sh が 83 °C 以上の機を見て process を止める（記録の済んだ件は再実行で飛ばされる）．止まったら冷えるのを待って続きから流す．
# 終わりの判定：q-ce は終了コード 0 かつ n_failed = 0，q-generate は終了コード 0 かつ n_missing = 0．各回の出力は results/d0009_b3ic_plan/run_<段階>.<回>.{stdout,stderr}．
# 正答率の測定なので，熱の減速（Slowdown が Active）は記録だけで実行を無効にしない（待つのは冷えるまでだけ）．
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.." || exit 1
STAGE=${1:?usage: b3ic_run.sh <q-ce|q-generate> [args...]}
shift
PLAN_DIR=${B3IC_PLAN_DIR:-results/d0009_b3ic_plan}
HOSTS_SH=scripts/d0009/b3ic_hosts.sh
read -r -a ALL_HOSTS <<< "${B3IC_HOSTS:-wafl502 wafl503 wafl504 wafl505 wafl506 wafl507 wafl508}"
# b3ic_monitor.sh（B3IC_DROPPED_FILE）が 83 °C 以上の標本の機を書く一覧．その段階（この script の 1 回の実行）の残りで，その機には新しい要求を送らない
DROPPED_FILE=$PLAN_DIR/dropped_hosts.txt
[ "${B3IC_KEEP_DROPPED:-0}" = 1 ] && [ -f "$DROPPED_FILE" ] || : > "$DROPPED_FILE" # B3IC_KEEP_DROPPED=1：同じ段階の続きで，外した機の一覧を引き継ぐ
HOT_C=60
DROP_C=83
COOL_MAX_S=900
COOL_POLL_S=20
MAX_ATTEMPTS=60
LOG=$PLAN_DIR/run_$STAGE.log
say() { echo "[$(date +%F_%T)] $*" >> "$LOG"; }

hot_hosts() { # 冷えていない機の一覧（温度が読めない機も含める）
  local h row temp sw hw
  for h in "${HOSTS[@]}"; do
    row=$(ssh -n -q -o BatchMode=yes -o ConnectTimeout=10 "$h" "nvidia-smi --query-gpu=temperature.gpu,clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_slowdown --format=csv,noheader,nounits" 2> /dev/null | grep -E '^[0-9]+,' | tr -d ' ' | tr ',' ' ')
    read -r temp sw hw <<< "$row"
    if [ -z "$temp" ] || [ "$temp" -gt "$HOT_C" ] || [ "$sw" != NotActive ] || [ "$hw" != NotActive ]; then echo -n "$h($temp,$sw) "; fi
  done
}
active_hosts() { # 83 °C 未満で温度が読める機（空白区切り）．外した機は log に残す
  local h row temp keep=() dropped=()
  for h in "${HOSTS[@]}"; do
    row=$(ssh -n -q -o BatchMode=yes -o ConnectTimeout=10 "$h" "nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits" 2> /dev/null | grep -E '^[0-9]+$')
    temp=${row:-}
    if [ -z "$temp" ] || [ "$temp" -ge "$DROP_C" ]; then dropped+=("$h(${temp:-unreadable})"); else keep+=("$h"); fi
  done
  [ "${#dropped[@]}" -gt 0 ] && say "dropped from this attempt: ${dropped[*]}"
  echo "${keep[*]}"
}
refresh_hosts() { # ALL_HOSTS から，その段階で外した機を除いて HOSTS にする
  local h
  HOSTS=()
  for h in "${ALL_HOSTS[@]}"; do grep -qw "$h" "$DROPPED_FILE" 2> /dev/null || HOSTS+=("$h"); done
}
wait_cool() {
  local waited=0 hot
  while true; do
    hot=$(hot_hosts)
    [ -z "$hot" ] && { say "cool after ${waited}s"; return 0; }
    if [ "$waited" -ge "$COOL_MAX_S" ]; then say "still hot after ${waited}s: $hot (continuing)"; return 1; fi
    sleep "$COOL_POLL_S"
    waited=$((waited + COOL_POLL_S))
  done
}

case "$STAGE" in
  q-ce) URL_KIND=scorer; DONE_KEY=n_failed ;;
  q-generate | g-gen-generate) URL_KIND=node; DONE_KEY=n_missing ;;
  *) echo "unsupported stage $STAGE" >&2; exit 2 ;;
esac
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  refresh_hosts
  say "hosts dropped for the rest of the stage: $(tr '\n' ' ' < "$DROPPED_FILE")"
  wait_cool
  ACTIVE=$(active_hosts)
  if [ -z "$ACTIVE" ]; then say "no usable host; waiting"; sleep "$COOL_POLL_S"; continue; fi
  ARGS=$(B3IC_HOSTS="$ACTIVE" bash $HOSTS_SH urls $URL_KIND)
  echo "$ACTIVE" > "$PLAN_DIR/active_hosts.txt" # b3ic_monitor.sh（B3IC_ACTIVE_FILE）が，使っていない機の高温で process を止めないための記録
  say "attempt $attempt: $STAGE"
  # shellcheck disable=SC2086
  uv run atrium inbox-context-eval --stage "$STAGE" $ARGS "$@" > "$PLAN_DIR/run_$STAGE.$attempt.stdout" 2> "$PLAN_DIR/run_$STAGE.$attempt.stderr"
  rc=$?
  left=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['$DONE_KEY'])" "$PLAN_DIR/run_$STAGE.$attempt.stdout" 2> /dev/null || echo unknown)
  say "attempt $attempt: exit $rc, $DONE_KEY=$left"
  if [ "$rc" -eq 0 ] && [ "$left" = 0 ]; then say "DONE"; exit 0; fi
done
say "gave up after $MAX_ATTEMPTS attempts"
exit 1
