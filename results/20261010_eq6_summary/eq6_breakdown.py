"""EQ6：待ち時間の内訳（prefill・decode・その他）と入出力のトークン数を，条件・回・受信箱の置き場所ごとに出す．

「その他」は generate_s − (prefill_s + decode_s) で，Ollama の順番待ち・通信・委託の往復を含む．
B1・B3 は質問者が答えるので row["llm"] に，B2・B4 は専門家が答えるので node_answers[0] に時間が入る．
"""

import collections
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


def llm_fields(row: dict) -> dict:
    if row.get("node_answers"):
        a = row["node_answers"][0]
        return {"prefill": a["prefill_s"], "decode": a["decode_s"], "in_tok": a["prompt_tokens"], "out_tok": a["output_tokens"]}
    return {"prefill": row["llm"]["prefill_s"], "decode": row["llm"]["decode_s"], "in_tok": row["prompt_tokens"], "out_tok": row["output_tokens"]}


print("## 内訳（中央値．秒．その他 = generate − prefill − decode）")
print("| 条件 | 回 | 置き場所 | n | e2e | prefill | decode | その他 | その他 p95 | 入力トークン | 出力トークン |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
host_stats = {}
for c, key in CONDS.items():
    for i in range(1, REPEATS + 1):
        d = REPO / "results" / f"20261010_eq6_{key}_r{i}"
        if not (d / "results.jsonl").exists():
            continue
        rows = [r for r in read_results(d / "results.jsonl") if r.get("error") is None]
        placement = json.loads((d / "placement.json").read_text())
        host_of = {s: n["host"] for n in placement["nodes"] for s in n["shard_ids"]}
        for where in ("cpu", "gpu"):
            sub = [r for r in rows if (host_of[r["bank"]] in GPU) == (where == "gpu")]
            f = [llm_fields(r) for r in sub]
            e2e = np.array([r["timings"]["e2e_s"] for r in sub])
            gen = np.array([r["timings"]["generate_s"] for r in sub])
            pre = np.array([x["prefill"] for x in f])
            dec = np.array([x["decode"] for x in f])
            other = gen - pre - dec
            print(
                f"| {c} | {i} | {where} | {len(sub)} | {np.median(e2e):.2f} | {np.median(pre):.2f} | {np.median(dec):.2f} | "
                f"{np.median(other):.2f} | {np.percentile(other, 95):.2f} | {np.median([x['in_tok'] for x in f]):.0f} | "
                f"{np.median([x['out_tok'] for x in f]):.0f} |"
            )
        # 1 回目だけ，答える専門家（CPU）ごとの受信箱の数と待ち時間を出す（順番待ちの偏りを見る）
        if i == 1 and c in ("B2", "B2'"):
            banks_per_host = collections.Counter(host_of.values())
            by_host = collections.defaultdict(list)
            for r in rows:
                by_host[host_of[r["bank"]]].append(r)
            host_stats[c] = []
            for h, rs in by_host.items():
                if h in GPU:
                    continue
                f = [llm_fields(r) for r in rs]
                host_stats[c].append(
                    (
                        h,
                        banks_per_host[h],
                        len(rs),
                        float(np.median([r["timings"]["e2e_s"] for r in rs])),
                        float(max(r["timings"]["e2e_s"] for r in rs)),
                        float(np.median([x["prefill"] / x["in_tok"] * 1000 for x in f])),
                        float(np.median([x["decode"] / max(x["out_tok"], 1) * 1000 for x in f])),
                        float(np.median([x["out_tok"] for x in f])),
                        float(np.median([r["timings"]["generate_s"] - x["prefill"] - x["decode"] for r, x in zip(rs, f)])),
                        float(max(r["timings"]["generate_s"] - x["prefill"] - x["decode"] for r, x in zip(rs, f))),
                    )
                )

# EQ6 の 300 問では CPU の専門家はどれも受信箱を 5 個持つので，専門家の差は受信箱の数ではなく計算の速さと出力の長さで見る
for c, stats in host_stats.items():
    print()
    print(f"## {c} の 1 回目：CPU の専門家ごと（e2e p50 の遅い順．ms/トークンは問ごとの中央値）")
    print("| 専門家 | 受信箱 | 問 | e2e p50 | e2e 最大 | prefill ms/入力トークン | decode ms/出力トークン | 出力トークン | その他 p50 | その他 最大 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for h, nb, nq, p50, mx, pre_ms, dec_ms, out, oth, oth_mx in sorted(stats, key=lambda x: -x[3]):
        print(f"| {h} | {nb} | {nq} | {p50:.2f} | {mx:.2f} | {pre_ms:.2f} | {dec_ms:.1f} | {out:.0f} | {oth:.2f} | {oth_mx:.2f} |")
