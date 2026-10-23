#!/usr/bin/env bash
# p0007 A6：test の失敗した問だけを同じ run_id で 1 回流し直し（A5 の a5_resume.sh），全候補の採点と cross-encoder の得点を求めて取得する．
# 主の実行では，操作端末から制御点への ssh が一時的に応答しなくなり（04:09），操作端末の監視（mise run start）だけが終わった．
# a6_run.sh が start の終了を確かめずに mise run stop へ進んだため，制御点の実行は残りの問を失敗として記録した．
# この版は，監視が切れたら同じ run_id で待ち直し（実験は制御点で続く），制御点の実行の終了コードを確かめてから stop へ進む．
#   nohup bash results/p0007_a6_live/a6_resume_test.sh > results/p0007_a6_live/a6_resume_test.nohup 2>&1 &
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
cd "$REPO" || exit 1
RUN=p0007_a6_test
D=results/p0007_a6_live
LOG=$D/chain_test.log
A=/home/denjo/workspace/ktakahashi/atrium
bash $D/precheck.sh > $D/precheck_test_resume_pre.txt 2>&1
if awk '$2 ~ /%$/ { if ($2 + 0 > 90) bad = 1 } END { exit !bad }' $D/precheck_test_resume_pre.txt; then
  echo "[$(date +%F_%T)] DISK OVER 90%; stop" >> "$LOG"; exit 1
fi
# 再開は run_meta.json を上書きするので，主の実行の値を控える
ssh wafl-ctrl5 "cp -n $A/results/$RUN/run_meta.json $A/results/$RUN/run_meta.main.json"
# a5_resume.sh は操作端末に results/<run_id> がある回だけを再開するので，制御点の主の実行の結果を先に取得する
mkdir -p results/$RUN
rsync -az --exclude .exit wafl-ctrl5:$A/results/$RUN/ results/$RUN/
bash results/p0007_a5_live/a5_resume.sh "$LOG" $D/specs_test.txt
if [ ! -f "$LOG.$RUN.start_resume" ]; then echo "[$(date +%F_%T)] resume was skipped; stop" >> "$LOG"; exit 1; fi
inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$RUN.start_resume" | tail -1 | tr -dc '0-9')
# 監視だけが切れた（終了コードが無い）ときは，制御点の終了の印（.exit）を直接待つ（mise run start を呼び直すと，
# 制御点の実行が終わっていれば既定の config.yaml で再開してしまうため使わない）．終わったら結果を取得する
if [ -z "$inner" ]; then
  echo "[$(date +%F_%T)] monitor lost; waiting for the control host's exit code" >> "$LOG"
  until inner=$(ssh -o ConnectTimeout=30 wafl-ctrl5 "cat $A/results/$RUN/.exit 2> /dev/null") && [ -n "$inner" ]; do
    sleep 120
  done
  rsync -az --exclude .exit wafl-ctrl5:$A/results/$RUN/ results/$RUN/
fi
echo "[$(date +%F_%T)] resume finished: control exit=${inner:-unknown}" >> "$LOG"
if [ -z "$inner" ]; then echo "[$(date +%F_%T)] control exit unknown; not stopping" >> "$LOG"; exit 1; fi
mise run analyze "$RUN" > "$LOG.analyze_resume2" 2>&1 < /dev/null
mise run stop > "$LOG.stop_resume" 2>&1 < /dev/null
echo "[$(date +%F_%T)] stopped; judging on the control host" >> "$LOG"
scp -q $D/a6_judge_ctrl.sh $D/a6_judge_chunks.sh wafl-ctrl5:$A/
ssh wafl-ctrl5 "nohup bash $A/a6_judge_ctrl.sh $RUN > $A/results/$RUN.judge_ctrl.log 2>&1 < /dev/null &"
until ssh wafl-ctrl5 "grep -q SELECT_FEATURES_DONE $A/results/$RUN.judge_ctrl.log" 2> /dev/null; do sleep 120; done
scp -q wafl-ctrl5:$A/results/$RUN.judge_ctrl.log "$LOG.judge"
echo "[$(date +%F_%T)] judge: $(grep -E 'DONE rc=|^PART |^split' "$LOG.judge" | tr '\n' ' ')" >> "$LOG"
rsync -az wafl-ctrl5:$A/results/$RUN/ results/$RUN/ --exclude .exit
mise run analyze "$RUN" > "$LOG.analyze_judged" 2>&1 < /dev/null
bash $D/precheck.sh > $D/precheck_test_post.txt 2>&1
echo "[$(date +%F_%T)] a6_resume_test: done" >> "$LOG"
