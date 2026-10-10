"""EQ6 B2 の 1 回目と 2・3 回目で，Ollama の中の時間（prefill / decode）と待ち時間の差を比べる．"""

import collections
import json
import statistics as s
from pathlib import Path

BASE = Path("/home/denjo/workspace/ktakahashi/atrium/results")
RUNS = ["20261010_eq6_b2_r1", "20261010_eq6_b2_r2", "20261010_eq6_b2_r3"]


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(s.median(xs), 2) if xs else None


per_node = {}
for run in RUNS:
    rows = [json.loads(line) for line in (BASE / run / "results.jsonl").open()]
    ok = [r for r in rows if not r.get("error")]
    answers = [a for r in ok for a in (r.get("node_answers") or [])]
    e2e = [r["timings"]["e2e_s"] for r in ok]
    inner = [(a.get("prefill_s") or 0) + (a.get("decode_s") or 0) for a in answers]
    print(
        run,
        "n", len(ok),
        "e2e p50", med(e2e),
        "prefill p50", med([a.get("prefill_s") for a in answers]),
        "decode p50", med([a.get("decode_s") for a in answers]),
        "inner p50", med(inner),
        "prompt_tok p50", med([a.get("prompt_tokens") for a in answers]),
        "out_tok p50", med([a.get("output_tokens") for a in answers]),
    )
    print("  timings keys:", sorted(ok[0]["timings"].keys()))
    by = collections.defaultdict(list)
    for a in answers:
        by[a["node_id"]].append(a)
    per_node[run] = {n: (med([x.get("prefill_s") for x in l]), med([x.get("decode_s") for x in l])) for n, l in by.items()}

# 1 回目が 2 回目より遅いノードを，prefill の比の大きい順に出す
r1, r2 = per_node[RUNS[0]], per_node[RUNS[1]]
ratios = []
for n in r1:
    if n in r2 and r1[n][0] and r2[n][0]:
        ratios.append((round(r1[n][0] / r2[n][0], 2), round(r1[n][1] / r2[n][1], 2) if r2[n][1] else None, n, r1[n], r2[n]))
ratios.sort(reverse=True)
print("prefill 比（r1/r2）の上位と下位: (prefill比, decode比, node, r1(prefill,decode), r2(prefill,decode))")
for x in ratios[:8]:
    print(" ", x)
print("  ...")
for x in ratios[-4:]:
    print(" ", x)
print("prefill 比の中央値", med([x[0] for x in ratios]), "decode 比の中央値", med([x[1] for x in ratios]))
