# Iteration 12 の事前調査：Iteration 11 の bench.py（偽のノード 30 個）を，sniffio の import の扱いを変えて流す．
# 使い方：python bench_sniffio.py {search|blocked} {base|pre} <問の数>．blocked は sys.modules["sniffio"] = None を置いてから bench.py を動かす．
# sniffio を入れた場合（installed）は search のまま PYTHONPATH に sniffio を足して流す．
# 偽のノードは bench.py serve <port> を 30 個起こす．2026-10-15 に gpu2 で流した出力は gpu2_out.txt．
# 注意：bench.py は httpx の INFO のログを自分のディレクトリ（Iteration 11 の prestudy）に {base,pre}.log として書くので，流した後に消す．
import runpy
import sys

BENCH = "/mnt/data-raid/ktakahashi/workspace/atrium/results/20261015_iter11_probe_body_summary/prestudy/bench.py"

if __name__ == "__main__":
    lookup, variant, n = sys.argv[1], sys.argv[2], sys.argv[3]
    if lookup == "blocked":
        sys.modules["sniffio"] = None  # import sniffio が sys.path を探さずに ImportError になる
    sys.argv = [BENCH, variant, n]
    runpy.run_path(BENCH, run_name="__main__")
