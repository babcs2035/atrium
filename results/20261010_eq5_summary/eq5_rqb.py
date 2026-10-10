"""EQ5（RQ-B）：受信箱が CPU と GPU のどちらに置かれたかで分けて，正答率・待ち時間・露出と McNemar を出す．"""

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path("/mnt/data-raid/ktakahashi/workspace/atrium")
sys.path.insert(0, str(REPO / "src"))

from atrium.analysis import compare_runs, merge_judgements, read_results, wilson_interval  # noqa: E402

RUNS = {
    "B1": "20261009_eq5_b1",
    "B1flood": "20261009_eq5_b1_flood",
    "B2": "20261009_eq5_b2",
    "B2_1p7b": "20261009_eq5_b2_1p7b",
    "B3": "20261009_eq5_b3",
    "B4": "20261009_eq5_b4",
}
GPU = {f"192.168.15.{i}" for i in range(100, 110)}

placement = json.loads((REPO / "results" / RUNS["B2"] / "placement.json").read_text())
host_of = {s: n["host"] for n in placement["nodes"] for s in n["shard_ids"]}


def load(run_id: str) -> list[dict]:
    d = REPO / "results" / run_id
    rows = read_results(d / "results.jsonl")
    rows = merge_judgements(rows, d / "judgements.jsonl")
    # merge_judgements は採点の無い行に correct を付けないので，数を出してから除く
    ungraded = [r for r in rows if r.get("error") is None and "correct" not in r]
    if ungraded:
        print(f"WARNING {run_id}: 採点の無い行 {len(ungraded)} 件を除外", file=sys.stderr)
    return [r for r in rows if r.get("error") is None and "correct" in r]


data = {k: load(v) for k, v in RUNS.items()}


def subset(rows: list[dict], where: str) -> list[dict]:
    if where == "all":
        return rows
    want_gpu = where == "gpu"
    return [r for r in rows if (host_of[r["bank"]] in GPU) == want_gpu]


print("## 条件ごと（受信箱の置き場所で分ける）")
print("| 条件 | 置き場所 | n | 正答率 [95%CI] | e2e p50 / p95 (s) | 外に出た本文（件/問） | judge_unparsed |")
print("|---|---|---|---|---|---|---|")
for name, rows in data.items():
    for where in ("cpu", "gpu"):
        s = subset(rows, where)
        k = sum(bool(r["correct"]) for r in s)
        lo, hi = wilson_interval(k, len(s))
        e2e = np.array([r["timings"]["e2e_s"] for r in s])
        exposed = np.mean([sum(len(v) if isinstance(v, list) else v for v in r["docs_exposed_by_source"].values()) for r in s])
        unparsed = sum(bool(r.get("judge_unparsed")) for r in s)
        print(
            f"| {name} | {where} | {len(s)} | {k / len(s):.3f} [{lo:.3f}, {hi:.3f}] | "
            f"{np.percentile(e2e, 50):.2f} / {np.percentile(e2e, 95):.2f} | {exposed:.2f} | {unparsed} |"
        )

PAIRS = [
    ("H-B1", "B2", "B3", "cpu"),
    ("H-B2a", "B2", "B1", "cpu"),
    ("H-B2b", "B4", "B1", "cpu"),
    ("H-B2c", "B4", "B2", "cpu"),
    ("参考", "B2_1p7b", "B1", "cpu"),
    ("参考", "B2_1p7b", "B2", "cpu"),
    ("大きさ", "B1", "B3", "cpu"),
    ("参考", "B2", "B1", "gpu"),
    ("参考", "B4", "B1", "gpu"),
    ("非オラクル", "B1flood", "B1", "all"),
]
print()
print("## 対応のある比較（McNemar の正確検定，両側）")
print("| 仮説 | A | B | 置き場所 | n | 正答率 A | 正答率 B | 差 A−B | A だけ正解 | B だけ正解 | p |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for tag, a, b, where in PAIRS:
    c = compare_runs(subset(data[a], where), subset(data[b], where))
    print(
        f"| {tag} | {a} | {b} | {where} | {c['n_shared']} | {c['accuracy_a']:.3f} | {c['accuracy_b']:.3f} | "
        f"{c['accuracy_a'] - c['accuracy_b']:+.3f} | {c['only_a_correct']} | {c['only_b_correct']} | {c['mcnemar_p']:.2e} |"
    )
