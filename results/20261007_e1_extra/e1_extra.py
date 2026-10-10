"""E1 の追加の再集計（問題集ごとの all−none，s3 と s4 の top_doc_ids の一致，ragroute の PubMed 選択率，random で PubMed を外した PubMedQA）．"""
import json
from math import comb
from collections import defaultdict

def load(r):
    return {x["qid"]: x for x in map(json.loads, open(f"results/20261007_{r}/results.jsonl"))}

def mcnemar(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)

s3, s4, s5, s6, s7 = (load(r) for r in ("s3_med_all_ret", "s4_med_all_snip", "s5_med_ragroute", "s6_med_random", "s7_med_none"))
ok = [q for q in s4 if s4[q]["error"] is None]
print("shared ok", len(ok))
same = sum(s3[q]["top_doc_ids"] == s4[q]["top_doc_ids"] for q in ok)
print("top_doc_ids s3==s4:", same, "/", len(ok))
by = defaultdict(lambda: [0, 0, 0, 0, 0])
for q in ok:
    a, n = bool(s4[q]["correct"]), bool(s7[q]["correct"])
    b = by[s4[q]["bank"]]
    b[0] += 1; b[1] += a; b[2] += n; b[3] += a and not n; b[4] += n and not a
for bank, (cnt, a, n, b_, c_) in sorted(by.items()):
    print(f"{bank}: n={cnt} all={a/cnt:.3f} none={n/cnt:.3f} diff={(a-n)/cnt*100:+.1f}pt p={mcnemar(b_, c_):.2g}")
print("ragroute selects pubmed:", sum("pubmed" in s5[q]["selected_sources"] for q in s5) / len(s5))
pq = [q for q in ok if s4[q]["bank"] == "pubmedqa" and "pubmed" not in s6[q]["selected_sources"]]
acc = lambda d: sum(bool(d[q]["correct"]) for q in pq) / len(pq)
print(f"pubmedqa random-without-pubmed n={len(pq)} random={acc(s6):.3f} all={acc(s4):.3f} none={acc(s7):.3f}")
fails = [s4[q]["error"][:40] for q in s4 if s4[q]["error"]]
from collections import Counter
print("s4 errors:", Counter(fails))
