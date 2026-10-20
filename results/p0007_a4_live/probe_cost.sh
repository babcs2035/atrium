#!/usr/bin/env bash
# probe_cost.py を各ノードのイメージの中で rrf・rrf+probe の順に 2 回ずつ流し，probe_cost.jsonl に集める（制御点経由）．
cd "$(dirname "$0")"
: > probe_cost.jsonl
run_node() {
  local host=$1 shards=$2
  for rep in 1 2; do
    for mode in rrf rrf+probe; do
      out=$(ssh wafl-ctrl5 "ssh denjo@$host 'docker run --rm -i -v /home/denjo/workspace/ktakahashi/atrium/shards:/shards localhost:5000/atrium-node:latest python - $mode $shards'" < probe_cost.py 2>&1 | tail -1)
      echo "{\"host\": \"$host\", \"rep\": $rep, \"result\": $out}" >> probe_cost.jsonl
    done
  done
}
run_node 192.168.13.100 "dasovich-j phanis-s quenet-j solberg-g south-s"
run_node 192.168.15.100 "davis-d mckay-j mclaughlin-e presto-k sager-e"
