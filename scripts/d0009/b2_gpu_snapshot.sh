#!/usr/bin/env bash
# d0009 B2：GPU の PC の状態を key=value の行で標準出力へ出す（読み取りだけ．b2_chain.sh が `ssh <host> bash -s < このファイル` で流す）．
#   ts          epoch 秒
#   gpu         温度(°C),使用率(%),GPU メモリ(MiB),SW Thermal Slowdown,HW Slowdown（空白なし．例: 47,0,32,NotActive,NotActive）
#   counter_us  SW Thermal Slowdown の起動からの累計（マイクロ秒）
#   other_procs 自分たちの採点の container（atrium-scorer）以外の GPU のプロセスの数（他の研究者の使用の検出）
echo "ts=$(date +%s)"
echo "gpu=$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,clocks_event_reasons.sw_thermal_slowdown,clocks_event_reasons.hw_slowdown --format=csv,noheader,nounits | tr -d ' ')"
echo "counter_us=$(nvidia-smi -q -d PERFORMANCE | awk '/SW Thermal Slowdown/ && /us$/ {print $(NF-1)}')"
mine=$(docker top atrium-scorer -eo pid 2> /dev/null | tail -n +2 | tr -d ' ' | tr '\n' ' ')
other=0
for p in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader); do
  case " $mine " in *" $p "*) ;; *) other=$((other + 1)) ;; esac
done
echo "other_procs=$other"
