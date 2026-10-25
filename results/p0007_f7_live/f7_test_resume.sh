#!/usr/bin/env bash
# p0007 F-7：test の主の実行で失敗した問（GPU の PC の Ollama のコンテナが 05:38 に再起動され 05:54 に消えたため，ノードが 502・接続の失敗を返した）だけを，
# 同じ run_id・同じ設定で 1 回流し直す（A4〜A6 と同じ規則）．Ollama が消えたので deploy をやり直してから再開する（deploy は 1 回だけ）．
#   bash results/p0007_f7_live/f7_test_resume.sh
set -uo pipefail
cd /mnt/data-raid/ktakahashi/workspace/atrium || exit 1
LOG=results/p0007_f7_live/chain.log
trap 'git checkout -- config.yaml' EXIT
sed -n 3p results/p0007_f7_live/specs.txt > /tmp/f7_test_spec.txt
read -r run kvs < /tmp/f7_test_spec.txt
read -r -a parts <<< "$kvs"
python3 - "${parts[@]}" <<'PY'
import re, sys, pathlib
p = pathlib.Path("config.yaml"); lines = p.read_text().split("\n")
for kv in sys.argv[1:]:
    path, v = kv.split("=", 1)
    section, key = path.split(".", 1)
    start = lines.index(f"{section}:")
    end = next((i for i in range(start + 1, len(lines)) if lines[i] and not lines[i].startswith((" ", "#"))), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"^  {key}: ", lines[i]):
            lines[i] = f"  {key}: {v}"
            break
    else:
        raise SystemExit(f"key not found: {path}")
p.write_text("\n".join(lines))
PY
echo "[$(date +%T)] $run resume: redeploying (Ollama containers on the GPU PCs are gone)" >> "$LOG"
bash results/p0007_f6_live/gpu_apps_snapshot.sh > "results/$run/gpu_apps_pre_resume.txt" 2>&1 < /dev/null
mise run deploy > "$LOG.$run.deploy_resume" 2>&1 < /dev/null || { echo "[$(date +%T)] $run DEPLOY FAILED" >> "$LOG"; exit 1; }
echo "[$(date +%T)] $run deployed (resume)" >> "$LOG"
bash results/p0007_f7_live/ollama_ps_snapshot.sh > results/p0007_f7_live/ollama_ps_test_resume_start.txt 2>&1 < /dev/null
mise run start "$run" > "$LOG.$run.start_resume" 2>&1 < /dev/null; rc=$?
bash results/p0007_f6_live/gpu_apps_snapshot.sh > "results/$run/gpu_apps_post_resume.txt" 2>&1 < /dev/null
bash results/p0007_f7_live/ollama_ps_snapshot.sh > results/p0007_f7_live/ollama_ps_test_resume_end.txt 2>&1 < /dev/null
echo "[$(date +%T)] $run resume start=$rc" >> "$LOG"
mise run analyze "$run" > "$LOG.$run.analyze_resume" 2>&1 < /dev/null; echo "[$(date +%T)] $run analyze_resume=$?" >> "$LOG"
exit $rc
