"""Iteration 4：centroid_probe の実機の負荷試験の集計（事前登録の指標・正しさの確認・判定規則の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261014_iter4_load_summary/iter4_summary.py > results/20261014_iter4_load_summary/iter4_summary.out

定義は t4_summary.py と同じ（処理量 = 成功した問の数 ÷ run_meta.json の finished_at − started_at．3 回の幅が重ならないときだけ「差がある」）．
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from atrium.analysis import read_results

RESULTS = Path("results")
PREFIX = "20261014_iter4"
PARALLELS = (1, 4, 8, 16, 30)
REPEATS = (1, 2, 3)
KS = (75, 30)
ENV_LOW, ENV_HIGH = 0.9, 1.1
ADOPT_RATIO = 1.5
SAME_SHARDS_BAND = 0.10
CORRECT_SELECTION_RATE = 0.99
CORRECT_RECIPIENTS_TOL = 0.01
REACH = json.loads((RESULTS / f"{PREFIX}_probe_reach" / "metrics.json").read_text(encoding="utf-8"))


def pct(values: list[float], q: float) -> float | None:
    """百分位数（値が無ければ None）．"""
    return float(np.percentile(values, q)) if values else None


def summarize(path: Path, k: int | None) -> dict[str, Any] | None:
    """1 回の実行の指標．実行が無ければ None．k は centroid_probe の K（それ以外は None）．"""
    if not (path / "results.jsonl").exists():
        return None
    rows = read_results(path / "results.jsonl")
    meta = json.loads((path / "run_meta.json").read_text(encoding="utf-8"))
    ok = [r for r in rows if r["error"] is None]
    elapsed = meta["finished_at"] - meta["started_at"]
    t = [r["timings"] for r in ok]
    out: dict[str, Any] = {
        "n": len(rows),
        "failed": len(rows) - len(ok),
        "failure_rate": (len(rows) - len(ok)) / len(rows),
        "errors": dict(Counter((r["error"] or "").split(":")[0] for r in rows if r["error"])),
        "e2e_p50": pct([x["e2e_s"] for x in t], 50),
        "e2e_p95": pct([x["e2e_s"] for x in t], 95),
        "probe_p50": pct([x["probe_s"] for x in t if "probe_s" in x], 50),
        "probe_p95": pct([x["probe_s"] for x in t if "probe_s" in x], 95),
        "route_p50": pct([x["route_s"] for x in t if "route_s" in x], 50),
        "throughput": len(ok) / elapsed,
        "elapsed_s": elapsed,
        "query_recipients": float(np.mean([r.get("query_recipients", 0) for r in ok])) if ok else None,
        "hit2": float(np.mean([r["bank"] in (r.get("selected_sources") or []) for r in ok])) if ok else None,
    }
    if any("probe_recipients" in r for r in ok):
        recipients = [r["probe_recipients"] for r in ok if "probe_recipients" in r]
        node_max = [r["probe_node_s_max"] for r in ok if r.get("probe_node_s_max") is not None]
        out["probe_recipients_mean"] = float(np.mean(recipients))
        out["probe_recipients_hist"] = dict(sorted(Counter(recipients).items()))
        out["probe_shards_mean"] = float(np.mean([r["probe_shards"] for r in ok if "probe_shards" in r]))
        out["shards_per_device_mean"] = float(
            np.mean([np.mean(r["probe_shards_per_device"]) for r in ok if r.get("probe_shards_per_device")])
        )
        out["node_s_max_p50"] = pct(node_max, 50)
        out["node_s_max_p95"] = pct(node_max, 95)
    if k is not None:
        offline = REACH["selected_sources"][str(k)]
        match = [set(r["selected_sources"]) == set(offline[r["qid"]]) for r in ok]
        out["selection_match_rate"] = float(np.mean(match)) if match else None
        reach = next(x for x in REACH["live_300"]["by_k"] if x["k"] == k)
        out["offline_devices_mean"] = reach["devices_reached"]["mean"]
        out["offline_hit2"] = reach["hit@2"]
    return out


def spread(vals: list[float | None]) -> tuple[float, float, float] | None:
    """3 回の（最小，中央値，最大）．値が欠けていれば None．"""
    v = [x for x in vals if x is not None]
    return (min(v), float(median(v)), max(v)) if len(v) == len(REPEATS) else None


def main() -> None:
    """全条件の表，正しさの確認，環境の揺れの規則，H-P1〜H-P3 の判定規則の出力を行う．"""
    runs: dict[str, list[dict[str, Any] | None]] = {}
    print("run | 失敗率 | e2e p50 | e2e p95 | probe p50 | probe p95 | route p50 | 処理量 | 受信デバイス/問 | probe shards | shards/device | node_s_max p50 | p95 | hit@2 | 選択一致率 | 誤り")
    for name, k in (("cp75", 75), ("cp30", 30)):
        for p in PARALLELS:
            lst = [summarize(RESULTS / f"{PREFIX}_{name}_p{p}_r{r}", k) for r in REPEATS]
            runs[f"{name}_p{p}"] = lst
            for r, s in zip(REPEATS, lst, strict=True):
                if s is None:
                    print(f"{name} p{p} r{r} | (実行なし)")
                    continue
                print(
                    f"{name} p{p} r{r} | {s['failure_rate']:.4f} | {s['e2e_p50']:.4f} | {s['e2e_p95']:.4f} | {s['probe_p50']:.4f} | {s['probe_p95']:.4f} | "
                    f"{s['route_p50']:.4f} | {s['throughput']:.3f} | {s['probe_recipients_mean']:.3f} {s['probe_recipients_hist']} | {s['probe_shards_mean']:.3f} | "
                    f"{s['shards_per_device_mean']:.3f} | {s['node_s_max_p50']:.4f} | {s['node_s_max_p95']:.4f} | {s['hit2']:.4f} | {s['selection_match_rate']:.4f} | {s['errors']}"
                )
    anchors: dict[str, dict[str, Any] | None] = {}
    for routing in ("flood_score", "multi_centroid"):
        for when in ("pre", "post"):
            s = summarize(RESULTS / f"{PREFIX}_anchor_{routing}_p30_{when}", None)
            anchors[f"{routing}_{when}"] = s
            print(f"anchor {routing} p30 {when} | " + (str({k: v for k, v in s.items()}) if s else "(実行なし)"))
    t4: dict[tuple[str, int], list[dict[str, Any] | None]] = {
        (x, p): [summarize(RESULTS / f"20261014_t4_{x}_p{p}_r{r}", None) for r in REPEATS]
        for x in ("flood_score", "multi_centroid")
        for p in PARALLELS
    }
    print("\n## T4 の参考値（hit@2 は gold の受信箱が selected_sources に入る割合）")
    for (x, p), lst in t4.items():
        print(f"t4 {x} p{p}: 処理量 {[round(s['throughput'], 3) for s in lst if s]} e2e_p95 {[round(s['e2e_p95'], 3) for s in lst if s]} hit2 {[round(s['hit2'], 4) for s in lst if s]}")

    print("\n## 正しさの確認")
    for key, lst in runs.items():
        for r, s in zip(REPEATS, lst, strict=True):
            if s is None:
                continue
            ok = (
                s["selection_match_rate"] >= CORRECT_SELECTION_RATE
                and abs(s["probe_shards_mean"] - (75 if "cp75" in key else 30)) < 1e-9
                and abs(s["probe_recipients_mean"] - s["offline_devices_mean"]) <= CORRECT_RECIPIENTS_TOL
            )
            if not ok:
                print(f"不一致: {key} r{r} 選択一致 {s['selection_match_rate']:.4f} shards {s['probe_shards_mean']:.3f} recipients {s['probe_recipients_mean']:.3f} (offline {s['offline_devices_mean']:.3f})")
    print("(不一致の行が無ければ全回で通過)")

    print("\n## 環境の揺れの規則（前後 4 回が T4 の [最小 × 0.9, 最大 × 1.1] に入るか）")
    env_ok = True
    for routing in ("flood_score", "multi_centroid"):
        thr = spread([s["throughput"] if s else None for s in t4[(routing, 30)]])
        p95 = spread([s["e2e_p95"] if s else None for s in t4[(routing, 30)]])
        for when in ("pre", "post"):
            a = anchors[f"{routing}_{when}"]
            if a is None or thr is None or p95 is None:
                env_ok = False
                continue
            in_thr = thr[0] * ENV_LOW <= a["throughput"] <= thr[2] * ENV_HIGH
            in_p95 = p95[0] * ENV_LOW <= a["e2e_p95"] <= p95[2] * ENV_HIGH
            env_ok &= in_thr and in_p95
            print(f"{routing} {when}: 処理量 {a['throughput']:.3f} (T4 {thr[0]:.3f}〜{thr[2]:.3f}) {'内' if in_thr else '外'}，e2e p95 {a['e2e_p95']:.3f} (T4 {p95[0]:.3f}〜{p95[2]:.3f}) {'内' if in_p95 else '外'}")
    print(f"環境の揺れの規則: {'全て内（比べる相手は T4 の 3 回）' if env_ok else '外れあり（比べる相手は前後の確認の値）'}")

    print("\n## 判定の規則（K = 75，parallel = 30）")
    if env_ok:
        f_thr = spread([s["throughput"] if s else None for s in t4[("flood_score", 30)]])
        f_p95 = spread([s["e2e_p95"] if s else None for s in t4[("flood_score", 30)]])
        base = "T4 の 3 回"
    else:
        pair = [anchors["flood_score_pre"], anchors["flood_score_post"]]
        f_thr = (min(a["throughput"] for a in pair), float(median([a["throughput"] for a in pair])), max(a["throughput"] for a in pair))
        f_p95 = (min(a["e2e_p95"] for a in pair), float(median([a["e2e_p95"] for a in pair])), max(a["e2e_p95"] for a in pair))
        base = "前後の確認 2 回"
    c_thr = spread([s["throughput"] if s else None for s in runs["cp75_p30"]])
    c_p95 = spread([s["e2e_p95"] if s else None for s in runs["cp75_p30"]])
    c30_thr = spread([s["throughput"] if s else None for s in runs["cp30_p30"]])
    print(f"比べる相手: flood_score {base}．処理量 {f_thr}，e2e p95 {f_p95}")
    print(f"cp75 p30: 処理量 {c_thr}，e2e p95 {c_p95}．cp30 p30: 処理量 {c30_thr}")
    if f_thr and f_p95 and c_thr and c_p95:
        hp1 = c_thr[0] > f_thr[2]
        hp2 = c_p95[2] < f_p95[0]
        ratio = c_thr[1] / f_thr[1]
        print(f"H-P1: {hp1}，H-P2: {hp2}，処理量の中央値の比（cp75 / flood_score）: {ratio:.3f}")
        if hp1 and hp2 and ratio >= ADOPT_RATIO:
            verdict = "採用"
        elif hp1 or hp2:
            verdict = "部分的に支持"
        else:
            verdict = "棄却"
        print(f"結論（規則の機械的な適用）: {verdict}")
        if ratio >= ADOPT_RATIO:
            h3 = "シャードの数"
        elif abs(ratio - 1) <= SAME_SHARDS_BAND:
            h3 = "デバイスの数"
        else:
            h3 = "分けられない"
        print(f"H-P3（規則の出力）: {h3}")

    print("\n## 曲線（parallel 30 の中央値．K = 150 は flood_score，K = 2 は multi_centroid の T4 3 回）")
    for label, lst in (("K=150 flood_score", t4[("flood_score", 30)]), ("K=2 multi_centroid", t4[("multi_centroid", 30)]), ("K=75", runs["cp75_p30"]), ("K=30", runs["cp30_p30"])):
        thr = spread([s["throughput"] if s else None for s in lst])
        p95 = spread([s["e2e_p95"] if s else None for s in lst])
        print(f"{label}: 処理量 {thr}，e2e p95 {p95}")
    print("処理量の parallel 別（3 回の中央値）:")
    for key, lst in runs.items():
        thr = spread([s["throughput"] if s else None for s in lst])
        p50 = spread([s["e2e_p50"] if s else None for s in lst])
        print(f"  {key}: 処理量 {thr}，e2e p50 {p50}")


if __name__ == "__main__":
    main()
