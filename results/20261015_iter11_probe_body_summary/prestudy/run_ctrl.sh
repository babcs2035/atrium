#!/bin/bash
# 制御点のコンテナの中で偽のノード 30 個と質問者の 1 プロセスを流す（使い捨て）
cd /w
for i in $(seq 0 29); do python bench.py serve $((18100+i)) > srv$i.log 2>&1 & done
sleep 20
for rep in 1 2 3; do
  for v in base pre base_quiet; do python bench.py $v 300; done
  for v in base pre; do PYTHONPATH=/w/sniffio_pkg python bench.py $v 300 | sed 's/^/sniffio /'; done
done
python bench.py base 100 prof 2>&1 | head -45
kill $(jobs -p)
