"""d0009 A4（Iteration 32）：質問者へ渡る文書と，生成の委託で GPU の PC へ送る文書を合わせた露出の全体を既存の results から再集計する．

使い方（リポジトリの直下．オフライン．実機・GPU は使わない）:
    uv run python scripts/d0009/a4_exposure_total.py \
        --h d0009_a3_p1 --h-reruns d0009_a3_p2 d0009_a3_p3 --h2 d0009_m1_h2_base --test p0007_a6_test \
        --out results/d0009_a4_exposure

出力は `<out>/{exposure_total.json, verdict.json, analysis_report.md}`．文書 ID・本文は出さず，数と割合だけを書く．
受け手は，質問者（wafl-ctrl5．全候補の context_doc_ids）と，委託先の GPU の PC（delegated_to の候補の context_doc_ids）である．
候補と node_answers は source（受信箱）で対応させ，index の順序には頼らない．受信箱・文書 ID・分母の規則は
`atrium.multi_requester_eval` の既存の関数を使う．
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from atrium.analysis import read_results
from atrium.multi_requester_eval import (
    candidate_doc_ids,
    inbox_of_doc_id,
    inboxes_by_host,
    retrieval_signature,
    total_docs_of,
)
from atrium.verdict import write_verdict

REQUESTER = "wafl-ctrl5"
RESULTS = Path("results")
TEST_E_REQ_DOCS = 27_704  # d0009 A1 の multi_requester.json と d0007 の報告書の 37.6% の文書数
H_E_REQ_A1 = 0.3698151060022773  # Iteration 23 の H の E_any（G-H の参照値）
G_H_TOLERANCE = 0.005
G_D_MAX_RATE = 0.001
H_A4A_MAX_DIFF = 0.001
H_B_THRESHOLD = 0.5
EXPECTED = {"E_deleg": (0.22, 0.30), "E_pair_total": (0.60, 0.70), "E_max_gpu": (0.02, 0.04)}

DocSets = dict[str, set[str]]


def recipient_docs_of_row(row: Mapping[str, Any]) -> DocSets:
    """1 問の受け手 → 受け取る文書．質問者は全候補，GPU の PC は delegated_to がその PC の候補（source で対応）．error の行は空．"""
    if row.get("error") is not None:
        return {}
    out: DocSets = {REQUESTER: candidate_doc_ids(row)}
    contexts = retrieval_signature(row)
    for na in row.get("node_answers") or []:
        gpu = na.get("delegated_to")
        if gpu and na.get("source") in contexts:
            out.setdefault(gpu, set()).update(contexts[na["source"]])
    return out


def accumulate(
    rows: Sequence[Mapping[str, Any]], inboxes: Mapping[str, set[str]], acc: DocSets
) -> int:
    """rows の受け手ごとの文書を acc へ足す．受け手が持つ受信箱の文書は数えない（除いた数を返す）．"""
    excluded = 0
    for row in rows:
        for recipient, docs in recipient_docs_of_row(row).items():
            own = inboxes.get(recipient, set())
            kept = {d for d in docs if inbox_of_doc_id(d) not in own}
            excluded += len(docs) - len(kept)
            acc.setdefault(recipient, set()).update(kept)
    return excluded


def per_inbox_distribution(
    exposed: set[str],
    inboxes: Mapping[str, set[str]],
    n_docs_by_inbox: Mapping[str, int],
    gpu_hosts: set[str],
) -> dict[str, Any]:
    """受信箱ごとの露出の割合（その受信箱の文書のうち外に出た割合）の最小・中央値・平均・最大．持ち主が CPU・GPU の別も出す．"""
    count: dict[str, int] = {}
    for d in exposed:
        count[inbox_of_doc_id(d)] = count.get(inbox_of_doc_id(d), 0) + 1
    shares: dict[str, list[float]] = {"all": [], "cpu": [], "gpu": []}
    for host, boxes in inboxes.items():
        kind = "gpu" if host in gpu_hosts else "cpu"
        for box in boxes:
            share = count.get(box, 0) / n_docs_by_inbox[box]
            shares["all"].append(share)
            shares[kind].append(share)
    return {
        k: {
            "n_inboxes": len(v),
            "min": min(v),
            "median": statistics.median(v),
            "mean": statistics.fmean(v),
            "max": max(v),
        }
        for k, v in shares.items()
        if v
    }


def exposure_metrics(
    acc: DocSets,
    total: int,
    inboxes: Mapping[str, set[str]],
    n_docs_by_inbox: Mapping[str, int],
    gpu_hosts: set[str],
) -> dict[str, Any]:
    """受け手ごとの集合 acc から E_req・E_deleg・E_any_total・E_pair_total・E_max_gpu・2 受け手以上の文書の数・受信箱ごとの分布を出す．"""
    d_req = acc.get(REQUESTER, set())
    d_gpu = {g: v for g, v in acc.items() if g != REQUESTER}
    deleg: set[str] = set().union(*d_gpu.values()) if d_gpu else set()
    any_total = d_req | deleg
    holders: dict[str, int] = {}
    for docs in acc.values():
        for d in docs:
            holders[d] = holders.get(d, 0) + 1
    pair = len(d_req) + sum(len(v) for v in d_gpu.values())
    max_gpu = max((len(v) for v in d_gpu.values()), default=0)
    return {
        "total_docs": total,
        "n_gpu_recipients": len(d_gpu),
        "docs_req": len(d_req),
        "docs_deleg": len(deleg),
        "docs_any_total": len(any_total),
        "docs_any_total_minus_req": len(any_total) - len(d_req),
        "docs_pair_total": pair,
        "docs_max_gpu": max_gpu,
        "docs_deleg_not_in_req": len(deleg - d_req),
        "docs_with_2plus_recipients": sum(1 for n in holders.values() if n >= 2),
        "E_req": len(d_req) / total,
        "E_deleg": len(deleg) / total,
        "E_any_total": len(any_total) / total,
        "E_pair_total": pair / total,
        "E_max_gpu": max_gpu / total,
        "docs_per_gpu": {g: len(v) for g, v in sorted(d_gpu.items())},
        "per_inbox_exposure": per_inbox_distribution(
            any_total, inboxes, n_docs_by_inbox, gpu_hosts
        ),
    }


def delegation_gate(
    rows: Sequence[Mapping[str, Any]], delegate_of: Mapping[str, str]
) -> dict[str, Any]:
    """G-D（委託の記録の整合）と G-S（1 問ごとに D_g ⊆ D_req）．error の行は飛ばす．"""
    n_deleg = n_bad_target = n_bad_non_cpu = n_missing_source = 0
    n_q_bad_count = n_q_not_subset = n_q = 0
    for row in rows:
        if row.get("error") is not None:
            continue
        n_q += 1
        contexts = retrieval_signature(row)
        sent_total = 0
        by_gpu: DocSets = {}
        for na in row.get("node_answers") or []:
            gpu = na.get("delegated_to")
            if not gpu:
                continue
            n_deleg += 1
            n_bad_target += gpu != delegate_of.get(na["node_id"])
            n_bad_non_cpu += na["node_id"] not in delegate_of
            if na.get("source") not in contexts:
                n_missing_source += 1
                continue
            sent_total += len(contexts[na["source"]])
            by_gpu.setdefault(gpu, set()).update(contexts[na["source"]])
        n_q_bad_count += sum((row.get("docs_exposed_by_source") or {}).values()) != sent_total
        d_req = candidate_doc_ids(row)
        n_q_not_subset += any(not v <= d_req for v in by_gpu.values())
    rates = {
        "delegated_to_mismatch": n_bad_target / n_deleg if n_deleg else 0.0,
        "delegated_by_non_cpu": n_bad_non_cpu / n_deleg if n_deleg else 0.0,
        "missing_source": n_missing_source / n_deleg if n_deleg else 0.0,
        "docs_exposed_sum_mismatch": n_q_bad_count / n_q if n_q else 0.0,
    }
    return {
        "n_questions": n_q,
        "n_delegated_candidates": n_deleg,
        "n_questions_docs_exposed_sum_mismatch": n_q_bad_count,
        "rates": rates,
        "G_D_passes": max(rates.values()) <= G_D_MAX_RATE,
        "G_S_n_questions_not_subset": n_q_not_subset,
        "G_S_holds": n_q_not_subset == 0,
    }


def load_run(name: str) -> dict[str, Any]:
    """results/<name> の行（error 行を含む．最後の行を採る）・placement・分母・受信箱ごとの文書数を読む．"""
    run_dir = RESULTS / name
    placement = json.loads((run_dir / "placement.json").read_text(encoding="utf-8"))
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    return {
        "name": name,
        "rows": read_results(run_dir / "results.jsonl"),
        "placement": placement,
        "total": total_docs_of(run_dir),
        "n_docs_by_inbox": {s["source"]: int(s["n_docs"]) for s in meta["sources"]},
    }


def evaluate_group(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """同じ配置の実行の組（1 本または H ∪ H2）の受け手ごとの集合を和集合にして露出を出す．配置が違えば ValueError．"""
    first = runs[0]
    for r in runs[1:]:
        if r["placement"] != first["placement"] or r["total"] != first["total"]:
            raise ValueError(f"placement differs between {first['name']} and {r['name']}")
    placement = first["placement"]
    inboxes = inboxes_by_host(placement)
    gpu_hosts = set(placement["delegate_of"].values())
    acc: DocSets = {}
    excluded = sum(accumulate(r["rows"], inboxes, acc) for r in runs)
    out = exposure_metrics(acc, first["total"], inboxes, first["n_docs_by_inbox"], gpu_hosts)
    out["runs"] = [r["name"] for r in runs]
    out["n_rows"] = sum(len(r["rows"]) for r in runs)
    out["n_error_rows"] = sum(r.get("error") is not None for x in runs for r in x["rows"])
    out["n_own_inbox_docs_excluded"] = excluded
    out["gates"] = {r["name"]: delegation_gate(r["rows"], placement["delegate_of"]) for r in runs}
    # V2 の検証の LLM を 1 台の GPU の PC に委託する配備の記述：各候補の上位 5 通（= D_req）がその PC へ渡る．
    d_req = acc.get(REQUESTER, set())
    v2 = [
        len({d for d in d_req if inbox_of_doc_id(d) not in inboxes[g]}) for g in sorted(gpu_hosts)
    ]
    out["descriptive_v2_on_one_gpu"] = {
        "docs_to_that_pc_min": min(v2),
        "docs_to_that_pc_max": max(v2),
        "E_pair_total_with_v2_min": (out["docs_pair_total"] + min(v2)) / first["total"],
        "E_pair_total_with_v2_max": (out["docs_pair_total"] + max(v2)) / first["total"],
        "E_any_total_unchanged": True,
    }
    return out


def judge(result: Mapping[str, Any]) -> dict[str, Any]:
    """事前登録のゲート G-37・G-H・G-D・G-S と判定 H-A4a〜c を出す．"""
    groups = result["groups"]
    h, test = groups["H"], groups["test"]
    g37 = {"docs_req_test": test["docs_req"], "expected": TEST_E_REQ_DOCS}
    g37["passes"] = test["docs_req"] == TEST_E_REQ_DOCS
    gh = {
        "E_req_H": h["E_req"],
        "reference": H_E_REQ_A1,
        "abs_diff": abs(h["E_req"] - H_E_REQ_A1),
        "within_tolerance": abs(h["E_req"] - H_E_REQ_A1) <= G_H_TOLERANCE,
    }
    gd = {name: all(g["G_D_passes"] for g in grp["gates"].values()) for name, grp in groups.items()}
    gs = {
        name: {r: g["G_S_n_questions_not_subset"] for r, g in grp["gates"].items()}
        for name, grp in groups.items()
    }
    sides: dict[str, str] = {}
    for name in ("H", "H_p2", "H_p3"):
        if name in groups:
            diff = groups[name]["docs_any_total_minus_req"] / groups[name]["total_docs"]
            sides[name] = "no_increase" if diff <= H_A4A_MAX_DIFF else "increase"
    consistent = len(set(sides.values())) == 1
    if not g37["passes"]:
        a4a = "not_determined_G37_failed"
    elif consistent:
        a4a = (
            "委託は重複なしの露出を増やさない（37.6% の数え方は全体に等しい）"
            if sides["H"] == "no_increase"
            else "委託は重複なしの露出を増やす（37.6% は下限）"
        )
    else:
        a4a = "決まらない（H・p2・p3 で側が違う）"
    b: dict[str, Any] = {}
    for name, grp in groups.items():
        b[name] = {
            k: grp[k]
            for k in (
                "E_req",
                "E_deleg",
                "E_any_total",
                "E_pair_total",
                "E_max_gpu",
                "docs_with_2plus_recipients",
            )
        }
        b[name]["within_expected"] = {k: lo <= grp[k] <= hi for k, (lo, hi) in EXPECTED.items()}
    c = {
        name: {
            "E_any_total": grp["E_any_total"],
            "below_H_B_threshold": grp["E_any_total"] < H_B_THRESHOLD,
        }
        for name, grp in groups.items()
    }
    return {
        "gates": {"G-37": g37, "G-H": gh, "G-D": gd, "G-S_n_questions_not_subset": gs},
        "H-A4a": {"sides": sides, "consistent": consistent, "verdict": a4a},
        "H-A4b": b,
        "H-A4c": {
            "per_group": c,
            "note": "E_pair_total は H-B の対象ではない（p0007 B の数え方は受け手ごとの集合の和集合）",
        },
        "adopt": g37["passes"] and all(gd.values()),
    }


def write_report(out_dir: Path, result: Mapping[str, Any], verdict: Mapping[str, Any]) -> None:
    """analysis_report.md（数と割合だけ．文書 ID・本文は出さない）を書く．"""
    lines = [
        "# d0009 A4: 生成の委託を含めた露出の全体（Iteration 32）",
        "",
        "既存の results の再集計（オフライン）．分母は全メールの数（73,772 通）．",
        "",
        "## ゲート",
        "",
        f"- G-37: test の E_req = {verdict['gates']['G-37']['docs_req_test']} 通"
        f"（期待 {TEST_E_REQ_DOCS}）．通過 = {verdict['gates']['G-37']['passes']}",
        f"- G-H: H の E_req = {verdict['gates']['G-H']['E_req_H']:.4f}"
        f"（参照 {H_E_REQ_A1:.4f}，差 {verdict['gates']['G-H']['abs_diff']:.4f}）",
        f"- G-D（集合ごと）: {verdict['gates']['G-D']}",
        f"- G-S（D_g ⊆ D_req を満たさない問の数）: {verdict['gates']['G-S_n_questions_not_subset']}",
        "",
        "## 露出（割合は全メール比）",
        "",
        "| 集合 | 問数(行) | error 行 | E_req | E_deleg | E_any_total | E_pair_total |"
        " E_max_gpu | 2 受け手以上の文書数 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, g in result["groups"].items():
        lines.append(
            f"| {name} | {g['n_rows']} | {g['n_error_rows']} | {g['E_req']:.4f} | {g['E_deleg']:.4f}"
            f" | {g['E_any_total']:.4f} | {g['E_pair_total']:.4f} | {g['E_max_gpu']:.4f}"
            f" | {g['docs_with_2plus_recipients']} |"
        )
    lines += [
        "",
        "test は error 行の候補が無いので下限．",
        "",
        "## 判定",
        "",
        f"- H-A4a: {verdict['H-A4a']['verdict']}（側: {verdict['H-A4a']['sides']}）",
        "- H-A4b: 見込み（E_deleg 0.22〜0.30・E_pair_total 0.60〜0.70・E_max_gpu 0.02〜0.04）との照合は"
        " verdict.json の `H-A4b.*.within_expected`（採否に使わない）",
        "- H-A4c: 全メールの 50% 未満か:"
        f" { {k: v['below_H_B_threshold'] for k, v in verdict['H-A4c']['per_group'].items()} }"
        "．E_pair_total は H-B の対象ではない",
        f"- 採用（G-37・G-D 通過）: {verdict['adopt']}",
        "",
    ]
    (out_dir / "analysis_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """引数の実行を読み，exposure_total.json・verdict.json・analysis_report.md を出力先へ書く．"""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--h", required=True)
    ap.add_argument("--h-reruns", nargs="*", default=[])
    ap.add_argument("--h2", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    h = load_run(args.h)
    h2 = load_run(args.h2)
    reruns = [load_run(n) for n in args.h_reruns]
    groups: dict[str, Any] = {"H": evaluate_group([h])}
    for r in reruns:
        groups[f"H_{r['name'].rsplit('_', 1)[-1]}"] = evaluate_group([r])
    groups["H2"] = evaluate_group([h2])
    groups["Q"] = evaluate_group([h, h2])
    groups["test"] = evaluate_group([load_run(args.test)])
    result = {"groups": groups}
    verdict = judge(result)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "exposure_total.json").write_text(
        json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    write_verdict(args.out, verdict)
    write_report(args.out, result, verdict)


if __name__ == "__main__":
    main()
