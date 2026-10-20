"""p0007 D の事後の感度分析（clean window）の実行の予定を作る．

本番の格子の 10 枚の GPU が，別の研究（他の利用者の DETR の学習）と同じ時間に使われていたため（journal の「p0007 D の結果」），
DETR が GPU を使っていない時間に，parallel 30 だけを central・semi・p2p で 3 回（c1〜c3）流し直す．格子の外の追加の実行で，
判定（verdict_original）には使わず，別の表で併記する．run_id は p0007_dc_<条件>_p30_c<番号>．
使い方: python3 make_specs_clean.py > specs_clean.txt（条件の設定は make_specs.py と同じ）
"""

import random

from make_specs import COMMON, CONDITIONS

SEED = 112
BLOCKS = (1, 2, 3)
NAMES = ("central", "semi", "p2p")
for block in BLOCKS:
    rng = random.Random(SEED + block)
    names = list(NAMES)
    rng.shuffle(names)
    print(f"# clean block {block}: {' '.join(names)}")
    for name in names:
        node_kvs, _ = CONDITIONS[name]
        print(f"p0007_dc_{name}_p30_c{block} {COMMON} {node_kvs} experiment.parallel=30 experiment.question_limit=2")
