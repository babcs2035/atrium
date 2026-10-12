"""EQ8（文脈の通数と正答率）と EQ9（流し直しによる揺れ）：journal の「EQ8〜EQ10 の事前登録」の比較と判定を出す．"""

import json
import statistics as st
import sys
from itertools import combinations
from pathlib import Path

REPO = Path("/mnt/data-raid/ktakahashi/workspace/atrium")
sys.path.insert(0, str(REPO / "src"))

from atrium.analysis import (  # noqa: E402
    compare_runs,
    merge_judgements,
    read_results,
    wilson_interval,
)

GPU = {f"192.168.15.{i}" for i in range(100, 110)}
# 失敗した問が 1% を超えた実行は集計から除く（事前登録，§12 の 4）
MAX_FAIL_RATE = 0.01
ALPHA = 0.05
# ひな形の文だけの回答（EQ5・EQ6 と同じ文字列．prompts.py の回答形式の例）
TEMPLATE = "The answer to the question"
KS = (1, 3, 5)
EQ8 = {
    "B1": "20261011_eq8_b1_k{k}",
    "B2": "20261011_eq8_b2_k{k}",
    "B2'": "20261011_eq8_b2_1p7b_k{k}",
}
EQ9_NEW = {
    "B1": "20261011_eq8_b1_k5",
    "B2": "20261011_eq8_b2_k5",
    "B2'": "20261011_eq8_b2_1p7b_k5",
    "B3": "20261011_eq9_b3_k5",
}
EQ5 = {
    "B1": "20261009_eq5_b1",
    "B1 flood_score": "20261009_eq5_b1_flood",
    "B2": "20261009_eq5_b2",
    "B2'": "20261009_eq5_b2_1p7b",
    "B3": "20261009_eq5_b3",
    "B4": "20261009_eq5_b4",
}


def load(run_id: str) -> tuple[list[dict] | None, str]:
    """失敗していない行（CPU に置かれた受信箱かを _cpu に付け，採点があれば正誤も付ける）と，状態の説明を返す．"""
    d = REPO / "results" / run_id
    if not (d / "results.jsonl").exists():
        return None, "未実行"
    rows = read_results(d / "results.jsonl")
    if len({r["qid"] for r in rows}) != len(rows):
        raise SystemExit(
            f"{run_id}: results.jsonl に同じ qid の行が複数ある（再開の行をまとめていない）"
        )
    ok = [r for r in rows if r.get("error") is None]
    fail = 1 - len(ok) / len(rows)
    if fail > MAX_FAIL_RATE:
        return None, f"除外（失敗率 {fail:.3f}）"
    placement = json.loads((d / "placement.json").read_text())
    host_of = {s: n["host"] for n in placement["nodes"] for s in n["shard_ids"]}
    for r in ok:
        r["_cpu"] = host_of[r["bank"]] not in GPU
    if not (d / "judgements.jsonl").exists():
        return ok, f"失敗率 {fail:.3f}，未採点"
    ok = merge_judgements(ok, d / "judgements.jsonl")
    ungraded = sum("correct" not in r for r in ok)
    if ungraded:
        print(f"WARNING {run_id}: 採点の無い行 {ungraded} 件（比較では除かれる）", file=sys.stderr)
    return ok, f"失敗率 {fail:.3f}"


def subset(rows: list[dict], where: str) -> list[dict]:
    if where == "all":
        return rows
    return [r for r in rows if r["_cpu"] == (where == "cpu")]


def graded(rows: list[dict] | None) -> bool:
    return rows is not None and any("correct" in r for r in rows)


def prompt_tokens(r: dict) -> int:
    # B1 は質問者の LLM が答えるので行の直下に，B2・B3 は専門家が答えるので node_answers に入る
    na = r.get("node_answers") or []
    return na[0]["prompt_tokens"] if na else r["prompt_tokens"]


def exposed(r: dict) -> int:
    return sum(len(v) if isinstance(v, list) else v for v in r["docs_exposed_by_source"].values())


def holm(ps: list[float]) -> list[float]:
    """Holm 法で補正した p 値（入力の順に返す）．"""
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj = [0.0] * len(ps)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - rank) * ps[i]))
        adj[i] = running
    return adj


def fmt_acc(rows: list[dict]) -> str:
    k = sum(bool(r["correct"]) for r in rows)
    lo, hi = wilson_interval(k, len(rows))
    return f"{k / len(rows):.3f} [{lo:.3f}, {hi:.3f}]"


eq8 = {(c, k): load(t.format(k=k)) for c, t in EQ8.items() for k in KS}

print("## EQ8：条件ごと（受信箱の置き場所で分ける）")
print(
    "| 条件 | k | 置き場所 | n | 正答率 [95%CI] | ひな形を不正解に直した正答率 | 外に出た本文（件/問） | 入力トークン p50 | 状態 |"
)
print("|---|---|---|---|---|---|---|---|---|")
for (c, k), (rows, status) in eq8.items():
    if rows is None:
        print(f"| {c} | {k} | - | - | - | - | - | - | {status} |")
        continue
    for where in ("cpu", "gpu"):
        s = subset(rows, where)
        if not s:
            continue
        acc = fmt_acc(s) if graded(s) else "-"
        fixed = (
            [
                {**r, "correct": r["correct"] and TEMPLATE not in (r.get("final_answer") or "")}
                for r in s
            ]
            if graded(s)
            else []
        )
        acc_fixed = fmt_acc(fixed) if fixed else "-"
        ex = st.mean(exposed(r) for r in s)
        tok = st.median(prompt_tokens(r) for r in s)
        print(
            f"| {c} | {k} | {where} | {len(s)} | {acc} | {acc_fixed} | {ex:.2f} | {tok:.0f} | {status} |"
        )

print()
print("## EQ8：事前登録の 6 組（CPU の受信箱，McNemar の正確検定，Holm 法で 6 組を補正）")
pairs = [(c, k) for c in EQ8 for k in (1, 3)]
results = []
for c, k in pairs:
    a, b = eq8[(c, k)][0], eq8[(c, 5)][0]
    results.append(
        compare_runs(subset(a, "cpu"), subset(b, "cpu")) if graded(a) and graded(b) else None
    )
if any(r is None for r in results):
    print("保留：6 組のうち採点済みでない組がある（Holm の補正は 6 組がそろってから行う）")
else:
    adj = holm([r["mcnemar_p"] for r in results])
    print(
        "| 条件 | A | B | n | 正答率 A | 正答率 B | 差 A−B | A だけ正解 | B だけ正解 | p | 補正後の p | 判定 |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (c, k), r, p_adj in zip(pairs, results, adj, strict=True):
        verdict = "差がある" if p_adj < ALPHA else "差があるとは言えない"
        print(
            f"| {c} | k={k} | k=5 | {r['n_shared']} | {r['accuracy_a']:.3f} | {r['accuracy_b']:.3f} | "
            f"{r['accuracy_a'] - r['accuracy_b']:+.3f} | {r['only_a_correct']} | {r['only_b_correct']} | "
            f"{r['mcnemar_p']:.2e} | {p_adj:.2e} | {verdict} |"
        )

print()
print("## EQ8：参考（GPU の受信箱．補正しない）")
for c, k in pairs:
    a, b = eq8[(c, k)][0], eq8[(c, 5)][0]
    if graded(a) and graded(b):
        r = compare_runs(subset(a, "gpu"), subset(b, "gpu"))
        print(
            f"- {c} k={k} 対 k=5：n={r['n_shared']}，{r['accuracy_a']:.3f} 対 {r['accuracy_b']:.3f}，p={r['mcnemar_p']:.2e}"
        )

# ---- EQ9 ----
eq5 = {c: load(v) for c, v in EQ5.items()}
eq9_new = {c: load(v) for c, v in EQ9_NEW.items()}

print()
print("## EQ9：流し直し（今回）と EQ5 の比較（CPU の受信箱）")
print(
    "| 条件 | n | final_answer の一致 | 正答率 今回 | 正答率 EQ5 | 差 今回−EQ5 | 今回だけ正解 | EQ5 だけ正解 | p |"
)
print("|---|---|---|---|---|---|---|---|---|")
width: float | None = 0.0
for c, (new, status) in eq9_new.items():
    old = eq5[c][0]
    if new is None or old is None:
        print(f"| {c} | - | - | - | - | - | - | - | {status} |")
        width = None
        continue
    new_cpu = {r["qid"]: r for r in subset(new, "cpu")}
    old_cpu = {r["qid"]: r for r in subset(old, "cpu")}
    shared = set(new_cpu) & set(old_cpu)
    same = sum(new_cpu[q]["final_answer"] == old_cpu[q]["final_answer"] for q in shared)
    if not graded(new):
        print(f"| {c} | {len(shared)} | {same / len(shared):.3f} | - | - | - | - | - | 未採点 |")
        width = None
        continue
    r = compare_runs(list(new_cpu.values()), list(old_cpu.values()))
    diff = r["accuracy_a"] - r["accuracy_b"]
    if width is not None:
        width = max(width, abs(diff))
    print(
        f"| {c} | {r['n_shared']} | {same / len(shared):.3f} | {r['accuracy_a']:.3f} | {r['accuracy_b']:.3f} | "
        f"{diff:+.3f} | {r['only_a_correct']} | {r['only_b_correct']} | {r['mcnemar_p']:.2e} |"
    )

print()
if width is None:
    print("流し直しの幅：保留（4 条件がそろっていない）")
else:
    print(f"流し直しの幅（同じ条件の 2 回の正答率の差の絶対値の最大，CPU の受信箱）：{width:.3f}")
    print()
    print("## EQ9：EQ5 の条件間の差（CPU の受信箱）と流し直しの幅")
    print("| A | B | n | 正答率 A | 正答率 B | 差の絶対値 | 読み |")
    print("|---|---|---|---|---|---|---|")
    for a, b in combinations(EQ5, 2):
        r = compare_runs(subset(eq5[a][0], "cpu"), subset(eq5[b][0], "cpu"))
        d = abs(r["accuracy_a"] - r["accuracy_b"])
        verdict = "流し方の揺れと区別できない" if d < width else "幅より大きい"
        print(
            f"| {a} | {b} | {r['n_shared']} | {r['accuracy_a']:.3f} | {r['accuracy_b']:.3f} | {d:.3f} | {verdict} |"
        )

print()
print(
    "## EQ9：H-B1 の再判定（B2 と B3，k = 5，CPU の受信箱．p ≥ 0.05 で同等．EQ5 の判定は書き換えない）"
)
for tag, b2, b3 in (
    ("今回", eq9_new["B2"][0], eq9_new["B3"][0]),
    ("EQ5", eq5["B2"][0], eq5["B3"][0]),
):
    if not (graded(b2) and graded(b3)):
        print(f"- {tag}：保留（未実行か未採点）")
        continue
    r = compare_runs(subset(b2, "cpu"), subset(b3, "cpu"))
    verdict = "同等" if r["mcnemar_p"] >= ALPHA else "同等とは言えない"
    print(
        f"- {tag}：n={r['n_shared']}，B2 {r['accuracy_a']:.3f} 対 B3 {r['accuracy_b']:.3f}，"
        f"B2 だけ正解 {r['only_a_correct']}，B3 だけ正解 {r['only_b_correct']}，p={r['mcnemar_p']:.2e} → {verdict}"
    )
