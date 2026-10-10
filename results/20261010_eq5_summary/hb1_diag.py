"""H-B1 の差の原因調べ：CPU 側の受信箱で B2 と B3 のプロンプト長・出力長・打ち切り・抜き出しの失敗を比べる．"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path("/mnt/data-raid/ktakahashi/workspace/atrium")
sys.path.insert(0, str(REPO / "src"))

from atrium.analysis import merge_judgements, read_results  # noqa: E402

GPU = {f"192.168.15.{i}" for i in range(100, 110)}
placement = json.loads((REPO / "results/20261009_eq5_b2/placement.json").read_text())
host_of = {s: n["host"] for n in placement["nodes"] for s in n["shard_ids"]}


def load(run_id: str) -> dict[str, dict]:
    d = REPO / "results" / run_id
    rows = merge_judgements(read_results(d / "results.jsonl"), d / "judgements.jsonl")
    return {r["qid"]: r for r in rows if r.get("error") is None and host_of[r["bank"]] not in GPU}


b2 = load("20261009_eq5_b2")
b3 = load("20261009_eq5_b3")
shared = sorted(set(b2) & set(b3))


def b2_tokens(r: dict, key: str) -> int:
    return r["node_answers"][0][key]


def pct(xs: list[float]) -> str:
    a = np.array(xs)
    return f"p50={np.percentile(a, 50):.0f} p95={np.percentile(a, 95):.0f} max={a.max():.0f}"


print("n_shared", len(shared))
print("node_answers の数", Counter(len(b2[q]["node_answers"]) for q in shared))
print("B2 n_context_docs", Counter(b2[q]["node_answers"][0]["n_context_docs"] for q in shared))
print("B3 snippets_exposed", Counter(b3[q]["snippets_exposed"] for q in shared))
print("prompt_tokens B2", pct([b2_tokens(b2[q], "prompt_tokens") for q in shared]))
print("prompt_tokens B3", pct([b3[q]["prompt_tokens"] for q in shared]))
d = [b2_tokens(b2[q], "prompt_tokens") - b3[q]["prompt_tokens"] for q in shared]
print("prompt_tokens 差 B2−B3", pct(d), "min", min(d))
print("output_tokens B2", pct([b2_tokens(b2[q], "output_tokens") for q in shared]))
print("output_tokens B3", pct([b3[q]["output_tokens"] for q in shared]))
for name, rows, get in (("B2", b2, lambda r: b2_tokens(r, "output_tokens")), ("B3", b3, lambda r: r["output_tokens"])):
    capped = [q for q in shared if get(rows[q]) >= 2048]
    empty = [q for q in shared if not (rows[q].get("final_answer") or "").strip()]
    print(f"{name}: num_predict に達した {len(capped)}（うち正解 {sum(rows[q]['correct'] for q in capped)}），final_answer が空 {len(empty)}")

# 出力が長い問ほど誤りやすいか（打ち切り・繰り返しの兆候）
for name, rows, get in (("B2", b2, lambda r: b2_tokens(r, "output_tokens")), ("B3", b3, lambda r: r["output_tokens"])):
    for lo, hi in ((0, 100), (100, 300), (300, 1000), (1000, 99999)):
        s = [q for q in shared if lo <= get(rows[q]) < hi]
        if s:
            print(f"{name} output_tokens [{lo},{hi}) n={len(s)} acc={np.mean([rows[q]['correct'] for q in s]):.3f}")

# 不一致の組の例
only_b2 = [q for q in shared if b2[q]["correct"] and not b3[q]["correct"]]
only_b3 = [q for q in shared if b3[q]["correct"] and not b2[q]["correct"]]
print("only_b2", len(only_b2), "only_b3", len(only_b3))
for tag, qs in (("B2 だけ正解", only_b2[:4]), ("B3 だけ正解", only_b3[:4])):
    for q in qs:
        print(f"--- {tag} {q}")
        print("gold:", b2[q]["gold"][:200])
        print("B2 :", repr((b2[q]["final_answer"] or "")[:200]), "out_tok", b2_tokens(b2[q], "output_tokens"))
        print("B3 :", repr((b3[q]["final_answer"] or "")[:200]), "out_tok", b3[q]["output_tokens"])
        print("B3 raw:", repr(b3[q]["answer"][:300]))
