"""Iteration 7：device_probe と centroid_probe の負荷試験の集計（事前登録の指標・正しさの確認・判定規則の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261015_iter7_load_summary/iter7_summary.py > results/20261015_iter7_load_summary/iter7_summary.out

指標の定義は iter6_summary.py の summarize・spread・rel を再利用する（処理量 = 成功した問の数 ÷ run_meta.json の finished_at − started_at．範囲が重ならないときだけ「差がある」）．
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path("results/20261015_iter6_load_summary")))
from iter6_summary import fmt, rel, spread, summarize, thr_p95  # noqa: E402

from atrium.analysis import read_results  # noqa: E402

RESULTS = Path("results")
PREFIX = "20261015_iter7"
ITER4 = "20261014_iter4"
ITER6 = "20261015_iter6"
REPEATS = (1, 2, 3)
ADOPT_RATIO = 1.10
ENV_THR = (11.127, 14.247)
ENV_P95 = (3.053, 4.067)
SERIES = ((75, 24), (30, 12))  # (K, D)．K = 75 を先
EVAL = json.loads((RESULTS / "20261015_iter7_device_probe_eval/metrics.json").read_text(encoding="utf-8"))
LIVE = EVAL["live_selected_sources"]


def load(run: str) -> dict[str, Any] | None:
    """実行の指標に，probe を受けたデバイスの数・probe したシャードの数を足したもの．"""
    s = summarize(RESULTS / run)
    if s is None:
        return None
    rows = [r for r in read_results(RESULTS / run / "results.jsonl") if r["error"] is None]
    s["recipients"] = [r.get("probe_recipients") for r in rows]
    s["probe_shards"] = [r.get("probe_shards") for r in rows]
    meta = json.loads((RESULTS / run / "run_meta.json").read_text(encoding="utf-8"))
    s["meta_routing"] = meta["routing"]
    s["meta_k"] = meta["config"]["routing"]["probe_k"]["enronqa"]
    s["meta_d"] = meta["config"]["routing"]["probe_d"]["enronqa"]
    return s


def main() -> None:
    """全実行の表，正しさの確認，環境の揺れ，K ごとの H-D1〜H-D3 と結論，記録の指標を出す．"""
    runs: dict[str, dict[str, Any] | None] = {}
    names: list[str] = [f"{PREFIX}_anchor_fs_pn_p30_pre", f"{PREFIX}_anchor_fs_pn_p30_post"]
    for k, d in SERIES:
        for r in REPEATS:
            names += [f"{PREFIX}_cp{k}_pn_p30_r{r}", f"{PREFIX}_dp{d}_pn_p30_r{r}"]
        names += [f"{PREFIX}_dp{d}_pn_p{p}_r{r}" for p in (1, 16) for r in REPEATS]
    print("run | 失敗率 | e2e p50 | e2e p95 | probe p50 | probe p95 | retrieve p50 | retrieve p95 | route p50 | 処理量 | cpu_util | 再試行 | 新接続(下限) | 要求/問 | HTTP要求/秒 | node_s_max p95 | hit@2 | recipients(平均) | probe_shards(平均) | 誤り")
    for n in names:
        s = load(n)
        runs[n] = s
        if s is None:
            print(f"{n} | (実行なし)")
            continue
        rec = [x for x in s["recipients"] if x is not None]
        shd = [x for x in s["probe_shards"] if x is not None]
        print(
            f"{n.removeprefix(PREFIX + '_')} | {s['failure_rate']:.4f} | {fmt(s['e2e_p50'], 4)} | {fmt(s['e2e_p95'], 4)} | {fmt(s['probe_p50'], 4)} | {fmt(s['probe_p95'], 4)} | "
            f"{fmt(s['retrieve_p50'], 4)} | {fmt(s['retrieve_p95'], 4)} | {fmt(s['route_p50'], 5)} | {s['throughput']:.3f} | {fmt(s['cpu_util'])} | {s['retries']} | "
            f"{s['new_connections']} | {fmt(s['requests_per_question'], 1)} | {fmt(s['http_requests_per_s'], 1)} | {fmt(s['node_s_max_p95'], 4)} | {fmt(s['hit2'], 4)} | "
            f"{fmt(float(np.mean(rec)) if rec else None, 2)} | {fmt(float(np.mean(shd)) if shd else None, 1)} | {s['errors']}"
        )

    print("\n## 正しさの確認（実機）")
    bad = 0
    for n, s in runs.items():
        if s is None:
            bad += 1
            print(f"欠け: {n}")
            continue
        short = n.removeprefix(PREFIX + "_")
        if s["http_pool"] != "per_node":
            bad += 1
            print(f"不一致: {short} http_pool={s['http_pool']}")
        if short.startswith("dp"):
            d = int(short.split("_")[0][2:])
            ok_cfg = s["meta_routing"] == "device_probe" and s["meta_d"] == d
            live = {q: set(v) for q, v in LIVE[str(d)].items()}
            common = set(s["selection"]) & set(live)
            rate = float(np.mean([s["selection"][q] == live[q] for q in common])) if common else float("nan")
            rec_ok = all(x == d for x in s["recipients"])
            shd_ok = all(x == 5 * d for x in s["probe_shards"])
            print(f"{short}: routing/D 一致 {ok_cfg}，live 選択一致 {rate:.4f}（比較 {len(common)} 問），recipients=D {rec_ok}，probe_shards=5D {shd_ok}")
            if not (ok_cfg and rate == 1.0 and rec_ok and shd_ok):
                bad += 1
        elif short.startswith("cp"):
            k = int(short.split("_")[0][2:])
            ref = summarize(RESULTS / f"{ITER4}_cp{k}_p1_r1")
            common = set(s["selection"]) & set(ref["selection"]) if ref else set()
            rate = float(np.mean([s["selection"][q] == ref["selection"][q] for q in common])) if common else float("nan")
            ok_cfg = s["meta_routing"] == "centroid_probe" and s["meta_k"] == k
            print(f"{short}: routing/K 一致 {ok_cfg}，iter4 選択一致 {rate:.4f}（比較 {len(common)} 問）")
            if not (ok_cfg and rate == 1.0):
                bad += 1
    print(f"(不一致・欠け {bad} 件．0 なら全回で通過)")

    print("\n## 環境の揺れ（前後の flood_score × per_node の p30）")
    for when in ("pre", "post"):
        a = runs[f"{PREFIX}_anchor_fs_pn_p30_{when}"]
        if a is None:
            continue
        in_t = ENV_THR[0] <= a["throughput"] <= ENV_THR[1]
        in_p = ENV_P95[0] <= a["e2e_p95"] <= ENV_P95[1]
        print(f"{when}: 処理量 {a['throughput']:.3f} {ENV_THR} {'内' if in_t else '外'}，e2e p95 {a['e2e_p95']:.3f} {ENV_P95} {'内' if in_p else '外'}，cpu_util {fmt(a['cpu_util'])}")

    print("\n## 判定の規則（parallel 30．同じ時間帯の centroid_probe(K) と device_probe(D_K)）")
    verdicts: dict[int, str] = {}
    for k, d in SERIES:
        ch = next(c for c in EVAL["choices"] if c["k"] == k)
        cp = thr_p95([runs[f"{PREFIX}_cp{k}_pn_p30_r{r}"] for r in REPEATS])
        dp = thr_p95([runs[f"{PREFIX}_dp{d}_pn_p30_r{r}"] for r in REPEATS])
        h1 = bool(ch["test_not_inferior"])
        h2 = rel(dp[0], cp[0]) == "上"
        h3 = rel(dp[1], cp[1]) == "下"
        ratio = dp[0][1] / cp[0][1] if dp[0] and cp[0] else float("nan")
        if h1 and h2 and h3 and ratio >= ADOPT_RATIO:
            v = "採用"
        elif h1 and (h2 or h3):
            v = "部分的に支持"
        else:
            v = "棄却"
        verdicts[k] = v
        print(f"K = {k}, D = {d}: cp 処理量 {cp[0]}，e2e p95 {cp[1]}；dp 処理量 {dp[0]}，e2e p95 {dp[1]}")
        print(f"  H-D1 {h1}（test hit@2 dp {ch['test_device_probe_hit']:.4f} vs cp {ch['test_centroid_probe_hit']:.4f}），H-D2 {h2}（dp は cp より {rel(dp[0], cp[0])}），H-D3 {h3}（e2e p95 は dp が cp より {rel(dp[1], cp[1])}），中央値の比 dp/cp {ratio:.3f}（採用は {ADOPT_RATIO} 以上）→ {v}")
    main_k = 75
    print(f"このイテレーションの結論（規則の機械的な適用．主は K = {main_k}．正しさの確認の不一致 {bad} 件が 0 のときだけ有効）: {verdicts[main_k]}（K = 30: {verdicts[30]}）")

    print("\n## 記録：R と予測（1 ÷ (route_s + (D + 2) ÷ R)，R は同じ時間帯の cp の R）")
    for k, d in SERIES:
        r_vals: list[float] = []
        for r in REPEATS:
            s = runs[f"{PREFIX}_cp{k}_pn_p30_r{r}"]
            if s is None:
                continue
            per_q = s["requests_per_question"]
            r_vals.append(per_q / (1 / s["throughput"] - (s["route_p50"] or 0.0)))
        r_med = float(np.median(r_vals))
        for r in REPEATS:
            s = runs[f"{PREFIX}_dp{d}_pn_p30_r{r}"]
            if s is None:
                continue
            pred = 1 / ((s["route_p50"] or 0.0) + (d + 2) / r_med)
            print(f"K = {k}, D = {d}, r{r}: 実測 {s['throughput']:.3f}，予測 {pred:.3f}（R 中央値 {r_med:.1f}），実測/予測 {s['throughput'] / pred:.3f}，要求/問 {fmt(s['requests_per_question'], 2)}，cpu_util {fmt(s['cpu_util'])}")
        print(f"  cp{k} の R: {[round(x, 1) for x in r_vals]}")

    print("\n## 曲線（device_probe の 3 回の最小・中央値・最大）")
    for _, d in SERIES:
        for p in (1, 16, 30):
            lst = [runs[f"{PREFIX}_dp{d}_pn_p{p}_r{r}"] for r in REPEATS]
            print(f"  dp{d} p{p}: 処理量 {spread([s['throughput'] if s else None for s in lst])}，e2e p50 {spread([s['e2e_p50'] if s else None for s in lst])}，e2e p95 {spread([s['e2e_p95'] if s else None for s in lst])}，hit@2 {spread([s['hit2'] if s else None for s in lst])}")
    print("\n## 参考（Iteration 6 の同じ条件）")
    for tag in ("cp75_pn", "cp30_pn"):
        t, e = thr_p95([summarize(RESULTS / f"{ITER6}_{tag}_p30_r{r}") for r in REPEATS])
        print(f"  iter6 {tag} p30: 処理量 {t}，e2e p95 {e}")


if __name__ == "__main__":
    main()
