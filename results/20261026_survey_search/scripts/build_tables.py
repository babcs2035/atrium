# Build screening.csv, coding.csv and stats.json from dedup.jsonl and the manual decision/coding files.
"""Turn manual screening and coding decisions into reproducible tables and counts.

Usage:
    uv run --no-project python -I build_tables.py OUT_DIR

Inputs (OUT_DIR): hits.jsonl, dedup.jsonl, scripts/screening_decisions.txt, scripts/coding_input.tsv
Outputs (OUT_DIR): screening.csv, coding.csv, stats.json
"""

import collections
import csv
import json
import sys
from pathlib import Path

SCREEN_REASONS: dict[str, str] = {
    "INC": "include: retrieval/generation spread over multiple parties or devices without pooling the corpus, or attack/leakage in such systems",
    "CEN": "exclude: single-party or pooled-corpus RAG (incl. single device, single-operator sharding/multi-source within one system)",
    "CEN2": "exclude: one corpus, two-party user-cloud privacy; data not partitioned among holders",
    "OFF": "exclude: off-topic (not RAG, or RAG not about distributed knowledge)",
    "APP": "exclude: application case report / system demo without a distributed-RAG method",
    "SURV": "exclude from primary count: survey, review, tutorial or book",
    "FLO": "exclude: federated learning without distributed retrieval at inference",
    "FS": "exclude: federated-search source selection without generation (federated-search lineage, cited by origin)",
    "NP": "exclude: not a research paper (index page, front matter, book page)",
    "DUP": "exclude: another version of an included record (counted once)",
}

# Decisions that needed more than the listed abstract; kept here so the reason is auditable.
SCREEN_NOTES: dict[str, str] = {
    "P0004": "local-first single-device IR (tvly snippet)",
    "P0055": "abstract from OpenReview/Tavily snippet: multi-provider decentralized retrieval",
    "P0072": "abstract from S2 title match",
    "P0075": "abstract from arXiv API",
    "P0168": "simulated federated hospitals with centralized baseline",
    "P0224": "marginal: decentralized RAG-based agents across organizations",
    "P0237": "marginal: provider-owned documents in RAG pipeline",
    "P0250": "marginal: edge-local RAG with cloud escalation (edge-cloud rule)",
    "P0262": "edge nodes each run RAG; corpus not partitioned among parties",
    "P0285": "no abstract; primary topic is causal inference by agent simulation",
    "P0304": "title only",
    "P0390": "title only",
    "P0527": "title only; abstract not retrievable",
    "P0531": "title + one-line snippet",
    "P0555": "title only",
    "P0637": "network-coded sharding of one operator's knowledge; compared with sharded/centralized RAG",
    "P0697": "multi-tenant isolation inside one SaaS; no cross-tenant retrieval",
}

# Tavily-only records have no API year; filled from the sources noted.
YEAR_OVERRIDE: dict[str, int | None] = {"P0072": 2024, "P0075": 2026, "P0055": None}

CODE_LABELS: dict[str, dict[str, str]] = {
    "partition": {
        "TOPIC": "by topic/domain/source collection",
        "SPLIT": "artificial split of public dataset",
        "OWNER": "by owner (person/organization)",
        "TIER": "device-private vs cloud-public tiers",
        "NS": "not stated in screened text",
        "NA": "not applicable",
    },
    "eval": {
        "SIM": "simulation / emulation / single machine",
        "PHYS": "multiple physical devices",
        "NONE": "no empirical evaluation",
        "NS": "not stated in screened text",
    },
    "central": {
        "NONE": "no comparison to centralized",
        "WINRATE": "relative LLM win rate",
        "ACC": "correctness-based accuracy (EM/F1/accuracy)",
        "OTHER": "other (retrieval metrics, overlap, unspecified 'performance', cost)",
        "NS": "not stated in screened text",
    },
}


def main() -> None:
    """Read inputs, write screening.csv / coding.csv / stats.json."""
    out = Path(sys.argv[1])
    hits = [json.loads(line) for line in open(out / "hits.jsonl")]
    dedup = {r["id"]: r for r in map(json.loads, open(out / "dedup.jsonl"))}

    decisions: dict[str, str] = {}
    for line in open(out / "scripts" / "screening_decisions.txt"):
        if line.startswith("#") or not line.strip():
            continue
        rid, code = line.split()
        decisions[rid] = code
    missing = set(dedup) - set(decisions)
    if missing:
        raise SystemExit(f"undecided records: {sorted(missing)[:10]}")

    def year_of(rid: str) -> int | None:
        return YEAR_OVERRIDE.get(rid, dedup[rid]["year"])

    with open(out / "screening.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "year", "include", "reason_code", "reason", "note", "sources", "arxiv_id", "doi", "url"])
        for rid, rec in dedup.items():
            code = decisions[rid]
            base = code.split(":")[0]
            reason = SCREEN_REASONS[base] + (f" ({code.split(':')[1]})" if ":" in code else "")
            w.writerow([
                rid, rec["title"], year_of(rid) or "", "y" if base == "INC" else "n", base, reason,
                SCREEN_NOTES.get(rid, ""), ";".join(rec["sources"]), rec["arxiv_id"] or "", rec["doi"] or "",
                rec["urls"][0] if rec["urls"] else "",
            ])

    coding_rows = []
    with open(out / "scripts" / "coding_input.tsv") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    for row in csv.DictReader(lines, delimiter="\t"):
        rec = dedup[row["id"]]
        coding_rows.append({
            "id": row["id"], "title": rec["title"], "year": year_of(row["id"]) or "",
            "venue": rec["venue_s2"], "arxiv_id": rec["arxiv_id"] or "", "doi": rec["doi"] or "",
            "data_partition": row["partition"], "evaluation_form": row["eval"],
            "comparison_to_centralized": row["central"], "centralized_has_same_retrieval_improvements": row["same_improv"],
            "exposure_measured": row["exposure"], "latency_measured": row["latency"],
            "compute_or_energy_measured": row["compute_energy"], "coding_basis": row["basis"], "note": row["note"],
        })
    included = {rid for rid, c in decisions.items() if c == "INC"}
    coded = {r["id"] for r in coding_rows}
    if included != coded:
        raise SystemExit(f"coding mismatch: missing={sorted(included - coded)} extra={sorted(coded - included)}")
    with open(out / "coding.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(coding_rows[0].keys()))
        w.writeheader()
        w.writerows(coding_rows)

    # Counts for summary.md.
    per_source = collections.Counter(h["source"] for h in hits)
    per_source_query = collections.Counter((h["source"], h["query_id"]) for h in hits)
    dedup_by_source = collections.Counter(s for r in dedup.values() for s in r["sources"])
    unique_to_tavily = sum(1 for r in dedup.values() if all(s.startswith("tavily") for s in r["sources"]))
    included_unique_tavily = sum(1 for rid in included if all(s.startswith("tavily") for s in dedup[rid]["sources"]))
    stats = {
        "hits_total": len(hits),
        "hits_per_source": dict(per_source),
        "hits_per_source_query": {f"{s}|{q}": n for (s, q), n in sorted(per_source_query.items())},
        "dedup_total": len(dedup),
        "dedup_records_containing_source": dict(dedup_by_source),
        "dedup_only_tavily": unique_to_tavily,
        "screen_codes": dict(collections.Counter(c.split(":")[0] for c in decisions.values())),
        "included": len(included),
        "included_only_tavily": included_unique_tavily,
        "included_per_year": dict(sorted(collections.Counter(str(year_of(r) or "unknown") for r in included).items())),
        "coding": {
            col: dict(collections.Counter(r[col] for r in coding_rows))
            for col in ["data_partition", "evaluation_form", "comparison_to_centralized",
                        "centralized_has_same_retrieval_improvements", "exposure_measured",
                        "latency_measured", "compute_or_energy_measured", "coding_basis"]
        },
        "code_labels": CODE_LABELS,
    }
    # Joint condition used for the counterexample claim.
    stats["joint"] = {
        "owner_partition": sorted(r["id"] for r in coding_rows if r["data_partition"] == "OWNER"),
        "physical_multi_device": sorted(r["id"] for r in coding_rows if r["evaluation_form"] == "PHYS"),
        "accuracy_vs_centralized": sorted(r["id"] for r in coding_rows if r["comparison_to_centralized"] == "ACC"),
        "same_improvements_yes": sorted(r["id"] for r in coding_rows if r["centralized_has_same_retrieval_improvements"] == "Y"),
        "owner_and_physical": sorted(r["id"] for r in coding_rows if r["data_partition"] == "OWNER" and r["evaluation_form"] == "PHYS"),
        "physical_and_acc": sorted(r["id"] for r in coding_rows if r["evaluation_form"] == "PHYS" and r["comparison_to_centralized"] == "ACC"),
        "owner_and_acc": sorted(r["id"] for r in coding_rows if r["data_partition"] == "OWNER" and r["comparison_to_centralized"] == "ACC"),
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    print(json.dumps({k: stats[k] for k in ["hits_total", "hits_per_source", "dedup_total", "screen_codes", "included", "included_per_year", "joint"]}, ensure_ascii=False, indent=1))
    for col, cnt in stats["coding"].items():
        print(col, cnt)


if __name__ == "__main__":
    main()
