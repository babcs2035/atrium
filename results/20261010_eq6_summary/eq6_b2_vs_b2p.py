# EQ6 の B2（qwen3:0.6b）と B2'（qwen3:1.7b）の追加の診断．
# 裾の内訳，3 回の再現性，同じ問・同じ専門家での比，EQ5（parallel 30）との答えの一致を出す．
import json
import statistics as st

ROOT = "/mnt/data-raid/ktakahashi/workspace/atrium/results/"
CONDS = {"B2": ("20261010_eq6_b2_r", "20261009_eq5_b2"), "B2'": ("20261010_eq6_b2_1p7b_r", "20261009_eq5_b2_1p7b")}
CPU_PREFIXES = ("192.168.13.", "192.168.14.")
TEMPLATE = "The answer to the question"


def load_rows_by_qid(path):
    # EQ5 の results.jsonl には再試行の前の失敗の行が残るので，read_results と同じく同じ qid の最後の行を採る
    rows = {}
    for line in open(path):
        x = json.loads(line)
        rows[x["qid"]] = x
    return rows


runs = {c: {r: load_rows_by_qid(f"{ROOT}{p}{r}/results.jsonl") for r in (1, 2, 3)} for c, (p, _) in CONDS.items()}

for c, by_run in runs.items():
    print(f"## {c}")
    for r, rows in by_run.items():
        xs = list(rows.values())
        cpu = [x for x in xs if x["node_answers"][0]["node_id"].startswith(CPU_PREFIXES)]
        hit_cap = [x["qid"] for x in xs if x["node_answers"][0].get("output_tokens", 0) >= 2048]
        echo = sum(1 for x in cpu if TEMPLATE in (x.get("final_answer") or ""))
        errors = sum(1 for x in xs if x.get("error"))
        print(f"run {r}: n={len(xs)} cpu={len(cpu)} errors={errors} hit2048={hit_cap} template_echo_cpu={echo}/{len(cpu)}")
        for x in sorted(xs, key=lambda y: -y["timings"]["e2e_s"])[:3]:
            na = x["node_answers"][0]
            other = x["timings"]["generate_s"] - na["prefill_s"] - na["decode_s"]
            print(f"  slow {x['qid']} bank={x['bank']} node={na['node_id']} e2e={x['timings']['e2e_s']:.1f} "
                  f"prefill={na['prefill_s']:.1f} decode={na['decode_s']:.1f} other={other:.1f} "
                  f"in={na['prompt_tokens']} out={na['output_tokens']}")
        ratios = [x["node_answers"][0]["prefill_s"] / x["node_answers"][0]["prompt_tokens"] * 1000 for x in cpu]
        q = st.quantiles(ratios, n=20)
        print(f"  cpu prefill ms/token p5={q[0]:.2f} p50={st.median(ratios):.2f} p95={q[-1]:.2f}")

    # 3 回の答えの一致（temperature 0 の再現性）
    qids = list(by_run[1])
    same = sum(1 for q in qids if by_run[1][q]["final_answer"] == by_run[2][q]["final_answer"] == by_run[3][q]["final_answer"])
    same_out = sum(
        1 for q in qids
        if by_run[1][q]["node_answers"][0]["output_tokens"]
        == by_run[2][q]["node_answers"][0]["output_tokens"]
        == by_run[3][q]["node_answers"][0]["output_tokens"]
    )
    print(f"answers identical across 3 runs: {same}/{len(qids)}; output tokens identical: {same_out}/{len(qids)}")

    # EQ5（同じ設定で parallel 30）との一致．同じ問・同じ専門家・同じ重みで並列度だけが違う
    eq5 = load_rows_by_qid(f"{ROOT}{CONDS[c][1]}/results.jsonl")
    shared = [q for q in qids if q in eq5]
    same_node = sum(1 for q in shared if eq5[q]["node_answers"][0]["node_id"] == by_run[1][q]["node_answers"][0]["node_id"])
    same_ans = [q for q in shared if eq5[q]["final_answer"] == by_run[1][q]["final_answer"]]
    cpu_shared = [q for q in shared if by_run[1][q]["node_answers"][0]["node_id"].startswith(CPU_PREFIXES)]
    cpu_same = sum(1 for q in cpu_shared if q in set(same_ans))
    print(f"vs EQ5: shared={len(shared)} same_node={same_node} same_answer={len(same_ans)} "
          f"(cpu {cpu_same}/{len(cpu_shared)}, gpu {len(same_ans) - cpu_same}/{len(shared) - len(cpu_shared)})")

# 同じ問・同じ専門家・同じ入力トークン数での B2' / B2 の比（1 回目，CPU の受信箱）
b2, b2p = runs["B2"][1], runs["B2'"][1]
pre, dec, e2e, outr = [], [], [], []
slower = 0
for q, x in b2p.items():
    na = x["node_answers"][0]
    if not na["node_id"].startswith(CPU_PREFIXES):
        continue
    nb = b2[q]["node_answers"][0]
    assert nb["node_id"] == na["node_id"] and nb["prompt_tokens"] == na["prompt_tokens"]
    pre.append(na["prefill_s"] / nb["prefill_s"])
    dec.append((na["decode_s"] / na["output_tokens"]) / (nb["decode_s"] / nb["output_tokens"]))
    outr.append(na["output_tokens"] / nb["output_tokens"])
    e2e.append(x["timings"]["e2e_s"] / b2[q]["timings"]["e2e_s"])
    slower += x["timings"]["e2e_s"] > b2[q]["timings"]["e2e_s"]
print("## B2' / B2（1 回目，CPU の受信箱，同じ問・同じ専門家・同じ入力トークン数）")
print(f"prefill ratio median={st.median(pre):.2f} range=[{min(pre):.2f},{max(pre):.2f}]")
print(f"decode per-token ratio median={st.median(dec):.2f}")
print(f"output tokens ratio median={st.median(outr):.2f}")
print(f"e2e ratio median={st.median(e2e):.2f}; B2' slower on {slower}/{len(e2e)}")

# KV キャッシュに当たった無効な B2 の 2・3 回目と，有効な 1 回目の答えの一致．
# temperature 0・同じプロンプトでも，プロンプトの KV を再計算するか再利用するかで生成が分かれるかを見る
print("## B2 の無効な回（KV キャッシュに当たった）と有効な 1 回目の答えの一致")
for r in (2, 3):
    warm = load_rows_by_qid(f"{ROOT}20261010_eq6_invalid_warmcache/20261010_eq6_b2_r{r}/results.jsonl")
    cpu_q = [q for q in b2 if b2[q]["node_answers"][0]["node_id"].startswith(CPU_PREFIXES)]
    gpu_q = [q for q in b2 if q not in set(cpu_q)]
    same_cpu = sum(1 for q in cpu_q if warm[q]["final_answer"] == b2[q]["final_answer"])
    same_gpu = sum(1 for q in gpu_q if warm[q]["final_answer"] == b2[q]["final_answer"])
    prefill = st.median(warm[q]["node_answers"][0]["prefill_s"] for q in cpu_q)
    print(f"warm r{r}: same_answer={same_cpu + same_gpu}/{len(b2)} (cpu {same_cpu}/{len(cpu_q)}, gpu {same_gpu}/{len(gpu_q)}); "
          f"cpu prefill median={prefill:.3f}s")
