#!/usr/bin/env bash
# bm25_cost.py を各ノードのイメージの中で fusion=none・rrf の順に 2 回ずつ流し，bm25_cost.jsonl に集める（制御点経由）．
cd "$(dirname "$0")"
: > bm25_cost.jsonl
run_node() {
  local host=$1 shards=$2
  for rep in 1 2; do
    for fusion in none rrf; do
      out=$(ssh wafl-ctrl5 "ssh denjo@$host 'docker run --rm -i -v /home/denjo/workspace/ktakahashi/atrium/shards:/shards localhost:5000/atrium-node:latest python - $fusion $shards'" < bm25_cost.py 2>&1 | tail -1)
      echo "{\"host\": \"$host\", \"rep\": $rep, \"result\": $out}" >> bm25_cost.jsonl
    done
  done
}
run_node 192.168.13.100 "dasovich-j phanis-s quenet-j solberg-g south-s"
run_node 192.168.15.100 "davis-d mckay-j mclaughlin-e presto-k sager-e"
