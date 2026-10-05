#!/usr/bin/env bash
# `mise run analyze [run_id]`: 結果の分析．run_id を省略すると results/ の最新の実行を分析する．
#   e0_measure : results/<run>/e0_summary.md を作る
#   e1_routing : 制御点から labels・split・質問を artifacts/ へ取得し，
#                results/<run>/metrics.json と analysis_report.md を作り，専門家のログを集める
source "$(dirname "$0")/lib.sh"

if [ -n "${1:-}" ]; then RUN_DIR="results/$1"; else RUN_DIR=$(latest_run); fi
if [ -z "$RUN_DIR" ] || [ ! -f "$RUN_DIR/run_meta.json" ]; then
  echo "no run to analyze (run_meta.json not found)" >&2
  exit 1
fi
RUN_ID=$(basename "$RUN_DIR")
RUN_KIND=$(jq -r '.kind // "e1_routing"' "$RUN_DIR/run_meta.json")

if [ "$RUN_KIND" = "e0_measure" ]; then
  uv run atrium e0 summarize --dir "$RUN_DIR/e0"
  exit 0
fi

RUN_DATASET=$(jq -r '.dataset' "$RUN_DIR/run_meta.json")
mkdir -p "artifacts/$RUN_DATASET"
for item in benchmark labels; do
  rsync -az "$CONTROL:$DATA_DIR/$RUN_DATASET/$item" "artifacts/$RUN_DATASET/"
done
uv run atrium analyze --run-dir "$RUN_DIR" --artifacts-dir artifacts
remote collect_logs "$RUN_ID" "$RUN_DATASET"
rsync -az "$CONTROL:$REMOTE_DIR/results/$RUN_ID/logs" "$RUN_DIR/"
cat "$RUN_DIR/analysis_report.md"
