"""診断：term_sketch の採点が，Python の文字列のハッシュの種（PYTHONHASHSEED）で変わるか．owner-eval と同じ関数を使う．"""
import json, os
from pathlib import Path
import numpy as np
from atrium.advert_eval import build_methods, load_inboxes, load_query_set, owner_probes, ranks_of_gold, hit_at
from atrium.config import load_config
from atrium.data_enronqa import read_gold
from atrium.paths import dataset_paths

cfg = load_config(Path("/app/config.yaml"))
paths = dataset_paths(Path("/data"), "enronqa")
e = cfg.data.require_enronqa()
inboxes = load_inboxes(cfg, paths)
test = load_query_set(cfg, paths, "test")
gold = read_gold(paths)
pe, pt, po = owner_probes(cfg, inboxes, {gold[q]["path"] for q in test.qids})
m = next(x for x in build_methods(cfg, inboxes, paths) if x.name == "term_sketch")
q, _, ep = m.run(e.term_sketch_size, test.emb, test.texts, pe, pt)
print(os.environ.get("PYTHONHASHSEED"), "Q top1 %.6f" % hit_at(ranks_of_gold(q, test.gold), 1), "E top1 %.6f" % hit_at(ranks_of_gold(ep, po), 1), "n_ties_Q", int(((q == q[np.arange(len(q)), test.gold][:, None]).sum(axis=1) > 1).sum()))
