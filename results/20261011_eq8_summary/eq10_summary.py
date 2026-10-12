"""EQ10：待ち時間（e2e_s，generate_s の p50・p95）を回ごと・条件ごとに出し，事前登録の 5 組を判定する（EQ6 と同じ方法）．"""

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path("/mnt/data-raid/ktakahashi/workspace/atrium")
sys.path.insert(0, str(REPO / "src"))

from atrium.analysis import read_results  # noqa: E402

REPEATS = 3
# k = 5・parallel 4 は EQ6 の 3 回を使う（事前登録．EQ6 から後の src の差分は HF の版の固定だけ）
CONDS = {
    "B2 k1": "20261011_eq10_b2_k1_r{i}",
    "B2 k3": "20261011_eq10_b2_k3_r{i}",
    "B2 k5（EQ6）": "20261010_eq6_b2_r{i}",
    "B2' k1": "20261011_eq10_b2_1p7b_k1_r{i}",
    "B2' k3": "20261011_eq10_b2_1p7b_k3_r{i}",
    "B2' k5（EQ6）": "20261010_eq6_b2_1p7b_r{i}",
    "B1 p1": "20261011_eq10_b1_p1_r{i}",
    "B4 p1": "20261011_eq10_b4_p1_r{i}",
    "B1 p4（EQ6）": "20261010_eq6_b1_r{i}",
}
GPU = {f"192.168.15.{i}" for i in range(100, 110)}
# 失敗した問が 1% を超えた実行は集計から除く（事前登録，§12 の 4）
MAX_FAIL_RATE = 0.01
# B2・B2' は CPU の専門家に置かれた受信箱の質問に限った値で判定する（事前登録）
CPU_SUBSET_CONDS = {c for c in CONDS if c.startswith("B2")}
PAIRS = [
    ("B2 k1", "B2 k5（EQ6）", "cpu"),
    ("B2 k3", "B2 k5（EQ6）", "cpu"),
    ("B2' k1", "B2' k5（EQ6）", "cpu"),
    ("B2' k3", "B2' k5（EQ6）", "cpu"),
    ("B1 p1", "B4 p1", "all"),
]
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


runs = {c: [summarize_run(t.format(i=i)) for i in range(1, REPEATS + 1)] for c, t in CONDS.items()}

print("## 回ごと（全 300 問．B2・B2' は CPU の受信箱に限った値も）")
print(
    "| 条件 | 回 | 置き場所 | n | 失敗率 | e2e p50 / p95 (s) | generate p50 / p95 (s) | 集計に使う |"
)
print("|---|---|---|---|---|---|---|---|")
for c, rs in runs.items():
    for i, r in enumerate(rs, 1):
        if r is None:
            print(f"| {c} | {i} | - | - | - | 未実行 | - | - |")
            continue
        use = "はい" if r["fail_rate"] <= MAX_FAIL_RATE else "いいえ（失敗率が 1% 超）"
        for where in ("all", "cpu") if c in CPU_SUBSET_CONDS else ("all",):
            e, g = r[(where, "e2e_s")], r[(where, "generate_s")]
            print(
                f"| {c} | {i} | {where} | {e[2]} | {r['fail_rate']:.3f} | {e[0]:.2f} / {e[1]:.2f} | {g[0]:.2f} / {g[1]:.2f} | {use} |"
            )


def valid_values(c: str, where: str, metric: str, q: int) -> list[float]:
    return [
        r[(where, metric)][q] for r in runs[c] if r is not None and r["fail_rate"] <= MAX_FAIL_RATE
    ]


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
        print(
            f"| {c} | {where} | {len(valid_values(c, where, 'e2e_s', 0))} | "
            + " | ".join(cells)
            + " |"
        )


def judge(a: str, b: str, where: str, metric: str) -> str:
    va, vb = valid_values(a, where, metric, 0), valid_values(b, where, metric, 0)
    if len(va) < REPEATS or len(vb) < REPEATS:
        return f"保留（有効な回 {a}={len(va)}，{b}={len(vb)}）"
    if max(va) < min(vb):
        return f"差がある（{a} が速い）"
    if max(vb) < min(va):
        return f"差がある（{b} が速い）"
    return "差があるとは言えない（幅が重なる）"


print()
print("## 事前登録の 5 組（p50 の 3 回の幅が重ならないときだけ差がある）")
for a, b, where in PAIRS:
    print(
        f"- {a} と {b}（{where}）：e2e {judge(a, b, where, 'e2e_s')}．generate {judge(a, b, where, 'generate_s')}"
    )

print()
print("## 参考：質問者での順番待ち（B1 の parallel 4 − parallel 1．判定しない）")
for m in METRICS:
    p1, p4 = valid_values("B1 p1", "all", m, 0), valid_values("B1 p4（EQ6）", "all", m, 0)
    if p1 and p4:
        print(
            f"- {m} p50 の中央値：p4 {np.median(p4):.2f} − p1 {np.median(p1):.2f} = {np.median(p4) - np.median(p1):+.2f} s"
        )
