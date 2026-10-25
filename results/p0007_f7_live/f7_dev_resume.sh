#!/usr/bin/env bash
# p0007 F-7：dev で失敗した質問だけを，同じ run_id・同じ設定で 1 回流し直す（A4〜A6 と同じ規則）．ノードは dev の実行のまま動いていること（deploy し直さない）．
#   bash results/p0007_f7_live/f7_dev_resume.sh
set -uo pipefail
cd /mnt/data-raid/ktakahashi/workspace/atrium || exit 1
LOG=results/p0007_f7_live/chain.log
trap 'git checkout -- config.yaml' EXIT
read -r run kvs < <(grep -v '^#' results/p0007_f7_live/specs.txt | head -1)
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
echo "[$(date +%T)] $run resume of failed questions" >> "$LOG"
bash scripts/tasks/remote.sh sync_config > "$LOG.$run.sync_resume" 2>&1 < /dev/null || { echo "[$(date +%T)] sync failed" >> "$LOG"; exit 1; }
mise run start "$run" > "$LOG.$run.start_resume" 2>&1 < /dev/null; rc=$?
echo "[$(date +%T)] $run resume start=$rc" >> "$LOG"
mise run analyze "$run" > "$LOG.$run.analyze_resume" 2>&1 < /dev/null; echo "[$(date +%T)] $run analyze_resume=$?" >> "$LOG"
exit $rc
