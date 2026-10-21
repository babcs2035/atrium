#!/usr/bin/env bash
# p0007 A5 の test 側の連続実行：P2P（全候補の保存）→ C1-RRF-CE（ce_chain.sh．質問者の LLM を wafl509 へ分ける）→ oracle-RRF．
#   bash a5_test_all.sh <log>
# 事前登録の順（test → C1-RRF-CE → oracle-RRF）．途中で commit しない．
set -uo pipefail
cd /mnt/data-raid/ktakahashi/workspace/atrium || exit 1
LOG=$1
D=results/p0007_a5_live
bash $D/a5_chain.sh "$LOG" $D/specs_test.txt
bash $D/ce_chain.sh "$LOG" $D/specs_ce.txt
bash $D/a5_chain.sh "$LOG" $D/specs_oracle.txt
mise run stop > "$LOG.stop_final" 2>&1 < /dev/null
echo "[$(date +%F_%T)] a5_test_all: done" >> "$LOG"
