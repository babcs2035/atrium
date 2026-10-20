"""p0007 A3 のオフラインのゲートの集計：frontier-eval の frontier.json から，方式の表と verdict.json（verdict_original のみ）を作る．

使い方：`uv run python -m atrium.frontier_a3 --frontier results/<run>/frontier.json --out-dir results/<run>`
frontier.json は `atrium frontier-eval --methods hybrid_summary,bm25_probe,hybrid_xl,...`（n30．m = 2）の出力．
hit@3 は m = 2 の実行から読む（選ぶ受信箱の集合が m に依らないので，m = 3 の hit@3 と同じ値．m = 3 の要求数は m = 2 の要求数 + 1）．
規則（journal の「p0007 A3：持ち主の特定（事前登録）」）：族ごとに dev の hit@3 が最大で要求数が上限以下の設定を選び，
dev の hit@3 が最大の族を test で hybrid_probe（D = 16）と比べる．メール本文は扱わない（集計値だけ）．
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# 比べる基準：hybrid_probe（V = 64,000・w = 0.5・D = 16．p0007 A1 の P2P の構成）
BASELINE_FAMILY = "hybrid_summary"
BASELINE_PARAMS: dict[str, Any] = {"V": 64000, "w": 0.5, "D": 16}
# 事前登録した族（A3 の (a)・(c)・(b)）
FAMILIES = ("bm25_shared", "bm25_fuse", "hybrid_xl")
# ゲート：主は m = 3（A1 で owner_m: 3 を採ったため）．併記は m = 2
PRIMARY_M = 3
SECONDARY_M = 2
MIN_GAIN = 0.05
MAX_REQUEST_RATIO = 1.5
# frontier.json の要求数は m = 2 の値．m の違いは最終の本照会の数だけ
FRONTIER_M = 2
# 上限の参考：全員に聞く（D = 30）
CEILING_D = 30


def requests_at(row: dict[str, Any], m: int) -> float:
    """行の要求数の平均を m（本照会の数）に直す．"""
    return float(row["requests_per_query"]["mean"]) + (m - FRONTIER_M)


def find_row(
    rows: list[dict[str, Any]], family: str, params: dict[str, Any], split: str, subset: str
) -> dict[str, Any]:
    """族・設定・split・subset が一致する 1 行（params は部分一致．bm25_shared と bm25_fuse は族で区別する）．"""
    found = [
        r
        for r in rows
        if r["family"] == family
        and r["split"] == split
        and r["subset"] == subset
        and all(r["params"].get(k) == v for k, v in params.items())
    ]
    if not found:
        raise ValueError(f"no row for {family} {params} ({split}, {subset})")
    return found[0]


def pick_in_family(
    rows: list[dict[str, Any]], family: str, m: int, max_requests: float
) -> dict[str, Any]:
    """族の設定を dev だけで選ぶ：要求数が上限以下で dev の hit@m が最大のもの（同点は D の小さい方）．"""
    dev = [
        r
        for r in rows
        if r["family"] == family
        and r["split"] == "dev"
        and r["subset"] == "all"
        and requests_at(r, m) <= max_requests
    ]
    if not dev:
        raise ValueError(f"no dev row of {family} within {max_requests} requests")
    best = max(dev, key=lambda r: (r[f"hit@{m}"], -r["requests_per_query"]["mean"]))
    return {
        "family": family,
        "method": best["method"],
        "params": best["params"],
        "dev_hit": best[f"hit@{m}"],
        "test": find_row(rows, family, best["params"], "test", "all"),
        "test_no_owner_name": find_row(rows, family, best["params"], "test", "no_owner_name"),
    }


def gate(rows: list[dict[str, Any]], baseline: dict[str, Any], m: int) -> dict[str, Any]:
    """m を主として，族ごとの選択と，dev の hit@m が最大の族の test のゲート判定を出す．"""
    base_requests = requests_at(baseline, m)
    max_requests = MAX_REQUEST_RATIO * base_requests
    chosen = {fam: pick_in_family(rows, fam, m, max_requests) for fam in FAMILIES}
    best_family = max(
        chosen,
        key=lambda f: (chosen[f]["dev_hit"], -chosen[f]["test"]["requests_per_query"]["mean"]),
    )
    out: dict[str, Any] = {"m": m, "max_requests": max_requests, "by_family": {}}
    for fam, c in chosen.items():
        test = c["test"]
        gain = test[f"hit@{m}"] - baseline[f"hit@{m}"]
        out["by_family"][fam] = {
            "params": c["params"],
            "dev_hit": c["dev_hit"],
            "test_hit": test[f"hit@{m}"],
            "gain_over_hybrid_probe": gain,
            "requests": requests_at(test, m),
            "passed": bool(gain >= MIN_GAIN and requests_at(test, m) <= max_requests),
        }
    out["selected_family"] = best_family
    out["passed"] = out["by_family"][best_family]["passed"]
    return out


def fmt_ci(row: dict[str, Any], key: str) -> str:
    """区間つきの hit の文字列．"""
    ci = row.get(f"{key}_ci95")
    return f"{row[key]:.4f}" + ("" if ci is None else f"（{ci[0]:.3f}〜{ci[1]:.3f}）")


def shared_table_bytes(row: dict[str, Any], base_advert: int) -> int:
    """exact の IDF の表（全語彙の（語，文書頻度））の大きさ．bm25_probe は 1 段目が基準と同じなので，公開情報の差がその表である．"""
    exact = row["method"] == "bm25_probe" and row["params"]["idf"] == "exact"
    return int(row["advert_bytes"]) - base_advert if exact else 0


def table_line(label: str, row: dict[str, Any], no_owner: dict[str, Any], base_advert: int) -> str:
    """表の 1 行（test・全問）．"""
    attr = row.get("attribution_top1")
    auc = row.get("attribution_auc")
    extra = shared_table_bytes(row, base_advert)
    return (
        f"| {label} | {row['hit@1']:.4f} | {row['hit@2']:.4f} | {fmt_ci(row, 'hit@3')} "
        f"| {no_owner['hit@3']:.4f} | {requests_at(row, 2):.0f} / {requests_at(row, 3):.0f} "
        f"| {row['advert_bytes']:,}（うち共有の IDF の表 {extra:,}） "
        f"| {'-' if attr is None else format(attr, '.3f')}・{'-' if auc is None else format(auc, '.3f')} |"
    )


def render_table(
    rows: list[dict[str, Any]], baseline: dict[str, Any], gate3: dict[str, Any]
) -> str:
    """方式 × {hit@1,2,3，要求数，公開情報の大きさ，持ち主の特定率} の表（test・全問．m = 2 / m = 3 の要求数）．"""
    base_advert = int(baseline["advert_bytes"])
    header = [
        "| 方式（設定） | hit@1 | hit@2 | hit@3（95% 区間） | A 群を除く hit@3 | 要求数（m = 2 / m = 3） | 公開情報の大きさ（byte） | 持ち主の特定 top-1・AUC |",
        "|---|---|---|---|---|---|---|---|",
    ]
    base_no = find_row(rows, BASELINE_FAMILY, BASELINE_PARAMS, "test", "no_owner_name")
    lines = [table_line("hybrid_probe D = 16（基準）", baseline, base_no, base_advert)]
    for fam, c in gate3["by_family"].items():
        test = find_row(rows, fam, c["params"], "test", "all")
        no = find_row(rows, fam, c["params"], "test", "no_owner_name")
        mark = "（選んだ方式）" if fam == gate3["selected_family"] else ""
        lines.append(table_line(f"{fam} {json.dumps(c['params'])}{mark}", test, no, base_advert))
    xl_all = [
        r
        for r in rows
        if r["family"] == "hybrid_xl"
        and r["split"] == "test"
        and r["subset"] == "all"
        and r["params"]["D"] == gate3["by_family"]["hybrid_xl"]["params"]["D"]
        and r["params"]["V"] == max(x["params"]["V"] for x in rows if x["family"] == "hybrid_xl")
    ][0]
    xl_no = find_row(rows, "hybrid_xl", xl_all["params"], "test", "no_owner_name")
    lines.append(
        table_line(
            f"hybrid_xl（語彙の全て）{json.dumps(xl_all['params'])}", xl_all, xl_no, base_advert
        )
    )
    for kind in ("exact", "published"):
        params = {"D": CEILING_D, "idf": kind}
        row = [
            r
            for r in rows
            if r["family"] == "bm25_shared"
            and r["split"] == "test"
            and r["subset"] == "all"
            and all(r["params"].get(k) == v for k, v in params.items())
        ][0]
        no = find_row(rows, "bm25_shared", row["params"], "test", "no_owner_name")
        lines.append(table_line(f"上限：bm25_shared D = 30（{kind}）", row, no, base_advert))
    # 参考：基準と同じ要求数（D = 16）の点．dev の選択には使っていない（上限の D = 24 が選ばれたので，同じ要求数の値を並べる）
    for fam, kind, wb in (
        ("bm25_shared", "exact", None),
        ("bm25_shared", "published", None),
        ("bm25_fuse", "exact", 2.0),
        ("bm25_fuse", "published", 2.0),
    ):
        match = {"D": BASELINE_PARAMS["D"], "idf": kind} | ({} if wb is None else {"wb": wb})
        ref = [
            r
            for r in rows
            if r["family"] == fam
            and r["split"] == "test"
            and r["subset"] == "all"
            and all(r["params"].get(k) == v for k, v in match.items())
        ][0]
        no = find_row(rows, fam, ref["params"], "test", "no_owner_name")
        lines.append(
            table_line(f"参考（同じ要求数）{fam} {json.dumps(match)}", ref, no, base_advert)
        )
    return "\n".join(header + lines) + "\n"


def main() -> int:
    """frontier.json から table.md と verdict.json を書く．"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontier", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    rows: list[dict[str, Any]] = [
        r for r in json.loads(args.frontier.read_text(encoding="utf-8")) if r["placement"] == "n30"
    ]
    baseline = find_row(rows, BASELINE_FAMILY, BASELINE_PARAMS, "test", "all")
    primary = gate(rows, baseline, PRIMARY_M)
    secondary = gate(rows, baseline, SECONDARY_M)
    verdict = {
        "verdict_original": {
            "rule": "test hit@3 gain over hybrid_probe D=16 >= 0.05 and requests <= 1.5x hybrid_probe",
            "baseline": {
                "params": BASELINE_PARAMS,
                "hit@2": baseline["hit@2"],
                "hit@3": baseline["hit@3"],
                "requests_m3": requests_at(baseline, PRIMARY_M),
            },
            "min_gain": MIN_GAIN,
            "primary_m3": primary,
            "secondary_m2_not_used_for_verdict": secondary,
            "passed": primary["passed"],
        }
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "verdict.json").write_text(
        json.dumps(verdict, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    table = render_table(rows, baseline, primary)
    (args.out_dir / "table.md").write_text(table, encoding="utf-8")
    print(table)
    print(json.dumps(verdict, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
