#!/usr/bin/env bash
# 制御点でのデータ準備の全体（scripts/remote/setup.sh が setsid nohup でバックグラウンドに起動する）．
#
# 使い方: bash scripts/remote/prepare_data.sh [dataset...]   # 省略時は feb4rag medrag（enronqa は明示して指定する）
# 出力: $DATA_DIR/logs/prepare.log（このスクリプトの出力），prepare.status（running / done / failed ...）
#
#   段階 A（制御点）: MIRAGE とコーパスの取得．FeB4RAG の準備は別のコンテナで並行して進める
#   段階 B（制御点＋GPU_WORKERS）: MedCPT の埋め込みを，断片ファイルのバイト数が均等になるよう分担する．
#           各 GPU PC へは断片ファイルを rsync で配り，埋め込みを制御点へ回収する．
#           失敗した GPU PC の分は，最後に制御点で残りを埋め込んで補う
#   段階 C（制御点）: シャード・クエリ埋め込み・関連ラベル・分割・ルーターの学習
# 各段は冪等なので，止まっても再び `mise run setup` すれば続きから再開する．
source "$(dirname "$0")/lib.sh"

DATASETS=${*:-"feb4rag medrag"}
LOG_DIR="$DATA_DIR/logs"
STATUS="$LOG_DIR/prepare.status"
PLAN_DIR="$DATA_DIR/medrag/embed_plan"
WORK_DIR="$REMOTE_DIR/embed-work"
mkdir -p "$LOG_DIR" "$DATA_DIR/.cache/huggingface"
echo running > "$STATUS"
# 開始時の config.yaml の写しを最後まで使う（実行中に同期された新しい設定を古いイメージが読めずに
# 止まったため．2026-10-06）
CONFIG_SNAPSHOT="$LOG_DIR/prepare-config.yaml"
cp config.yaml "$CONFIG_SNAPSHOT"
# イメージも開始時の版（digest）に固定する．実行中に setup・deploy が新しいイメージを push しても，
# 制御点と GPU PC は最後まで同じ版を使う
docker pull -q "$IMAGE_FULL" > /dev/null
IMAGE_REF=$(docker image inspect -f '{{index .RepoDigests 0}}' "$IMAGE_FULL")
log "using image $IMAGE_REF"
# 失敗の記録は EXIT trap で行う（ERR trap は関数の中の失敗では働かず，状態が running のまま残った）
on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then echo "failed (exit $rc)" > "$STATUS"; fi
}
trap on_exit EXIT

wants() { [[ " $DATASETS " == *" $1 "* ]]; }

# hub_run <container> <atrium の引数...>: 制御点の GPU で atrium を前景で実行する
hub_run() {
  local name=$1
  shift
  docker rm -f "$name" > /dev/null 2>&1 || true
  docker run --rm --name "$name" --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all \
    --user "$HOST_UID:$HOST_GID" -e HOME=/tmp -e HF_HOME=/data/.cache/huggingface \
    -v "$DATA_DIR:/data" -v "$CONFIG_SNAPSHOT:/app/config.yaml:ro" -w /data \
    "$IMAGE_REF" atrium --config /app/config.yaml "$@"
}

# GPU PC に残っている埋め込み（前回の中断分）を制御点へ回収する
collect_worker() {
  local host=$1
  if nssh "$host" "[ -d $WORK_DIR/medrag/corpus ]"; then
    nrsync -a --include='*/' --include='*.f16.npy' --exclude='*' \
      "$(ssh_dest "$host"):$WORK_DIR/medrag/corpus/" "$DATA_DIR/medrag/corpus/" || return 1
  fi
}

embed_worker() {
  local host=$1
  local plan="$PLAN_DIR/$host.txt"
  if [ ! -s "$plan" ]; then
    log "$host: nothing assigned"
    return 0
  fi
  if [ "$host" = local ]; then
    hub_run atrium-embed data medrag embed --data-dir /data --only-list "/data/medrag/embed_plan/local.txt" || return 1
    return 0
  fi
  # run_parallel を || 付きで呼ぶので，この関数の中では set -e が効かない．各手順の失敗を明示的に返し，
  # 埋め込みや回収に失敗したまま作業用の写しを消さないようにする
  ensure_tunnel "$host" || return 1
  nssh "$host" "docker pull -q $IMAGE_REF && mkdir -p $WORK_DIR/medrag $WORK_DIR/hf/hub" || return 1
  # 分担表の source/name を，データセットの root からの断片ファイルのパスへ直す
  sed 's|^\([^/]*\)/\(.*\)$|corpus/\1/chunk/\2.jsonl|' "$plan" > "$PLAN_DIR/$host.files"
  log "$host: sending $(wc -l < "$plan") chunk files"
  nrsync -a --files-from="$PLAN_DIR/$host.files" "$DATA_DIR/medrag/" "$(ssh_dest "$host"):$WORK_DIR/medrag/" || return 1
  nrsync -a "$plan" "$(ssh_dest "$host"):$WORK_DIR/plan.txt" || return 1
  nrsync -a "$CONFIG_SNAPSHOT" "$(ssh_dest "$host"):$WORK_DIR/config.yaml" || return 1
  # MedCPT のモデルは制御点のキャッシュから配り，GPU PC ではオフラインで読み込む
  # （GPU PC のインターネット接続に頼らない．2026-10-06 に研究室のゲートウェイが止まった）
  nrsync -a "$DATA_DIR"/.cache/huggingface/hub/models--ncbi--MedCPT-* "$(ssh_dest "$host"):$WORK_DIR/hf/hub/" || return 1
  log "$host: embedding"
  nssh "$host" "docker rm -f atrium-embed > /dev/null 2>&1; docker run --rm --name atrium-embed \
    --runtime nvidia -e NVIDIA_VISIBLE_DEVICES=all --user \$(id -u):\$(id -g) -e HOME=/tmp -e HF_HOME=/work/hf -e HF_HUB_OFFLINE=1 \
    -v $WORK_DIR:/work $IMAGE_REF atrium --config /work/config.yaml data medrag embed \
    --data-dir /work --only-list /work/plan.txt" || return 1
  collect_worker "$host" || return 1
  # 回収を終えた作業用の写し（断片と埋め込み）を消して，GPU PC のディスクを空ける
  nssh "$host" "rm -rf $WORK_DIR"
  log "$host: done"
}

# ── 段階 A ──────────────────────────────────────────────────────────────────
FEB_PID=""
if wants feb4rag; then
  log "feb4rag: started in background (log: $LOG_DIR/data-feb4rag.log)"
  {
    hub_run atrium-fetch-models-feb4rag fetch-models feb4rag \
      && hub_run atrium-data-feb4rag data feb4rag all --data-dir /data
  } >> "$LOG_DIR/data-feb4rag.log" 2>&1 &
  FEB_PID=$!
fi

if wants medrag; then
  for step in benchmark corpus; do
    log "medrag: $step"
    hub_run atrium-data-medrag data medrag "$step" --data-dir /data
  done

  # ── 段階 B ────────────────────────────────────────────────────────────────
  read -r -a worker_hosts <<< "$GPU_WORKERS"
  workers=(local "${worker_hosts[@]}")
  log "medrag: collecting leftovers from previous runs"
  for host in $GPU_WORKERS; do collect_worker "$host" || log "$host: could not collect"; done
  log "medrag: planning embedding over ${#workers[@]} GPUs"
  rm -rf "$PLAN_DIR"
  hub_run atrium-embed-plan embed-plan --data-dir /data --workers "${workers[@]}" --out-dir /data/medrag/embed_plan
  # GPU PC へ配る MedCPT のモデルを，制御点のキャッシュに用意しておく
  hub_run atrium-fetch-models fetch-models medrag
  run_parallel embed embed_worker "${workers[@]}" \
    || log "some GPU workers failed; their files will be embedded on the control host"
  log "medrag: embedding remaining files on the control host"
  hub_run atrium-embed data medrag embed --data-dir /data

  # ── 段階 C ────────────────────────────────────────────────────────────────
  for step in shards queries labels split train; do
    log "medrag: $step"
    hub_run atrium-data-medrag data medrag "$step" --data-dir /data
  done
fi

# ── EnronQA（p0004）．約 7.4 万通のメールを 1 億パラメータ級の符号化器で埋め込むだけなので，制御点の GPU 1 枚で行う
if wants enronqa; then
  log "enronqa: fetching models"
  hub_run atrium-fetch-models-enronqa fetch-models enronqa
  for step in fetch corpus embed questions queries labels shards train; do
    log "enronqa: $step"
    hub_run atrium-data-enronqa data enronqa "$step" --data-dir /data
  done
fi

if [ -n "$FEB_PID" ]; then
  log "waiting for feb4rag"
  wait "$FEB_PID"
fi
echo "done" > "$STATUS"
log "data preparation done: $DATASETS"
