#!/usr/bin/env bash
# d0009 B3（Iteration 41）：wafl502〜508 の GPU の温度・使用率・メモリ・Slowdown を POLL_S 秒ごとに CSV へ追記し，ABORT_C 度以上の機があれば
# 引数 2 個目のパターンに合う process（`atrium inbox-context-eval` など）を止める（記録の済んだ件は再実行で飛ばされる）．
#   (nohup setsid bash scripts/d0009/b3ic_monitor.sh <out.csv> "[a]trium inbox-context-eval" > /dev/null 2>&1 < /dev/null &)
# パターンは pkill -f の正規表現．monitor 自身の引数に合わないよう，頭文字を [] で囲んで渡す．止めるときは monitor の process を pid で止める．正答率の測定なので，熱の減速（Slowdown が Active）は記録だけで実行を無効にしない．
OUT=${1:?usage: b3ic_monitor.sh <out.csv> <pkill pattern>}
PATTERN=${2:?usage: b3ic_monitor.sh <out.csv> <pkill pattern>}
POLL_S=15
ABORT_C=83
# 見る機（環境変数 B3IC_HOSTS．既定は wafl502〜508 の全て．止める対象の判定にも使う）
read -r -a HOSTS <<< "${B3IC_HOSTS:-wafl502 wafl503 wafl504 wafl505 wafl506 wafl507 wafl508}"
[ -s "$OUT" ] || echo "ts,host,temp_c,util_pct,mem_mib,sw_thermal,hw_slowdown" > "$OUT"
while :; do
  now=$(date +%s)
  for h in "${HOSTS[@]}"; do
    row=$(ssh -n -q -o BatchMode=yes -o ConnectTimeout=10 "$h" "nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_slowdown --format=csv,noheader,nounits" 2> /dev/null | grep -E '^[0-9]+,' | tr -d ' ')
    [ -n "$row" ] || continue
    echo "$now,$h,$row" >> "$OUT"
    temp=${row%%,*}
    if [ "$temp" -ge "$ABORT_C" ]; then
      echo "$now,$h,ABORT,$temp" >> "$OUT"
      pkill -f "$PATTERN"
    fi
  done
  sleep "$POLL_S"
done
