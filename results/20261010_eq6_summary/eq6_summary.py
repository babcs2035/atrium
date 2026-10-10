"""EQ6：待ち時間（e2e_s，generate_s の p50・p95）を回ごと・条件ごと（3 回の中央値と最小〜最大）に出し，事前登録の 4 組を判定する．"""

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path("/mnt/data-raid/ktakahashi/workspace/atrium")
sys.path.insert(0, str(REPO / "src"))

from atrium.analysis import read_results  # noqa: E402

CONDS = {"B1": "b1", "B1 flood_score": "b1_flood", "B2": "b2", "B2'": "b2_1p7b", "B3": "b3", "B4": "b4"}
REPEATS = 3
GPU = {f"192.168.15.{i}" for i in range(100, 110)}
# 失敗した問が 1% を超えた実行は集計から除く（事前登録，§12 の 4）
MAX_FAIL_RATE = 0.01
PAIRS = [("B2", "B3"), ("B1", "B4"), ("B2", "B2'"), ("B1", "B1 flood_score")]
# B2・B2' は CPU の専門家に置かれた受信箱の質問に限った値も出す（事前登録）
CPU_SUBSET_CONDS = {"B2", "B2'"}
METRICS = ("e2e_s", "generate_s")


def summarize_run(run_id: str) -> dict | None:
    d = REPO / "results" / run_id
    if not (d / "results.jsonl").exists():
        return None
    rows = read_results(d / "results.jsonl")
    placement = json.loads((d / "placement.json").read_text())
    host_of = {s: n["host"] for n in placement["nodes"] for s in n["shard_ids"]}
    ok = [r for r in rows if r.get("error") is None]
    out = {"n": len(rows), "fail_rate": (len(rows) - len(ok)) / len(rows)}
    subsets = {"all": ok, "cpu": [r for r in ok if host_of[r["bank"]] not in GPU]}
    for where, s in subsets.items():
        for m in METRICS:
            v = np.array([r["timings"][m] for r in s])
            out[(where, m)] = (float(np.percentile(v, 50)), float(np.percentile(v, 95)), len(v))
    return out


runs = {c: [summarize_run(f"20261010_eq6_{key}_r{i}") for i in range(1, REPEATS + 1)] for c, key in CONDS.items()}

print("## 回ごと（全 300 問．B2・B2' は CPU の受信箱に限った値も）")
print("| 条件 | 回 | 置き場所 | n | 失敗率 | e2e p50 / p95 (s) | generate p50 / p95 (s) | 集計に使う |")
print("|---|---|---|---|---|---|---|---|")
for c, rs in runs.items():
    for i, r in enumerate(rs, 1):
        if r is None:
            print(f"| {c} | {i} | - | - | - | 未実行 | - | - |")
            continue
        use = "はい" if r["fail_rate"] <= MAX_FAIL_RATE else "いいえ（失敗率が 1% 超）"
        for where in ("all", "cpu") if c in CPU_SUBSET_CONDS else ("all",):
            e, g = r[(where, "e2e_s")], r[(where, "generate_s")]
            print(f"| {c} | {i} | {where} | {e[2]} | {r['fail_rate']:.3f} | {e[0]:.2f} / {e[1]:.2f} | {g[0]:.2f} / {g[1]:.2f} | {use} |")


def valid_values(c: str, where: str, metric: str, q: int) -> list[float]:
    return [r[(where, metric)][q] for r in runs[c] if r is not None and r["fail_rate"] <= MAX_FAIL_RATE]


print()
print("## 条件ごと（有効な回の中央値 [最小〜最大]）")
print("| 条件 | 置き場所 | 有効な回 | e2e p50 | e2e p95 | generate p50 | generate p95 |")
print("|---|---|---|---|---|---|---|")
for c in runs:
    for where in ("all", "cpu") if c in CPU_SUBSET_CONDS else ("all",):
        cells = []
        for m in METRICS:
            for q in (0, 1):
                v = valid_values(c, where, m, q)
                cells.append(f"{np.median(v):.2f} [{min(v):.2f}〜{max(v):.2f}]" if v else "-")
        print(f"| {c} | {where} | {len(valid_values(c, where, 'e2e_s', 0))} | " + " | ".join(cells) + " |")


def judge(a: str, b: str, where: str) -> str:
    va, vb = valid_values(a, where, "e2e_s", 0), valid_values(b, where, "e2e_s", 0)
    if len(va) < REPEATS or len(vb) < REPEATS:
        return f"保留（有効な回 {a}={len(va)}，{b}={len(vb)}）"
    if max(va) < min(vb):
        return f"差がある（{a} が速い）"
    if max(vb) < min(va):
        return f"差がある（{b} が速い）"
    return "差があるとは言えない（幅が重なる）"


print()
print("## 事前登録の 4 組（e2e p50 の 3 回の幅が重ならないときだけ差がある）")
for a, b in PAIRS:
    print(f"- {a} と {b}（全 300 問）：{judge(a, b, 'all')}")
    if a in CPU_SUBSET_CONDS and b in CPU_SUBSET_CONDS:
        print(f"  - 参考（CPU の受信箱に限る）：{judge(a, b, 'cpu')}")
