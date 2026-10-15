#!/bin/bash
# Iteration 14 の事前の測定（使い捨て．Iteration 13 の prestudy/run_ctrl.sh と同じ手順）：制御点の atrium-full のコンテナ（--network host，/data は読み取りだけ）の中で，
# 本物の atrium node を 30 個（シャードは /w/shards の複製）と本物の atrium run（質問者 1 プロセス）を流す．
# 使い方：run_ctrl.sh "<shape>:<variant>[:prof|:spy] ..." <回数>（例：run_ctrl.sh "fs:cur dp12:cur fs:cur:prof fs:cur:spy" 3）
set -u
cd /w
mkdir -p /w/logs /w/out
[ -d /w/shards ] || cp -rL /data/enronqa/shards /w/shards  # chunk・emb は corpus への相対の symlink なので実体を写す
python make_inputs.py /w/placement_real.json /w/config.yaml /w
while read -r port shards; do
  atrium --config /w/cfg_fs.yaml node --host 127.0.0.1 --port "$port" --node-id "n$port" \
    --shards-dir /w/shards --shard-ids "$shards" --ollama-url http://127.0.0.1:1 > "/w/logs/node$port.log" 2>&1 &
done < /w/nodes.txt
for _ in $(seq 1 120); do
  up=0
  while read -r port _s; do python -c "import urllib.request,sys;urllib.request.urlopen(sys.argv[1],timeout=2)" "http://127.0.0.1:$port/healthz" 2> /dev/null && up=$((up + 1)); done < /w/nodes.txt
  [ "$up" -eq 30 ] && break
  sleep 2
done
echo "nodes up: $up"
for rep in $(seq 1 "$2"); do
  for spec in $1; do
    IFS=: read -r shape variant prof <<< "$spec"
    out="/w/out/${shape}_${variant}_${prof:-plain}_r$rep"
    if [ "${prof:-}" = spy ]; then
      # py-spy（/w/py-spy．wheel から取り出した単体のバイナリ）で主スレッドを含む全スレッドの積み重ねを 250 Hz で取る
      /w/py-spy record -r 250 -f raw -o "$out.spy.txt" -- \
        python run_one.py "/w/cfg_$shape.yaml" /w/placement_local.json "$out" "$variant" 2> "$out.stderr" | tail -1
    else
      python run_one.py "/w/cfg_$shape.yaml" /w/placement_local.json "$out" "$variant" ${prof:-} 2> "$out.stderr" | tail -1
    fi
  done
done
kill $(jobs -p) 2> /dev/null
wait
