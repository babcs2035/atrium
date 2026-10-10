"""κ の参照採点に渡す 300 件を LLM を呼ばずに作り直し，プロンプトの長さを測る（500 の原因調べ）．"""

from pathlib import Path

from atrium import judge
from atrium.config import load_config
from atrium.paths import dataset_paths

RUNS = ["b1", "b1_flood", "b2", "b2_1p7b", "b3", "b4"]
captured = []


async def record_only(items, urls, model, cfg, transport=None):
    captured.extend(items)
    return {it.key: (True, "") for it in items}


judge.judge_items = record_only
cfg = load_config(Path("/app/config.yaml"))
paths = dataset_paths(Path("/data"), "enronqa")
run_dirs = [Path(f"/results/20261009_eq5_{r}") for r in RUNS]
e = cfg.data.require_enronqa()
judge.kappa_against_reference(cfg, paths, run_dirs, ["http://x"], e.judge_reference_model, e.judge_kappa_n)
lens = []
for it in captured:
    prompt = judge.JUDGE_PROMPT.format(email=it.email, question=it.question, gold=it.gold, answer=it.answer)
    lens.append((len(prompt), len(it.answer), it.key))
lens.sort(reverse=True)
print("n", len(captured))
for n_chars, n_ans, key in lens[:8]:
    print(f"{n_chars:>8} chars (~{n_chars // 4} tok)  answer {n_ans:>5} chars  {key}")
print("median chars", lens[len(lens) // 2][0])
