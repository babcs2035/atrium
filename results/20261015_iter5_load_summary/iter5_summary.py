"""Iteration 5：質問者の HTTP 接続の持ち方（experiment.http_pool）を変えた負荷試験の集計（事前登録の指標・正しさの確認・判定規則の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261015_iter5_load_summary/iter5_summary.py > results/20261015_iter5_load_summary/iter5_summary.out

定義は iter4_summary.py と同じ（処理量 = 成功した問の数 ÷ run_meta.json の finished_at − started_at．3 回の幅が重ならないときだけ「差がある」）．
追加の指標：requester_cpu_util（run_meta.json），再試行の数（requester.log の retrying の行），新しい接続の数（各ノードの access log の
送信元ポートの異なり数の合計．下限），1 秒あたりの HTTP 要求の数（ノードの access log の /v1/probe・/v1/retrieve の POST の数 ÷ 経過時間）．
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

from atrium.analysis import read_results

RESULTS = Path("results")
PREFIX = "20261015_iter5"
T4 = "20261014_t4"
ITER4 = "20261014_iter4"
PARALLELS = (1, 4, 8, 16, 30)
REPEATS = (1, 2, 3)
ENV_LOW, ENV_HIGH = 0.9, 1.1
ADOPT_RATIO = 1.5
H3_SAME_BAND = 0.10
H3_SU_VS_PN = 0.9
CPU_SATURATED = 0.9
CORRECT_SELECTION_RATE = 0.99
PER_NODE_CONNECTION_LIMIT = 300
ACCESS_LINE = re.compile(r"INFO:\s+(\S+):(\d+) - \"POST (/v1/probe|/v1/retrieve) ")
# (条件の名前, run_id の雛形 {p} {r}, routing, 表示する parallel)
CONDITIONS = {
    "fs_pn": ("flood_score", PARALLELS),
    "fs_su": ("flood_score", (4, 16, 30)),
    "cp75_pn": ("centroid_probe", (1, 4, 16, 30)),
    "mc_pn": ("multi_centroid", (30,)),
}
# 正しさの確認の基準になる選択（接続の持ち方は選択を変えないはず）
REFERENCE_RUN = {
    "flood_score": f"{T4}_flood_score_p1_r1",
    "multi_centroid": f"{T4}_multi_centroid_p1_r1",
    "centroid_probe": f"{ITER4}_cp75_p1_r1",
}


def pct(values: list[float], q: float) -> float | None:
    """百分位数（値が無ければ None）．"""
    return float(np.percentile(values, q)) if values else None


def count_connections_and_requests(path: Path) -> tuple[int | None, int | None]:
    """ノードの access log から，（新しい接続の数の下限，probe・retrieve の POST の数）を数える．ログが無ければ None．"""
    logs = sorted((path / "logs").glob("*.log"))
    if not logs:
        return None, None
    ports: set[tuple[str, str, str]] = set()
    requests = 0
    for log in logs:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = ACCESS_LINE.search(line)
            if m:
                ports.add((log.name, m.group(1), m.group(2)))
                requests += 1
    return len(ports), requests


def summarize(path: Path) -> dict[str, Any] | None:
    """1 回の実行の指標．実行が無ければ None．"""
    if not (path / "results.jsonl").exists() or not (path / "run_meta.json").exists():
        return None
    rows = read_results(path / "results.jsonl")
    meta = json.loads((path / "run_meta.json").read_text(encoding="utf-8"))
    ok = [r for r in rows if r["error"] is None]
    elapsed = meta["finished_at"] - meta["started_at"]
    t = [r["timings"] for r in ok]
    connections, requests = count_connections_and_requests(path)
    log = path / "requester.log"
    out: dict[str, Any] = {
        "http_pool": meta.get("http_pool"),
        "n": len(rows),
        "failed": len(rows) - len(ok),
        "failure_rate": (len(rows) - len(ok)) / len(rows),
        "errors": dict(Counter((r["error"] or "").split(":")[0] for r in rows if r["error"])),
        "e2e_p50": pct([x["e2e_s"] for x in t], 50),
        "e2e_p95": pct([x["e2e_s"] for x in t], 95),
        "probe_p50": pct([x["probe_s"] for x in t if "probe_s" in x], 50),
        "probe_p95": pct([x["probe_s"] for x in t if "probe_s" in x], 95),
        "retrieve_p50": pct([x["retrieve_s"] for x in t if "retrieve_s" in x], 50),
        "retrieve_p95": pct([x["retrieve_s"] for x in t if "retrieve_s" in x], 95),
        "route_p50": pct([x["route_s"] for x in t if "route_s" in x], 50),
        "throughput": len(ok) / elapsed,
        "elapsed_s": elapsed,
        "cpu_util": meta.get("requester_cpu_util"),
        "cpu_s": meta.get("requester_cpu_s"),
        "retries": sum("retrying" in ln for ln in log.read_text(errors="replace").splitlines()) if log.exists() else None,
        "new_connections": connections,
        "http_requests_per_s": requests / elapsed if requests is not None else None,
        "hit2": float(np.mean([r["bank"] in (r.get("selected_sources") or []) for r in ok])) if ok else None,
        "selection": {r["qid"]: set(r.get("selected_sources") or []) for r in ok},
    }
    node_max = [r["probe_node_s_max"] for r in ok if r.get("probe_node_s_max") is not None]
    out["node_s_max_p50"] = pct(node_max, 50)
    out["node_s_max_p95"] = pct(node_max, 95)
    return out


def spread(vals: list[float | None]) -> tuple[float, float, float] | None:
    """3 回の（最小，中央値，最大）．値が欠けていれば None．"""
    v = [x for x in vals if x is not None]
    return (min(v), float(median(v)), max(v)) if len(v) == len(REPEATS) else None


def fmt(x: float | None, nd: int = 3) -> str:
    """None を - にして丸める．"""
    return "-" if x is None else f"{x:.{nd}f}"


def main() -> None:
    """全条件の表，正しさの確認，環境の揺れの規則，H-H1〜H-H3・対照・K = 75 の記録の機械的な出力を行う．"""
    runs: dict[str, list[dict[str, Any] | None]] = {}
    print("run | 失敗率 | e2e p50 | e2e p95 | probe p50 | probe p95 | retrieve p50 | retrieve p95 | route p50 | 処理量 | cpu_util | 再試行 | 新接続(下限) | HTTP要求/秒 | node_s_max p95 | hit@2 | 誤り")
    for name, (routing, pars) in CONDITIONS.items():
        for p in pars:
            lst = [summarize(RESULTS / f"{PREFIX}_{name}_p{p}_r{r}") for r in REPEATS]
            runs[f"{name}_p{p}"] = lst
            for r, s in zip(REPEATS, lst, strict=True):
                if s is None:
                    print(f"{name} p{p} r{r} | (実行なし)")
                    continue
                print(
                    f"{name} p{p} r{r} | {s['failure_rate']:.4f} | {fmt(s['e2e_p50'], 4)} | {fmt(s['e2e_p95'], 4)} | {fmt(s['probe_p50'], 4)} | {fmt(s['probe_p95'], 4)} | "
                    f"{fmt(s['retrieve_p50'], 4)} | {fmt(s['retrieve_p95'], 4)} | {fmt(s['route_p50'], 4)} | {s['throughput']:.3f} | {fmt(s['cpu_util'])} | "
                    f"{s['retries']} | {s['new_connections']} | {fmt(s['http_requests_per_s'], 1)} | {fmt(s['node_s_max_p95'], 4)} | {fmt(s['hit2'], 4)} | {s['errors']}"
                )
    anchors: dict[str, dict[str, Any] | None] = {}
    for routing in ("flood_score", "multi_centroid"):
        for when in ("pre", "post"):
            s = summarize(RESULTS / f"{PREFIX}_anchor_shared_{routing}_p30_{when}")
            anchors[f"{routing}_{when}"] = s
            show = {k: v for k, v in s.items() if k != "selection"} if s else None
            print(f"anchor {routing} p30 {when} | {show if show else '(実行なし)'}")
    t4: dict[tuple[str, int], list[dict[str, Any] | None]] = {
        (x, p): [summarize(RESULTS / f"{T4}_{x}_p{p}_r{r}") for r in REPEATS]
        for x in ("flood_score", "multi_centroid")
        for p in PARALLELS
    }
    print("\n## T4 の参考値")
    for (x, p), lst in t4.items():
        print(f"t4 {x} p{p}: 処理量 {[round(s['throughput'], 3) for s in lst if s]} e2e_p95 {[round(s['e2e_p95'], 3) for s in lst if s]}")
    print("\n## Iteration 4 の参考値（K = 75，shared）")
    for p in (1, 4, 16, 30):
        lst = [summarize(RESULTS / f"{ITER4}_cp75_p{p}_r{r}") for r in REPEATS]
        print(f"iter4 cp75 p{p}: 処理量 {[round(s['throughput'], 3) for s in lst if s]} e2e_p95 {[round(s['e2e_p95'], 3) for s in lst if s]}")

    print("\n## 正しさの確認")
    spec_pool = {"fs_pn": "per_node", "fs_su": "shared_unlimited", "cp75_pn": "per_node", "mc_pn": "per_node"}
    bad = 0
    for key, lst in runs.items():
        name = key.rsplit("_p", 1)[0]
        routing = CONDITIONS[name][0]
        ref = summarize(RESULTS / REFERENCE_RUN[routing])
        for r, s in zip(REPEATS, lst, strict=True):
            if s is None or ref is None:
                continue
            common = set(s["selection"]) & set(ref["selection"])
            rate = float(np.mean([s["selection"][q] == ref["selection"][q] for q in common])) if common else float("nan")
            pool_ok = s["http_pool"] == spec_pool[name]
            conn_ok = not (name.endswith("_pn") and key.endswith("_p30") and (s["new_connections"] or 0) > PER_NODE_CONNECTION_LIMIT)
            if rate < CORRECT_SELECTION_RATE or not pool_ok or not conn_ok:
                bad += 1
                print(f"不一致: {key} r{r} 選択一致 {rate:.4f}（比較 {len(common)} 問） http_pool={s['http_pool']}（仕様 {spec_pool[name]}）{'' if conn_ok else f' 新接続 {s['new_connections']} > {PER_NODE_CONNECTION_LIMIT}'}")
    for key, s in anchors.items():
        if s is not None and s["http_pool"] != "shared":
            bad += 1
            print(f"不一致: anchor {key} http_pool={s['http_pool']}")
    print(f"(不一致 {bad} 件．0 なら全回で通過．新接続の数が 300 を超える per_node p30 は H-H3 の (ii) を外せたとみなさない)")

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
            print(f"{routing} {when}: 処理量 {a['throughput']:.3f} (T4 {thr[0]:.3f}〜{thr[2]:.3f}) {'内' if in_thr else '外'}，e2e p95 {a['e2e_p95']:.3f} (T4 {p95[0]:.3f}〜{p95[2]:.3f}) {'内' if in_p95 else '外'}，cpu_util {fmt(a['cpu_util'])}")
    print(f"環境の揺れの規則: {'全て内（比べる相手は T4 の 3 回）' if env_ok else '外れあり（比べる相手は前後の確認の値）'}")

    def baseline(routing: str) -> tuple[Any, Any, str]:
        """比べる相手の（処理量，e2e p95，名前）．各は（最小，中央値，最大）．"""
        if env_ok:
            return (
                spread([s["throughput"] if s else None for s in t4[(routing, 30)]]),
                spread([s["e2e_p95"] if s else None for s in t4[(routing, 30)]]),
                "T4 の 3 回",
            )
        pair = [anchors[f"{routing}_pre"], anchors[f"{routing}_post"]]
        if any(a is None for a in pair):
            return None, None, "前後の確認 2 回（欠け）"
        thr = [a["throughput"] for a in pair if a]
        p95 = [a["e2e_p95"] for a in pair if a]
        return (min(thr), float(median(thr)), max(thr)), (min(p95), float(median(p95)), max(p95)), "前後の確認 2 回"

    print("\n## 判定の規則（flood_score，parallel = 30）")
    f_thr, f_p95, base = baseline("flood_score")
    pn_thr = spread([s["throughput"] if s else None for s in runs["fs_pn_p30"]])
    pn_p95 = spread([s["e2e_p95"] if s else None for s in runs["fs_pn_p30"]])
    su_thr = spread([s["throughput"] if s else None for s in runs["fs_su_p30"]])
    su_p95 = spread([s["e2e_p95"] if s else None for s in runs["fs_su_p30"]])
    print(f"比べる相手: flood_score × shared，{base}．処理量 {f_thr}，e2e p95 {f_p95}")
    print(f"per_node: 処理量 {pn_thr}，e2e p95 {pn_p95}．shared_unlimited: 処理量 {su_thr}，e2e p95 {su_p95}")
    cpu_shared = [a["cpu_util"] for k, a in anchors.items() if k.startswith("flood_score") and a and a["cpu_util"] is not None]
    cpu_pn = [s["cpu_util"] for s in runs["fs_pn_p30"] if s and s["cpu_util"] is not None]
    cpu_su = [s["cpu_util"] for s in runs["fs_su_p30"] if s and s["cpu_util"] is not None]
    print(f"requester_cpu_util（flood_score・parallel 30）: shared（前後）{[round(c, 3) for c in cpu_shared]}，per_node {[round(c, 3) for c in cpu_pn]}，shared_unlimited {[round(c, 3) for c in cpu_su]}")
    if f_thr and f_p95 and pn_thr and pn_p95:
        hh1 = pn_thr[0] > f_thr[2]
        hh2 = pn_p95[2] < f_p95[0]
        ratio = pn_thr[1] / f_thr[1]
        print(f"H-H1: {hh1}，H-H2: {hh2}，処理量の中央値の比（per_node / shared）: {ratio:.3f}")
        if hh1 and hh2 and ratio >= ADOPT_RATIO:
            verdict = "採用"
        elif hh1 or hh2:
            verdict = "部分的に支持"
        else:
            verdict = "棄却"
        print(f"結論（規則の機械的な適用）: {verdict}")
        if su_thr:
            su_ratio = su_thr[1] / f_thr[1]
            su_vs_pn = su_thr[1] / pn_thr[1]
            if abs(su_ratio - 1) <= H3_SAME_BAND and ratio >= ADOPT_RATIO:
                h3 = "(i) 池の管理の費用"
            elif su_vs_pn >= H3_SU_VS_PN and su_ratio >= ADOPT_RATIO:
                h3 = "(ii) 池の上限による作り直し"
            elif abs(ratio - 1) <= H3_SAME_BAND:
                h3 = "(iii) 池以外"
            else:
                h3 = "混合"
            print(f"H-H3（規則の出力）: {h3}（shared_unlimited / shared = {su_ratio:.3f}，shared_unlimited / per_node = {su_vs_pn:.3f}，per_node / shared = {ratio:.3f}）")
        else:
            print("H-H3: shared_unlimited の 3 回が欠けていて出せない")
    shared_cpu_hint = bool(cpu_shared) and all(c >= CPU_SATURATED for c in cpu_shared)
    print(f"CPU の手がかり（shared の parallel 30 で requester_cpu_util が全て 0.9 以上）: {shared_cpu_hint}")

    print("\n## 対照（multi_centroid × per_node，parallel 30 の処理量が T4 の [最小 × 0.9, 最大 × 1.1] に入るか）")
    mc_t4 = spread([s["throughput"] if s else None for s in t4[("multi_centroid", 30)]])
    mc_thr = spread([s["throughput"] if s else None for s in runs["mc_pn_p30"]])
    if mc_t4 and mc_thr:
        inside = all(mc_t4[0] * ENV_LOW <= v <= mc_t4[2] * ENV_HIGH for v in (s["throughput"] for s in runs["mc_pn_p30"] if s))
        print(f"mc per_node 処理量 {mc_thr}，T4 {mc_t4}: {'内' if inside else '外（対照が外れた）'}")
    else:
        print("対照の 3 回が欠けている")

    print("\n## K = 75 × per_node（判定に使わない．Iteration 4 の順序が per_node でも成り立つか）")
    cp_thr = spread([s["throughput"] if s else None for s in runs["cp75_pn_p30"]])
    cp_p95 = spread([s["e2e_p95"] if s else None for s in runs["cp75_pn_p30"]])
    print(f"cp75 per_node p30: 処理量 {cp_thr}，e2e p95 {cp_p95}；flood_score per_node p30: 処理量 {pn_thr}，e2e p95 {pn_p95}")
    if cp_thr and cp_p95 and pn_thr and pn_p95:
        print(f"処理量: cp75 の範囲が flood_score の範囲より{'上' if cp_thr[0] > pn_thr[2] else '下' if cp_thr[2] < pn_thr[0] else '重なる'}，"
              f"e2e p95: cp75 の範囲が flood_score の範囲より{'下' if cp_p95[2] < pn_p95[0] else '上' if cp_p95[0] > pn_p95[2] else '重なる'}")

    print("\n## 曲線（3 回の最小・中央値・最大）")
    for name, (routing, pars) in CONDITIONS.items():
        for p in pars:
            lst = runs[f"{name}_p{p}"]
            print(f"  {name} p{p}: 処理量 {spread([s['throughput'] if s else None for s in lst])}，e2e p50 {spread([s['e2e_p50'] if s else None for s in lst])}，e2e p95 {spread([s['e2e_p95'] if s else None for s in lst])}，cpu_util {spread([s['cpu_util'] if s else None for s in lst])}")
    for x in ("flood_score", "multi_centroid"):
        for p in PARALLELS:
            lst = t4[(x, p)]
            print(f"  T4 {x} p{p}: 処理量 {spread([s['throughput'] if s else None for s in lst])}，e2e p95 {spread([s['e2e_p95'] if s else None for s in lst])}")


if __name__ == "__main__":
    main()
