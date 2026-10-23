"""p0007 F-6 の実行の予定（specs）を作る：標準構成に R3′（answer.aggregate=cross_encoder）を足した条件と足さない条件を交互に流す．

使い方: python3 make_specs.py > specs.txt
各行は `<run_id> <section>.<key>=<value> ...`（f6_chain.sh が読む）．事前登録は journal の「p0007 F-6（事前登録）」．
- smoke：cross_encoder の経路の確認（150 問）．失敗したら止まる
- agree：cross_encoder・save_candidates（300 問）．実機の選択とオフラインの R3′ の一致を見る（負荷の比較には使わない）
- base / ce の parallel 30・300 問・3 回：交互（r1: base→ce，r2: ce→base，r3: base→ce）
- parallel 1・150 問を各 1 回：待ちが無いときの cross-encoder の時間（サービス時間）
"""

COMMON = (
    "experiment.dataset=enronqa experiment.http_pool=per_node experiment.question_split=test "
    "experiment.requester_procs=1 experiment.routing=bm25_probe experiment.answer_mode=owner_answer "
    "experiment.central_generation=requester retrieval.fusion=rrf answer.owner_m=3"
)
BASE = "answer.aggregate=max_score answer.save_candidates=false"
CE = "answer.aggregate=cross_encoder answer.save_candidates=false"
LOAD_ORDER = {1: ("base", "ce"), 2: ("ce", "base"), 3: ("base", "ce")}


def line(run: str, cond: str, parallel: int, limit: int) -> str:
    """1 行（条件 cond の run_id・parallel・受信箱ごとの先頭の問数 limit）．"""
    kvs = BASE if cond == "base" else CE
    return f"{run} {COMMON} {kvs} experiment.parallel={parallel} experiment.question_limit={limit}"


if __name__ == "__main__":
    print(line("p0007_f6_smoke", "ce", 30, 1))
    print(
        f"p0007_f6_agree {COMMON} answer.aggregate=cross_encoder answer.save_candidates=true "
        "experiment.parallel=30 experiment.question_limit=2"
    )
    for rep, order in LOAD_ORDER.items():
        for cond in order:
            print(line(f"p0007_f6_{cond}_p30_r{rep}", cond, 30, 2))
    for cond in ("base", "ce"):
        print(line(f"p0007_f6_{cond}_p1_r1", cond, 1, 1))
