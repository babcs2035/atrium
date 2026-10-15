# Iteration 14 の事前の測定：変種の実行の selected_sources と top_doc_ids が基準の実行と問ごとに一致するかを数える．
# 使い方：python compare_selected.py <基準の out_dir> <out_dir> ...
import json
import sys
from pathlib import Path
from typing import Any

KEYS = ("selected_sources", "top_doc_ids")


def load_rows(out_dir: Path) -> dict[str, dict[str, Any]]:
    """results.jsonl を問の識別子で引ける表にする．"""
    rows: dict[str, dict[str, Any]] = {}
    for line in (out_dir / "results.jsonl").read_text().splitlines():
        row = json.loads(line)
        rows[str(row.get("question_id", row.get("qid")))] = row
    return rows


def main() -> None:
    """基準と各実行の一致の数を出す．"""
    base = load_rows(Path(sys.argv[1]))
    for path in sys.argv[2:]:
        rows = load_rows(Path(path))
        common = base.keys() & rows.keys()
        same = {k: sum(base[q].get(k) == rows[q].get(k) for q in common) for k in KEYS}
        print(json.dumps({"run": path, "n_common": len(common), **same}))


if __name__ == "__main__":
    main()
