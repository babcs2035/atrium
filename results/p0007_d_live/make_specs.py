"""p0007 D の実行の予定（specs）を作る：ブロックランダム化（事前登録は journal の「p0007 D（事前登録）」）．

使い方: python3 make_specs.py > specs.txt
各行は `<run_id> <section>.<key>=<value> ...`（d_chain.sh が読む）．同じ (ブロック, 条件) の行は続けて並び，条件を替える行の前に deploy する．
"""

import random

SEED = 12
BLOCKS = (1, 2, 3)
PARALLELS = (1, 4, 8, 16, 30)
# parallel 1 だけ受信箱ごとの先頭 1 問（150 問）．ほかは先頭 2 問（300 問）
QUESTION_LIMIT_OF = {1: 1}
DEFAULT_QUESTION_LIMIT = 2
COMMON = "experiment.dataset=enronqa experiment.http_pool=per_node experiment.question_split=test experiment.requester_procs=1"
CENTRAL = "experiment.routing=central_local experiment.answer_mode=snippet_return answer.owner_m=2 answer.aggregate=max_score"
CONDITIONS: dict[str, tuple[str, tuple[int, ...]]] = {
    "central": (f"{CENTRAL} retrieval.fusion=rrf experiment.central_generation=requester", PARALLELS),
    "central_norrf": (f"{CENTRAL} retrieval.fusion=none experiment.central_generation=requester", (30,)),
    "semi": (f"{CENTRAL} retrieval.fusion=rrf experiment.central_generation=gpu_nodes", PARALLELS),
    "p2p": (
        "experiment.routing=bm25_probe experiment.answer_mode=owner_answer answer.owner_m=3 answer.aggregate=abstain "
        "retrieval.fusion=rrf experiment.central_generation=requester",
        PARALLELS,
    ),
    "p2p_ref": (
        "experiment.routing=hybrid_probe experiment.answer_mode=snippet_return answer.owner_m=2 answer.aggregate=max_score "
        "retrieval.fusion=rrf experiment.central_generation=requester",
        PARALLELS,
    ),
}

if __name__ == "__main__":
    for block in BLOCKS:
        rng = random.Random(SEED + block)
        names = list(CONDITIONS)
        rng.shuffle(names)
        print(f"# block {block}: {' '.join(names)}")
        for name in names:
            node_kvs, parallels = CONDITIONS[name]
            order = list(parallels)
            rng.shuffle(order)
            for parallel in order:
                limit = QUESTION_LIMIT_OF.get(parallel, DEFAULT_QUESTION_LIMIT)
                print(
                    f"p0007_d_{name}_p{parallel}_r{block} {COMMON} {node_kvs} "
                    f"experiment.parallel={parallel} experiment.question_limit={limit}"
                )
