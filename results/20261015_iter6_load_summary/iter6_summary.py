"""Iteration 6：1 段目の採点の重心の前計算（SourceProfile.advert_centroids）後の負荷試験の集計（事前登録の指標・正しさの確認・判定規則の機械的な出力）．既存の結果を読むだけで何も書き換えない．

使い方（リポジトリの直下で）:
    uv run python results/20261015_iter6_load_summary/iter6_summary.py > results/20261015_iter6_load_summary/iter6_summary.out

定義は iter5_summary.py と同じ（処理量 = 成功した問の数 ÷ run_meta.json の finished_at − started_at．範囲が重ならないときだけ「差がある」）．
追加: route_s の p50・p95，R = 1 問あたりの要求の数 ÷ (1/処理量 − route_s の p50)（parallel 30．記録），1 問あたりの probe を受けたデバイスの数（K = 30・75）．
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
PREFIX = "20261015_iter6"
ITER5 = "20261015_iter5"
T4 = "20261014_t4"
ITER4 = "20261014_iter4"
PARALLELS = (1, 4, 16, 30)
REPEATS = (1, 2, 3)
ENV_LOW, ENV_HIGH = 0.9, 1.1
ADOPT_RATIO = 1.5
ROUTE_S_LIMIT = 0.010
CPU_SATURATED = 0.9
ACCESS_LINE = re.compile(r"INFO:\s+(\S+):(\d+) - \"POST (/v1/probe|/v1/retrieve) ")
# 条件名 -> routing．実行の名前は {PREFIX}_{条件名}_p{parallel}_r{n}
CONDITIONS = {"mc_pn": "multi_centroid", "cp75_pn": "centroid_probe", "cp30_pn": "centroid_probe"}
# 正しさの確認の基準になる選択（100% 一致を要求）
REFERENCE_RUN = {
    "mc_pn": f"{T4}_multi_centroid_p1_r1",
    "cp75_pn": f"{ITER4}_cp75_p1_r1",
    "cp30_pn": f"{ITER4}_cp30_p1_r1",
}
# 1 問あたりの要求の数（記録の R の分子．実測の HTTP 要求の数 ÷ 問数で置き換えられるときはそちらを使う）
REQUESTS_PER_QUESTION_FALLBACK = {"mc_pn": 2.0, "cp75_pn": 31.0, "cp30_pn": 22.0}


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
        "route_p95": pct([x["route_s"] for x in t if "route_s" in x], 95),
        "throughput": len(ok) / elapsed,
        "elapsed_s": elapsed,
        "cpu_util": meta.get("requester_cpu_util"),
        "retries": sum("retrying" in ln for ln in log.read_text(errors="replace").splitlines()) if log.exists() else None,
        "new_connections": connections,
        "requests_per_question": requests / len(rows) if requests is not None else None,
        "http_requests_per_s": requests / elapsed if requests is not None else None,
        "hit2": float(np.mean([r["bank"] in (r.get("selected_sources") or []) for r in ok])) if ok else None,
        "selection": {r["qid"]: set(r.get("selected_sources") or []) for r in ok},
    }
    node_max = [r["probe_node_s_max"] for r in ok if r.get("probe_node_s_max") is not None]
    out["node_s_max_p95"] = pct(node_max, 95)
    return out


def spread(vals: list[float | None]) -> tuple[float, float, float] | None:
    """3 回の（最小，中央値，最大）．値が欠けていれば None．"""
    v = [x for x in vals if x is not None]
    return (min(v), float(median(v)), max(v)) if len(v) == len(REPEATS) else None


def fmt(x: float | None, nd: int = 3) -> str:
    """None を - にして丸める．"""
    return "-" if x is None else f"{x:.{nd}f}"


def thr_p95(lst: list[dict[str, Any] | None]) -> tuple[Any, Any]:
    """3 回の（処理量，e2e p95）の（最小，中央値，最大）．"""
    return spread([s["throughput"] if s else None for s in lst]), spread([s["e2e_p95"] if s else None for s in lst])


def rel(a: tuple[float, float, float] | None, b: tuple[float, float, float] | None) -> str:
    """範囲 a が範囲 b より上・下・重なるのどれか．"""
    if a is None or b is None:
        return "欠け"
    return "上" if a[0] > b[2] else "下" if a[2] < b[0] else "重なる"


def main() -> None:
    """全条件の表，正しさの確認，環境の揺れの規則，H-S1〜H-S5 と結論の機械的な出力を行う．"""
    runs: dict[str, list[dict[str, Any] | None]] = {}
    print("run | 失敗率 | e2e p50 | e2e p95 | probe p50 | probe p95 | retrieve p50 | retrieve p95 | route p50 | route p95 | 処理量 | cpu_util | 再試行 | 新接続(下限) | 要求/問 | HTTP要求/秒 | node_s_max p95 | hit@2 | 誤り")
    for name in CONDITIONS:
        for p in PARALLELS:
            lst = [summarize(RESULTS / f"{PREFIX}_{name}_p{p}_r{r}") for r in REPEATS]
            runs[f"{name}_p{p}"] = lst
            for r, s in zip(REPEATS, lst, strict=True):
                if s is None:
                    print(f"{name} p{p} r{r} | (実行なし)")
                    continue
                print(
                    f"{name} p{p} r{r} | {s['failure_rate']:.4f} | {fmt(s['e2e_p50'], 4)} | {fmt(s['e2e_p95'], 4)} | {fmt(s['probe_p50'], 4)} | {fmt(s['probe_p95'], 4)} | "
                    f"{fmt(s['retrieve_p50'], 4)} | {fmt(s['retrieve_p95'], 4)} | {fmt(s['route_p50'], 5)} | {fmt(s['route_p95'], 5)} | {s['throughput']:.3f} | {fmt(s['cpu_util'])} | "
                    f"{s['retries']} | {s['new_connections']} | {fmt(s['requests_per_question'], 1)} | {fmt(s['http_requests_per_s'], 1)} | {fmt(s['node_s_max_p95'], 4)} | {fmt(s['hit2'], 4)} | {s['errors']}"
                )
    anchors = {when: summarize(RESULTS / f"{PREFIX}_anchor_fs_pn_p30_{when}") for when in ("pre", "post")}
    for when, s in anchors.items():
        show = {k: v for k, v in s.items() if k != "selection"} if s else None
        print(f"anchor flood_score per_node p30 {when} | {show if show else '(実行なし)'}")

    # Iteration 5 の per_node の値（比べる相手）
    i5 = {
        key: [summarize(RESULTS / f"{ITER5}_{key}_p30_r{r}") for r in REPEATS]
        for key in ("fs_pn", "mc_pn", "cp75_pn")
    }
    i5_thr = {k: thr_p95(v)[0] for k, v in i5.items()}
    i5_p95 = {k: thr_p95(v)[1] for k, v in i5.items()}
    print("\n## Iteration 5（per_node，parallel 30）の参考値")
    for k in i5:
        print(f"iter5 {k} p30: 処理量 {i5_thr[k]}，e2e p95 {i5_p95[k]}")

    print("\n## 正しさの確認（selected_sources が基準と 100% 一致，http_pool=per_node）")
    bad = 0
    refs = {name: summarize(RESULTS / REFERENCE_RUN[name]) for name in CONDITIONS}
    for key, lst in runs.items():
        name = key.rsplit("_p", 1)[0]
        ref = refs[name]
        for r, s in zip(REPEATS, lst, strict=True):
            if s is None or ref is None:
                continue
            common = set(s["selection"]) & set(ref["selection"])
            rate = float(np.mean([s["selection"][q] == ref["selection"][q] for q in common])) if common else float("nan")
            if rate < 1.0 or s["http_pool"] != "per_node":
                bad += 1
                print(f"不一致: {key} r{r} 選択一致 {rate:.4f}（比較 {len(common)} 問） http_pool={s['http_pool']}")
    for when, s in anchors.items():
        if s is not None and s["http_pool"] != "per_node":
            bad += 1
            print(f"不一致: anchor {when} http_pool={s['http_pool']}")
    all_runs = [s for lst in runs.values() for s in lst]
    print(f"(不一致 {bad} 件．0 なら全回で通過．欠けた実行は数えていない: 実行なし {sum(s is None for s in all_runs)} 件)")

    print("\n## 環境の揺れの規則（前後 2 回が Iteration 5 の fs_pn_p30 の [最小 × 0.9, 最大 × 1.1] に入るか）")
    env_ok = True
    fs_thr, fs_p95 = i5_thr["fs_pn"], i5_p95["fs_pn"]
    anchor_thr: list[float] = []
    for when, a in anchors.items():
        if a is None or fs_thr is None or fs_p95 is None:
            env_ok = False
            continue
        anchor_thr.append(a["throughput"])
        in_thr = fs_thr[0] * ENV_LOW <= a["throughput"] <= fs_thr[2] * ENV_HIGH
        in_p95 = fs_p95[0] * ENV_LOW <= a["e2e_p95"] <= fs_p95[2] * ENV_HIGH
        env_ok &= in_thr and in_p95
        print(f"{when}: 処理量 {a['throughput']:.3f} (範囲 {fs_thr[0] * ENV_LOW:.3f}〜{fs_thr[2] * ENV_HIGH:.3f}) {'内' if in_thr else '外'}，e2e p95 {a['e2e_p95']:.3f} (範囲 {fs_p95[0] * ENV_LOW:.3f}〜{fs_p95[2] * ENV_HIGH:.3f}) {'内' if in_p95 else '外'}，cpu_util {fmt(a['cpu_util'])}")
    ratio_needed = ADOPT_RATIO
    if not env_ok and len(anchor_thr) == 2 and fs_thr:
        ratio_needed = ADOPT_RATIO * max(1.0, fs_thr[1] / min(anchor_thr))
    print(f"環境の揺れの規則: {'全て内' if env_ok else '外れあり（環境が変わった）'}．採用の倍率 {ratio_needed:.3f}")

    print("\n## 判定の規則（parallel 30．比べる相手は Iteration 5 の per_node の 3 回）")
    all36 = [s for name in CONDITIONS for p in PARALLELS for s in runs[f"{name}_p{p}"]]
    present = [s for s in all36 if s is not None]
    hs1 = len(present) == len(all36) and all(s["route_p50"] is not None and s["route_p50"] < ROUTE_S_LIMIT for s in present)
    worst = max((s["route_p50"] for s in present if s["route_p50"] is not None), default=None)
    print(f"H-S1: {hs1}（実行 {len(present)}/{len(all36)}，route_s p50 の最大 {fmt(worst, 5)} 秒．基準 {ROUTE_S_LIMIT} 秒未満）")
    cur = {name: thr_p95(runs[f"{name}_p30"]) for name in CONDITIONS}
    for name in CONDITIONS:
        print(f"{name} p30: 処理量 {cur[name][0]}，e2e p95 {cur[name][1]}")
    hs2 = rel(cur["mc_pn"][0], i5_thr["mc_pn"]) == "上"
    hs3 = rel(cur["cp75_pn"][0], i5_thr["cp75_pn"]) == "上"
    hs4 = rel(cur["mc_pn"][1], i5_p95["mc_pn"]) == "下" and rel(cur["cp75_pn"][1], i5_p95["cp75_pn"]) == "下"
    print(f"H-S2: {hs2}（multi_centroid の処理量 {cur['mc_pn'][0]} は Iteration 5 の {i5_thr['mc_pn']} より {rel(cur['mc_pn'][0], i5_thr['mc_pn'])}）")
    print(f"H-S3: {hs3}（K = 75 の処理量 {cur['cp75_pn'][0]} は Iteration 5 の {i5_thr['cp75_pn']} より {rel(cur['cp75_pn'][0], i5_thr['cp75_pn'])}）")
    print(f"H-S4: {hs4}（multi_centroid の e2e p95 は {rel(cur['mc_pn'][1], i5_p95['mc_pn'])}，K = 75 は {rel(cur['cp75_pn'][1], i5_p95['cp75_pn'])}．どちらも「下」で成立）")
    mc_med = cur["mc_pn"][0][1] if cur["mc_pn"][0] else None
    cp_med = cur["cp75_pn"][0][1] if cur["cp75_pn"][0] else None
    mc_need = ratio_needed * 8.551
    cp_need = ratio_needed * 5.372
    big = mc_med is not None and cp_med is not None and mc_med >= mc_need and cp_med >= cp_need
    print(f"採用の倍率の条件: multi_centroid の中央値 {fmt(mc_med)} ≥ {mc_need:.2f}，K = 75 の中央値 {fmt(cp_med)} ≥ {cp_need:.2f} → {big}")
    if hs1 and hs2 and hs3 and hs4 and big:
        verdict = "採用"
    elif hs1 and (hs2 or hs3):
        verdict = "部分的に支持"
    else:
        verdict = "棄却"
    print(f"結論（規則の機械的な適用．正しさの確認の不一致 {bad} 件が 0 のときだけ有効）: {verdict}")

    print("\n## H-S5（記録）：parallel 30．Iteration 5 の flood_score × per_node の範囲との関係")
    print(f"flood_score per_node（Iteration 5）: 処理量 {fs_thr}，e2e p95 {fs_p95}")
    for name in CONDITIONS:
        print(f"{name}: 処理量は flood_score より{rel(cur[name][0], fs_thr)}，e2e p95 は{rel(cur[name][1], fs_p95)}（上・下・重なる．e2e は「下」が良い側）")
    order = sorted(CONDITIONS, key=lambda n: -(cur[n][0][1] if cur[n][0] else 0))
    print(f"処理量の中央値の順（大きい方から）: {order}")

    print("\n## R = 1 問あたりの要求の数 ÷ (1/処理量 − route_s p50)（parallel 30．記録）")
    for name in CONDITIONS:
        for r, s in zip(REPEATS, runs[f"{name}_p30"], strict=True):
            if s is None:
                continue
            per_q = s["requests_per_question"] or REQUESTS_PER_QUESTION_FALLBACK[name]
            denom = 1 / s["throughput"] - (s["route_p50"] or 0.0)
            print(f"{name} r{r}: 要求/問 {per_q:.2f}，処理量 {s['throughput']:.3f}，route_s p50 {fmt(s['route_p50'], 5)}，R = {per_q / denom:.1f} 要求/秒，cpu_util {fmt(s['cpu_util'])}")
    cpu_all = [s["cpu_util"] for s in present if s["cpu_util"] is not None]
    print(f"requester_cpu_util が 0.9 以上の実行: {sum(c >= CPU_SATURATED for c in cpu_all)}/{len(cpu_all)}")

    print("\n## 曲線（3 回の最小・中央値・最大）")
    for name in CONDITIONS:
        for p in PARALLELS:
            lst = runs[f"{name}_p{p}"]
            print(f"  {name} p{p}: 処理量 {spread([s['throughput'] if s else None for s in lst])}，e2e p50 {spread([s['e2e_p50'] if s else None for s in lst])}，e2e p95 {spread([s['e2e_p95'] if s else None for s in lst])}，route p50 {spread([s['route_p50'] if s else None for s in lst])}，cpu_util {spread([s['cpu_util'] if s else None for s in lst])}")
    print("\n## 参考（Iteration 4・5 の値）")
    for p in PARALLELS:
        for tag, pre in (("iter4 cp75 shared", f"{ITER4}_cp75"), ("iter4 cp30 shared", f"{ITER4}_cp30"), ("iter5 cp75 per_node", f"{ITER5}_cp75_pn"), ("iter5 fs per_node", f"{ITER5}_fs_pn")):
            lst = [summarize(RESULTS / f"{pre}_p{p}_r{r}") for r in REPEATS]
            t, e = thr_p95(lst)
            print(f"  {tag} p{p}: 処理量 {t}，e2e p95 {e}")


if __name__ == "__main__":
    main()
