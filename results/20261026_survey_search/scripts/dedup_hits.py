# Merge hits_{arxiv,s2,tavily}.jsonl into hits.jsonl and deduplicate into dedup.jsonl by arXiv ID / DOI / normalized title.
"""Deduplicate literature-search hits.

Usage:
    uv run --no-project python -I dedup_hits.py OUT_DIR

Records are linked (union-find) when they share an arXiv ID, a DOI (case-insensitive,
ACL Anthology URLs mapped to their 10.18653 DOI), or a normalized title. Tavily titles
truncated with "..." are linked to the unique longer title that starts with them.
Each dedup record keeps the union of sources / query ids and the longest abstract.
"""

import json
import re
import sys
from pathlib import Path

# Truncated-title prefix matching below this length is too ambiguous to trust.
MIN_PREFIX_LEN = 30
ACL_URL_RE = re.compile(r"aclanthology\.org/(\d{4}\.[a-z0-9-]+\.\d+)")
ACM_DOI_RE = re.compile(r"dl\.acm\.org/doi/(?:abs/|pdf/|fullHtml/)?(10\.\d+/[0-9.]+)")
SNIPPET_YEAR_RE = re.compile(r"^by [^·]{1,60}·\s*((?:20)\d\d)\s*·")
TITLE_NOISE = [
    re.compile(r"^\[PDF\]\s*", re.I),
    re.compile(r"^\[\d{4}\.\d{4,5}(v\d+)?\]\s*"),
    re.compile(r"\s*[-|]\s*ACL Anthology$", re.I),
    re.compile(r"\s*\|\s*Proceedings of.*$", re.I),
    re.compile(r"\s*\|\s*IEEE.*$", re.I),
    re.compile(r"\s*\|\s*OpenReview$", re.I),
    re.compile(r"\s*-\s*arXiv$", re.I),
]


def clean_title(title: str) -> tuple[str, bool]:
    """Strip site decorations; return (title, truncated)."""
    t = title.strip()
    for pat in TITLE_NOISE:
        t = pat.sub("", t)
    truncated = t.endswith("...") or t.endswith("…")
    t = t.rstrip(".… ")
    return t, truncated


def norm_title(title: str) -> str:
    """Lowercase alphanumeric-only key for title matching."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def main() -> None:
    """Merge per-source hit files, link duplicates and write hits.jsonl / dedup.jsonl."""
    out = Path(sys.argv[1])
    hits: list[dict] = []
    for src in ("arxiv", "s2", "tavily"):
        hits += [json.loads(line) for line in open(out / f"hits_{src}.jsonl")]
    for h in hits:
        h["title"], h["title_truncated"] = clean_title(h["title"])
        if not h.get("doi"):
            m = ACL_URL_RE.search(h.get("url") or "")
            if m:
                h["doi"] = f"10.18653/v1/{m.group(1)}"
            m = ACM_DOI_RE.search(h.get("url") or "")
            if m:
                h["doi"] = m.group(1)
        if h.get("doi"):
            h["doi"] = h["doi"].lower()
        if h.get("arxiv_id"):
            h["arxiv_id"] = re.sub(r"v\d+$", "", h["arxiv_id"])
        # Tavily snippets from Google-Scholar-like pages start with "by A Author · 2025 ·".
        if h.get("year") is None:
            m = SNIPPET_YEAR_RE.match(h.get("abstract") or "")
            if m:
                h["year"] = int(m.group(1))
    with open(out / "hits.jsonl", "w") as f:
        for h in hits:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")

    parent = list(range(len(hits)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    first_by_key: dict[str, int] = {}
    for i, h in enumerate(hits):
        keys = []
        if h.get("arxiv_id"):
            keys.append("ax:" + h["arxiv_id"])
            # arXiv-issued DOIs (10.48550/arxiv.X) are the same work as the arXiv ID.
            keys.append("doi:10.48550/arxiv." + h["arxiv_id"].lower())
        if h.get("doi"):
            keys.append("doi:" + h["doi"])
        nt = norm_title(h["title"])
        if nt and not h["title_truncated"]:
            keys.append("t:" + nt)
        for k in keys:
            if k in first_by_key:
                union(i, first_by_key[k])
            else:
                first_by_key[k] = i

    # Link truncated titles to the unique group whose (full or truncated) title they prefix.
    all_titles = [(norm_title(h["title"]), i) for i, h in enumerate(hits)]
    for i, h in enumerate(hits):
        if not h["title_truncated"]:
            continue
        nt = norm_title(h["title"])
        if len(nt) < MIN_PREFIX_LEN:
            continue
        matched = [(t, j) for t, j in all_titles if j != i and t.startswith(nt)]
        # Ambiguous when the prefix matches two different complete titles.
        full_norms = {t for t, j in matched if not hits[j]["title_truncated"]}
        if len(full_norms) <= 1:
            for _, j in matched:
                union(i, j)

    groups: dict[int, list[dict]] = {}
    for i, h in enumerate(hits):
        groups.setdefault(find(i), []).append(h)

    records = []
    for members in groups.values():
        # Prefer an API record (full metadata) as the representative.
        rep = sorted(members, key=lambda m: (m["source"].startswith("tavily"), m["title_truncated"]))[0]
        years = [m["year"] for m in members if m.get("year")]
        venues = sorted({m["venue"] for m in members if m.get("venue") and m["source"] == "s2"})
        records.append(
            {
                "title": rep["title"],
                "year": min(years) if years else None,
                "venue_s2": "; ".join(venues),
                "arxiv_id": next((m["arxiv_id"] for m in members if m.get("arxiv_id")), None),
                "doi": next((m["doi"] for m in members if m.get("doi") and "48550" not in m["doi"]), None),
                "s2_id": next((m["s2_id"] for m in members if m.get("s2_id")), None),
                "urls": sorted({m["url"] for m in members if m.get("url")}),
                "sources": sorted({m["source"] for m in members}),
                "query_ids": sorted({m["query_id"] for m in members}),
                "abstract": max((m.get("abstract") or "" for m in members), key=len),
                "abstract_from_api": any(m["source"] in ("arxiv", "s2") and m.get("abstract") for m in members),
            }
        )
    records.sort(key=lambda r: (r["year"] or 0, r["title"].lower()))
    for n, r in enumerate(records, 1):
        r["id"] = f"P{n:04d}"
    with open(out / "dedup.jsonl", "w") as f:
        for r in records:
            f.write(json.dumps({"id": r.pop("id"), **r}, ensure_ascii=False) + "\n")
    print(f"hits={len(hits)} dedup={len(records)}", file=sys.stderr)


if __name__ == "__main__":
    main()
