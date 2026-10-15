"""Iteration 14：retrieve の本文の作り方（retrieve_body）の負荷試験の集計（事前登録の指標・G1〜G6・判定規則・記録の指標の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261016_iter14_retrieve_body_summary/iter14_summary.py > results/20261016_iter14_retrieve_body_summary/iter14_summary.out
    uv run python results/20261016_iter14_retrieve_body_summary/iter14_summary.py --prefix 20261017_iter14   # 実行日が違うとき
    uv run python results/20261016_iter14_retrieve_body_summary/iter14_summary.py --self-test              # 合成の結果で判定の部分だけ確かめる

指標の定義は iter6_summary.py の summarize・rel・fmt と iter8_summary.py の load を再利用する（処理量 = 成功した問の数 ÷ run_meta.json の finished_at − started_at）．
主の量は組（同じ回・同じ方式・同じ N の pr と pe）の比 q = pe の処理量 ÷ pr の処理量．
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

sys.path.insert(0, str(Path("results/20261015_iter6_load_summary")))
sys.path.insert(0, str(Path("results/20261015_iter8_load_summary")))
from iter6_summary import fmt, rel  # noqa: E402
from iter8_summary import load  # noqa: E402

from atrium.analysis import read_results  # noqa: E402

RESULTS = Path("results")
REPEATS = {1: (1, 2, 3, 4, 5), 8: (1, 2, 3)}  # N → 回
BODIES = {"pr": "per_request", "pe": "pre_encoded"}
METHODS = {"fs": ("flood_score", None), "dp12": ("device_probe", 12)}
TEST_HIT2 = {"fs": 0.8041, "dp12": 0.6830}
HIT_TOL = 5e-4
FAIL_LIMIT = 0.01
ENV_THR = (11.127, 14.247)
ENV_P95 = (3.053, 4.067)
RATIO_MIN = 1.10  # Iteration 13 と同じ範囲の規則（記録）
Q_MEDIAN_MIN = 1.02
MIN_VALID_PAIRS = 4
PER_PROC_MIN = 0.95  # G6
BASE_TOL = 0.05
N_EXPERTS = 30
DELAY_PROBE_S = 0.9
NODE_COMPUTE_S = 0.5
RELATIVE_DELAY_FACTOR = 5.0
SHIFT_PER_PROC_MAX = 0.9
SHIFT_EXPERT_MIN = 1.58
SHIFT_DELAY_MAX_QUESTIONS = 10
HTTPCORE_VERSION = "1.0.9"
# Iteration 13 の ln の同じ条件の 3 回の処理量の範囲（問/秒）
ITER13_LN_RANGE = {("fs", 1): (30.091, 30.714), ("fs", 8): (171.171, 171.672), ("dp12", 1): (59.378, 62.591), ("dp12", 8): (198.503, 307.596)}
# 事前の測定の 1 要求あたりの質問者の CPU の差（pr − pe．ms）と，実機の大きさへ縮めた見込み
CPU_DIFF_PRESTUDY = {"fs": (0.019, 0.015), "dp12": (0.081, 0.049)}
RETRY_LINE = re.compile(r"retrying \S+ \(\d+/\d+\): (\w+)")


def per_request_cpu_ms(s: dict[str, Any]) -> float | None:
    """1 要求あたりの質問者の CPU（ms）= requester_cpu_s ÷ HTTP 要求の数．"""
    cpu = s["meta"].get("requester_cpu_s")
    reqs = s["http_requests_per_s"]
    return None if cpu is None or reqs is None else cpu / (reqs * s["elapsed_s"]) * 1000.0


def delay_counts(run: str) -> tuple[int, int, int]:
    """（probe_s ≥ 0.9 秒の問の数，そのうち probe_node_s_max < 0.5 秒の数，probe_s がその実行の中央値の 5 倍以上の問の数）．失敗した問は除く．"""
    rows = [r for r in read_results(RESULTS / run / "results.jsonl") if r["error"] is None]
    probes = [r["timings"].get("probe_s", 0.0) for r in rows]
    delayed = [r for r in rows if r["timings"].get("probe_s", 0.0) >= DELAY_PROBE_S]
    node_free = [r for r in delayed if r.get("probe_node_s_max") is not None and r["probe_node_s_max"] < NODE_COMPUTE_S]
    med = float(median(probes)) if probes else 0.0
    relative = sum(p >= RELATIVE_DELAY_FACTOR * med for p in probes) if med > 0 else 0
    return len(delayed), len(node_free), relative


def retry_types(run: str) -> dict[str, int]:
    """requester.log の再試行の例外の型ごとの数．"""
    counts: dict[str, int] = {}
    path = RESULTS / run / "requester.log"
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            m = RETRY_LINE.search(line)
            if m:
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return counts


def load_top_docs(run: str) -> dict[str, list[str]]:
    """失敗の無い問の top_doc_ids（G2 の比較用）．"""
    return {r["qid"]: list(r.get("top_doc_ids") or []) for r in read_results(RESULTS / run / "results.jsonl") if r["error"] is None}


def spread(vals: list[float]) -> tuple[float, float, float] | None:
    """（最小，中央値，最大）．空なら None．"""
    return (min(vals), float(median(vals)), max(vals)) if vals else None


def grew_rule(ratios: list[float], n_valid: int) -> bool:
    """「伸びた」：有効な組が 4 組以上，有効な組の全てで q > 1.000，q の中央値が 1.02 以上．"""
    return n_valid >= MIN_VALID_PAIRS and all(q > 1.0 for q in ratios) and float(median(ratios)) >= Q_MEDIAN_MIN


def decide(g2_dp12: bool, g3_bad: list[str], dp12_valid: int, hr1: bool, hr2: bool, dp12_all_gt1: bool, dp12_cpu_all_smaller: bool) -> str:
    """このイテレーションの結論（事前登録の規則）．"""
    if not g2_dp12 or g3_bad or dp12_valid < MIN_VALID_PAIRS:
        return "棄却（測定不成立）"
    if hr1:
        return "採用"
    if dp12_all_gt1 or dp12_cpu_all_smaller or hr2:
        return "部分的に支持"
    return "棄却（伸びない）"


def self_test() -> None:
    """判定の規則を合成の値で確かめる（実機の結果は読まない）．"""
    assert grew_rule([1.03, 1.04, 1.05, 1.02], 4)
    assert not grew_rule([1.03, 1.04, 1.05, 0.999, 1.04], 5)  # 1 組でも 1 以下
    assert not grew_rule([1.01, 1.01, 1.02, 1.01, 1.015], 5)  # 中央値が 1.02 未満
    assert not grew_rule([1.05, 1.05, 1.05], 3)  # 有効な組が 4 未満
    assert decide(True, [], 5, True, False, True, True) == "採用"
    assert decide(True, [], 5, False, False, True, False) == "部分的に支持"
    assert decide(True, [], 5, False, False, False, False) == "棄却（伸びない）"
    assert decide(False, [], 5, True, False, True, True) == "棄却（測定不成立）"
    assert decide(True, ["x"], 5, True, False, True, True) == "棄却（測定不成立）"
    assert decide(True, [], 3, True, False, True, True) == "棄却（測定不成立）"
    print("self-test ok")


def main() -> None:
    """全実行の表，G1〜G6，環境の揺れ，H-R1〜H-R3・結論の機械的な出力，記録の指標を出す．"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="20261016_iter14")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return
    prefix: str = args.prefix
    cells = [(m, n) for m in METHODS for n in REPEATS]
    names = [f"{prefix}_anchor_fs_n1_pr_pre"]
    for m, n in cells:
        names += [f"{prefix}_{m}_n{n}_{b}_r{r}" for r in REPEATS[n] for b in BODIES]
    names.append(f"{prefix}_anchor_fs_n1_pr_post")
    runs: dict[str, dict[str, Any] | None] = {n: load(n) for n in names}

    print("run | 失敗率 | e2e p50 | e2e p95 | probe p50 | probe p95 | retrieve p50 | 処理量 | req_util/proc 中央値 | 要求あたり質問者CPU(ms) | expert_max | ctl_busy | 再試行 | 新接続 | 要求/問 | HTTP要求/秒 | hit@2 | 誤り")
    for n in names:
        s = runs[n]
        if s is None:
            print(f"{n} | (実行なし)")
            continue
        print(
            f"{n.removeprefix(prefix + '_')} | {s['failure_rate']:.4f} | {fmt(s['e2e_p50'], 4)} | {fmt(s['e2e_p95'], 4)} | {fmt(s['probe_p50'], 4)} | {fmt(s['probe_p95'], 4)} | "
            f"{fmt(s['retrieve_p50'], 4)} | {s['throughput']:.3f} | {fmt(s['req_util_proc_median'])} | {fmt(per_request_cpu_ms(s), 3)} | {fmt(s['expert_max'])} | "
            f"{fmt(s['control_busy'])} | {s['retries']} | {s['new_connections']} | {fmt(s['requests_per_question'], 1)} | {fmt(s['http_requests_per_s'], 1)} | {fmt(s['hit2'], 4)} | {s['errors']}"
        )

    print("\n## 正しさの確認（G1・G3・G5．満たさない実行は判定に使わず「欠け」）")
    g1_ok: dict[str, bool] = {}
    g3_ok: dict[str, bool] = {}
    g3_bad: list[str] = []
    for n, s in runs.items():
        short = n.removeprefix(prefix + "_")
        if s is None:
            g1_ok[n] = g3_ok[n] = False
            g3_bad.append(short)
            print(f"欠け: {short}")
            continue
        parts = short.split("_")
        meta, cfg = s["meta"], s["meta"]["config"]
        if parts[0] == "anchor":
            m, nproc, rb, pb, sn, pa, nq = "fs", 1, "per_request", "per_request", "search", "stock", 300
        else:
            m, nproc, rb, pb, sn, pa, nq = parts[0], int(parts[1][1:]), BODIES[parts[2]], "pre_encoded", "blocked", "lean", 2940
        routing, d = METHODS[m]
        ppp = meta.get("requester_parallel_per_proc")
        g1 = s["failure_rate"] <= FAIL_LIMIT
        g3 = (
            meta.get("retrieve_body") == rb and meta.get("pool_assign") == pa and meta.get("httpcore_version") == HTTPCORE_VERSION
            and meta.get("sniffio_lookup") == sn and meta.get("sniffio_installed") is False
            and meta.get("probe_body") == pb and meta.get("requester_procs") == nproc
            and isinstance(ppp, list) and len(ppp) == nproc and sum(ppp) == 30
            and meta.get("http_pool") == "per_node" and meta["routing"] == routing
            and meta["n_questions"] == nq
            and (d is None or cfg["routing"]["probe_d"]["enronqa"] == d)
        )
        g5 = s["expert_n"] == N_EXPERTS and s["control_busy"] is not None
        g1_ok[n], g3_ok[n] = g1, g3
        if not g3:
            g3_bad.append(short)
        print(f"{short}: G1 {g1}（失敗率 {s['failure_rate']:.4f}），G3 {g3}（retrieve_body {meta.get('retrieve_body')}，pool_assign {meta.get('pool_assign')}，httpcore {meta.get('httpcore_version')}，sniffio_installed {meta.get('sniffio_installed')}），G5 {g5}")

    print("\n## G2：同じ方式の 2,940 問の全実行（pr・pe・N = 1・8）で selected_sources と top_doc_ids が問ごとに一致するか（失敗の無い問のみ）")
    g2: dict[str, bool] = {}
    for m in METHODS:
        group = [(n, s) for n, s in runs.items() if s is not None and f"_{m}_n" in n and "_anchor_" not in n]
        if not group:
            g2[m] = False
            print(f"{m}: 実行なし")
            continue
        ref_name, ref = group[0]
        ref_docs = load_top_docs(ref_name)
        sel_rates, doc_rates = [], []
        for n, s in group[1:]:
            docs = load_top_docs(n)
            common = set(s["selection"]) & set(ref["selection"])
            sel_rates.append(float(np.mean([s["selection"][q] == ref["selection"][q] for q in common])) if common else float("nan"))
            common_d = set(docs) & set(ref_docs)
            doc_rates.append(float(np.mean([docs[q] == ref_docs[q] for q in common_d])) if common_d else float("nan"))
        g2[m] = bool(sel_rates) and all(r == 1.0 for r in sel_rates) and all(r == 1.0 for r in doc_rates)
        print(f"{m}: 実行 {len(group)} 件，基準 {ref_name.removeprefix(prefix + '_')}，selected_sources の最小の一致率 {min(sel_rates, default=float('nan')):.4f}，top_doc_ids の最小の一致率 {min(doc_rates, default=float('nan')):.4f} → {g2[m]}")

    print("\n## G4（記録）：2,940 問の hit@2")
    for m, ref_hit in TEST_HIT2.items():
        hits = sorted({round(s["hit2"], 4) for n, s in runs.items() if s is not None and f"_{m}_n" in n and "_anchor_" not in n})
        print(f"{m}: 実測の値 {hits}，Iteration 8・11〜13 の値 {ref_hit} → 5e-4 以内 {all(abs(h - ref_hit) <= HIT_TOL for h in hits)}")

    print("\n## 環境の揺れ（前後の flood_score × N = 1 × per_request × search × stock × 300 問）")
    for when in ("pre", "post"):
        a = runs[f"{prefix}_anchor_fs_n1_pr_{when}"]
        if a is None:
            continue
        in_t = ENV_THR[0] <= a["throughput"] <= ENV_THR[1]
        in_p = ENV_P95[0] <= a["e2e_p95"] <= ENV_P95[1]
        print(f"{when}: 処理量 {a['throughput']:.3f} {ENV_THR} {'内' if in_t else '外'}，e2e p95 {a['e2e_p95']:.3f} {ENV_P95} {'内' if in_p else '外'}")

    print("\n## 組の比 q = pe の処理量 ÷ pr の処理量（有効な組：pr・pe の両方が G1・G3 を満たす．N = 1 は G6 として両方の requester_cpu_util_per_proc の中央値が 0.95 以上）")
    qs: dict[tuple[str, int], list[float]] = {}
    valid_n: dict[tuple[str, int], int] = {}
    cpu_smaller: dict[tuple[str, int], list[bool]] = {}
    for m, n in cells:
        qs[(m, n)], cpu_smaller[(m, n)] = [], []
        invalid: list[str] = []
        for r in REPEATS[n]:
            pr, pe = f"{prefix}_{m}_n{n}_pr_r{r}", f"{prefix}_{m}_n{n}_pe_r{r}"
            sp, se = runs[pr], runs[pe]
            if sp is None or se is None:
                invalid.append(f"r{r}（欠け）")
                print(f"{m} N={n} r{r}: 欠け")
                continue
            q = se["throughput"] / sp["throughput"]
            ok = g1_ok[pr] and g3_ok[pr] and g1_ok[pe] and g3_ok[pe]
            g6 = True
            if n == 1:
                g6 = all(x["req_util_proc_median"] is not None and x["req_util_proc_median"] >= PER_PROC_MIN for x in (sp, se))
            cp, ce = per_request_cpu_ms(sp), per_request_cpu_ms(se)
            cpu_ratio = None if cp is None or ce is None else cp / ce
            print(f"{m} N={n} r{r}（{'pr → pe' if r % 2 else 'pe → pr'}）: q {q:.3f}，pr {sp['throughput']:.3f}・pe {se['throughput']:.3f} 問/秒，"
                  f"CPU の比 pr/pe {fmt(cpu_ratio)}，pr の 1 要求 {fmt(cp)}・pe の 1 要求 {fmt(ce)} ms，per_proc pr {fmt(sp['req_util_proc_median'])}・pe {fmt(se['req_util_proc_median'])}，"
                  f"有効 {ok and g6}（G1・G3 {ok}，G6 {g6}）")
            if ok and g6:
                qs[(m, n)].append(q)
                if cp is not None and ce is not None:
                    cpu_smaller[(m, n)].append(ce < cp)
            else:
                invalid.append(f"r{r}")
        valid_n[(m, n)] = len(qs[(m, n)])
        print(f"{m} N={n}: 有効な組 {valid_n[(m, n)]}／{len(REPEATS[n])}（無効 {invalid}）")

    print("\n## 判定の規則")
    grew: dict[tuple[str, int], bool] = {}
    for m, n in cells:
        v = qs[(m, n)]
        grew[(m, n)] = bool(v) and grew_rule(v, valid_n[(m, n)])
        if n == 1:
            print(f"{m} N=1: 有効な組 {valid_n[(m, n)]}，q {[round(x, 3) for x in v]}，中央値 {fmt(float(median(v)) if v else None)}，全て > 1.000 {all(x > 1.0 for x in v) if v else None}，q の中央値 ≥ {Q_MEDIAN_MIN} → 伸びた {grew[(m, n)]}")
        else:
            print(f"{m} N=8（記録．G6 を当てない）: q {[round(x, 3) for x in v]}，全て > 1 {all(x > 1.0 for x in v) if v else None}，中央値 {fmt(float(median(v)) if v else None)}")
    print(f"H-R1（dp12 × N = 1）: {grew[('dp12', 1)]}．H-R2（fs × N = 1）: {grew[('fs', 1)]}．H-R3（記録）: fs × N = 8 / dp12 × N = 8 の q は上のとおり")
    dp12_cpu = cpu_smaller[("dp12", 1)]
    verdict = decide(
        g2["dp12"], g3_bad, valid_n[("dp12", 1)], grew[("dp12", 1)], grew[("fs", 1)],
        bool(qs[("dp12", 1)]) and all(x > 1.0 for x in qs[("dp12", 1)]),
        bool(dp12_cpu) and all(dp12_cpu),
    )
    print(f"\nこのイテレーションの結論（規則の機械的な適用．G2 dp12 {g2['dp12']}，G3 の不成立または欠け {len(g3_bad)} 件 {g3_bad}，dp12 × N = 1 の有効な組 {valid_n[('dp12', 1)]}）: {verdict}")

    print("\n## 記録（判定に使わない）")
    for m, n in cells:
        lst = {b: [s for r in REPEATS[n] if (s := runs[f"{prefix}_{m}_n{n}_{b}_r{r}"]) is not None] for b in BODIES}
        for b, ss in lst.items():
            thr = spread([s["throughput"] for s in ss])
            cpu = [c for s in ss if (c := per_request_cpu_ms(s)) is not None]
            print(f"{m} N={n} {b}: 処理量（最小，中央値，最大）{thr}，1 要求あたりの質問者の CPU(ms) 中央値 {fmt(float(median(cpu)) if cpu else None)}，"
                  f"req_util/proc {[round(s['req_util_proc_median'], 3) for s in ss if s['req_util_proc_median'] is not None]}，"
                  f"expert_cpu_util_max {[round(s['expert_max'], 3) for s in ss if s['expert_max'] is not None]}")
        tp, te = spread([s["throughput"] for s in lst["pr"]]), spread([s["throughput"] for s in lst["pe"]])
        if tp and te:
            print(f"{m} N={n}: Iteration 13 と同じ範囲の規則：pe の範囲は pr より {rel(te, tp)}，中央値の比 {te[1] / tp[1]:.3f}（{RATIO_MIN} 以上で範囲が重ならなければ「差がある」．効果の大きさから成り立たない見込み）")
            lo, hi = ITER13_LN_RANGE[(m, n)]
            print(f"{m} N={n}: pr の中央値 {tp[1]:.3f} は Iteration 13 の ln の範囲 {lo}〜{hi} の ±5% 内 {lo * (1 - BASE_TOL) <= tp[1] <= hi * (1 + BASE_TOL)}（外なら「環境が変わった」）")
        cp = [c for s in lst["pr"] if (c := per_request_cpu_ms(s)) is not None]
        ce = [c for s in lst["pe"] if (c := per_request_cpu_ms(s)) is not None]
        if cp and ce:
            pre, shrunk = CPU_DIFF_PRESTUDY[m]
            print(f"{m} N={n}: 1 要求あたりの質問者の CPU の差 pr − pe = {median(cp) - median(ce):.3f} ms（事前の測定 {pre} ms，実機の大きさへ縮めた見込み 約 {shrunk} ms）")
    for m in METHODS:
        for b in BODIES:
            t1 = spread([s["throughput"] for r in REPEATS[1] if (s := runs[f"{prefix}_{m}_n1_{b}_r{r}"]) is not None])
            t8 = spread([s["throughput"] for r in REPEATS[8] if (s := runs[f"{prefix}_{m}_n8_{b}_r{r}"]) is not None])
            if t1 and t8:
                print(f"{m} {b}: E(8) {t8[1] / (8 * t1[1]):.3f}")

    print("\n## 記録の指標：遅れの問の数（絶対の閾値 probe_s ≥ 0.9 秒は N = 8 の実行だけ．括弧内はそのうち probe_node_s_max < 0.5 秒），"
          "相対の閾値（probe_s ≥ その実行の中央値の 5 倍．全実行），質問者の TCP の再送（run_meta の requester_tcp），再試行の例外の型")
    delays: dict[str, int] = {}
    for n in names:
        if runs[n] is None:
            continue
        d_all, d_free, d_rel = delay_counts(n)
        is_n8 = "_n8_" in n
        delays[n] = d_all if is_n8 else 0
        abs_text = f"遅れの問 {d_all}（ノードの計算によらない {d_free}）" if is_n8 else "遅れの問（絶対）数えない"
        print(f"{n.removeprefix(prefix + '_')}: {abs_text}，相対の遅れの問 {d_rel}，TCP {runs[n]['meta'].get('requester_tcp')}，再試行の型 {retry_types(n)}")

    print("\n## 律速の移りの読み（dp12 × N = 8 × pe．per_proc の中央値 < 0.9 かつ最も忙しいノードの expert_cpu_util ≥ 1.58．遅れの問が 10 問以上の実行は使わない）")
    for r in REPEATS[8]:
        name = f"{prefix}_dp12_n8_pe_r{r}"
        s = runs[name]
        if s is None or s["req_util_proc_median"] is None or s["expert_max"] is None:
            print(f"r{r}: 欠け")
            continue
        if delays.get(name, 0) >= SHIFT_DELAY_MAX_QUESTIONS:
            print(f"r{r}: 遅れの問 {delays[name]} 問のため読みに使わない（per_proc {s['req_util_proc_median']:.3f}，expert_max {s['expert_max']:.3f}）")
            continue
        moved = s["req_util_proc_median"] < SHIFT_PER_PROC_MAX and s["expert_max"] >= SHIFT_EXPERT_MIN
        print(f"r{r}: per_proc {s['req_util_proc_median']:.3f}，expert_max {s['expert_max']:.3f} → 律速が専門家ノードへ移った {moved}")


if __name__ == "__main__":
    main()
