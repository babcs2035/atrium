#!/usr/bin/env bash
# EQ8〜EQ10：各回の前に deploy し直し（KV キャッシュを空にする），start → analyze．specs の @judge の行で，
# それまでの EQ8・EQ9 の回を判定モデルで採点して analyze する．config.yaml は各回の後と終了時に元へ戻す．
#   bash eq8_chain.sh <log> <specs>
# specs の各行は "<run_id> key=value ..."（# で始まる行と空行は飛ばす）．キーは experiment・llm の直下のキー，
# または data の下の "<節>.<キー>"（例 enronqa.k_context）．
# results/<run_id> がある回は飛ばす．制御点で実行中の回は deploy せずに終わりを待つ（deploy するとその回を壊すため）．
# EQ8・EQ9 の回は，失敗した問が残ったら（start.sh の exit 2）同じ run_id で 1 回だけ再開する．
# EQ10 は待ち時間を測るので再開しない（再開した問は KV キャッシュの状態が違う）．
set -uo pipefail
REPO=/mnt/data-raid/ktakahashi/workspace/atrium
CONTROL=wafl-ctrl5
JUDGE_MODEL=qwen3:8b
cd "$REPO" || exit 1
LOG=$1
SPECS=$2
trap 'git checkout -- config.yaml' EXIT
setcfg() {
  python3 - "$@" <<'PY'
import re, sys, pathlib
p = pathlib.Path("config.yaml"); t = p.read_text()
for kv in sys.argv[1:]:
    k, v = kv.split("=", 1)
    if "." in k:
        # 字下げ 2 個の節（data の下の enronqa など）の中だけで置き換える．先頭から探すと local_answer.k_context など
        # 同名の別のキーを書き換えてしまうため
        section, key = k.split(".")
        m = re.search(rf"(?m)^  {section}:\n((?:(?:    .*|[ \t]*)\n)*)", t)
        assert m, section
        body, n = re.subn(rf"(?m)^(    {key}: ).*$", rf"\g<1>{v}", m.group(1), count=1)
        t = t[: m.start(1)] + body + t[m.end(1) :]
    else:
        t, n = re.subn(rf"(?m)^(  {k}: ).*$", rf"\g<1>{v}", t, count=1)
    assert n == 1, k
p.write_text(t)
PY
}
# run_start <run_id> <tag>: start を待ち，内側の終了コードを $inner に入れる
run_start() {
  mise run start "$1" > "$LOG.$1.$2" 2>&1 < /dev/null; local rc=$?
  # mise run の終了コードは内側の start.sh の失敗（exit 2 = 失敗した問が残る）を反映しないことがあるため，ログから拾う
  inner=$(grep -oE '\(exit [0-9]+\)' "$LOG.$1.$2" | tail -1 | tr -dc '0-9')
  echo "[$(date +%T)] $1 $2=$rc inner=${inner:-?}" >> "$LOG"
}
judge_runs=()
# specs は fd 3 で読む（ssh・mise が標準入力を読んで行を消費しないように）
while read -r run kvs <&3; do
  [[ -z "$run" || "$run" == \#* ]] && continue
  if [ "$run" = @judge ]; then
    todo=()
    for r in "${judge_runs[@]}"; do
      [ -f "results/$r/results.jsonl" ] && [ ! -f "results/$r/judgements.jsonl" ] && todo+=("$r")
    done
    echo "[$(date +%F_%T)] BEGIN judge ${todo[*]}" >> "$LOG"
    [ "${#todo[@]}" -gt 0 ] || continue
    bash scripts/tasks/judge.sh up "$JUDGE_MODEL" > "$LOG.judge.up" 2>&1 < /dev/null; rc=$?
    echo "[$(date +%T)] judge up=$rc" >> "$LOG"
    if [ "$rc" = 0 ]; then
      bash scripts/tasks/judge.sh run "${todo[@]}" > "$LOG.judge.run" 2>&1 < /dev/null
      echo "[$(date +%T)] judge run=$?" >> "$LOG"
      for r in "${todo[@]}"; do
        mise run analyze "$r" > "$LOG.$r.analyze_judged" 2>&1 < /dev/null
        echo "[$(date +%T)] analyze $r=$? judged=$(wc -l < "results/$r/judgements.jsonl" 2> /dev/null)" >> "$LOG"
      done
    fi
    # 判定モデルは GPU の専門家のコンテナを止めて GPU を使うので，続く回の deploy の前に消す
    bash scripts/tasks/judge.sh down > "$LOG.judge.down" 2>&1 < /dev/null
    echo "[$(date +%T)] judge down=$?" >> "$LOG"
    continue
  fi
  read -r -a parts <<< "$kvs"
  [[ "$run" == *_eq10_* ]] || judge_runs+=("$run")
  if [ -d "results/$run" ]; then
    echo "[$(date +%T)] $run skip (results exist)" >> "$LOG"; continue
  fi
  echo "[$(date +%F_%T)] BEGIN $run ${parts[*]}" >> "$LOG"
  # start.sh は config.yaml を制御点へ同期するので，実行中の回を待つときも同じ条件の設定にしておく
  if ! setcfg "${parts[@]}" 2>> "$LOG"; then
    echo "[$(date +%T)] $run SETCFG FAILED" >> "$LOG"; git checkout -- config.yaml; continue
  fi
  # [s] で囲むのは，ssh が起こす bash -c のコマンドライン自身に pgrep が一致しないようにするため
  if ssh -n "$CONTROL" "pgrep -f '[s]cripts/remote/start.sh $run ' > /dev/null"; then
    echo "[$(date +%T)] $run already running; waiting without deploy" >> "$LOG"
  elif ! mise run deploy > "$LOG.$run.deploy" 2>&1 < /dev/null; then
    echo "[$(date +%T)] $run DEPLOY FAILED" >> "$LOG"; git checkout -- config.yaml; continue
  else
    echo "[$(date +%T)] $run deployed" >> "$LOG"
  fi
  run_start "$run" start
  if [ "${inner:-}" = 2 ] && [[ "$run" != *_eq10_* ]]; then
    run_start "$run" resume
  fi
  mise run analyze "$run" > "$LOG.$run.analyze" 2>&1 < /dev/null; echo "[$(date +%T)] $run analyze=$?" >> "$LOG"
  grep -E 'e2e' "$LOG.$run.analyze" | head -2 >> "$LOG"
  git checkout -- config.yaml
done 3< "$SPECS"
echo "[$(date +%F_%T)] ALL DONE" >> "$LOG"
