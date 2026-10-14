# p0003 S5：実行の top_doc_ids を s3（routing=all）と比べる．全問と，test の分割に限った値を出す．
import json
import sys
from pathlib import Path

from atrium.analysis import read_results
from atrium.loso import top_doc_overlap

run_dir = Path(sys.argv[1])
ref_dir = Path(sys.argv[2])
run_rows = read_results(run_dir / "results.jsonl")
ref_rows = read_results(ref_dir / "results.jsonl")
print("all", json.dumps(top_doc_overlap(run_rows, ref_rows)))

# test の質問 id は，analyze が制御点から取得して集計に使う split.json と同じものを使う
split_path = Path(sys.argv[3])
test_qids = json.loads(split_path.read_text(encoding="utf-8")).get("test")
if test_qids:
    keep = set(test_qids)
    print(
        "test",
        json.dumps(
            top_doc_overlap(
                [r for r in run_rows if r["qid"] in keep], [r for r in ref_rows if r["qid"] in keep]
            )
        ),
    )
