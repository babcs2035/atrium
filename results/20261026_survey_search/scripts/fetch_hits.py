# Fetch raw literature-search hits (arXiv API, Semantic Scholar bulk search, Tavily CLI) into raw/ and hits.jsonl.
"""Literature search for d0008 chapter 2 (RAG side, 2020-01-01..2026-10-26).

Usage (from any directory):
    uv run --no-project python -I fetch_hits.py OUT_DIR

Writes raw API responses under OUT_DIR/raw/{arxiv,s2,tavily}/ and one JSON line
per hit to OUT_DIR/hits.jsonl with fields: source, query_id, query, title, year,
venue, arxiv_id, doi, s2_id, url, abstract.
"""

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# arXiv asks for >= 3 s between calls; S2 without a key is rate-limited (HTTP 429).
ARXIV_SLEEP_SEC = 3.5
ARXIV_PAGE_SIZE = 200
S2_SLEEP_SEC = 3.0
S2_MAX_RETRY = 8
S2_BACKOFF_SEC = 10.0
TAVILY_MAX_RESULTS = 20
HTTP_TIMEOUT_SEC = 90

ARXIV_DATE = "submittedDate:[202001010000 TO 202610262359]"
ARXIV_RAG = '(all:"retrieval augmented generation" OR all:RAG)'
S2_RAG = '("retrieval augmented generation" | "retrieval-augmented generation" | RAG)'

# One entry per query theme from the task (Q5 merges "edge RAG" and "edge-cloud RAG").
# nl = natural-language query used for Tavily; arxiv / s2 = Boolean translations.
QUERIES: list[dict[str, str]] = [
    {
        "id": "Q1",
        "nl": "federated retrieval-augmented generation",
        "arxiv": f"all:federated AND {ARXIV_RAG}",
        "s2": f"federated + {S2_RAG}",
    },
    {
        "id": "Q2",
        "nl": "federated RAG",
        "arxiv": 'all:"federated RAG"',
        "s2": '"federated RAG"',
    },
    {
        "id": "Q3",
        "nl": "distributed retrieval-augmented generation",
        "arxiv": (
            '(ti:distributed OR abs:"distributed retrieval" OR abs:"distributed knowledge" '
            'OR abs:"distributed data" OR abs:"distributed RAG" OR abs:"distributed across") '
            f"AND {ARXIV_RAG}"
        ),
        "s2": (
            '("distributed retrieval" | "distributed knowledge" | "distributed data" | '
            f'"distributed RAG" | "distributed retrieval-augmented") + {S2_RAG}'
        ),
    },
    {
        "id": "Q4",
        "nl": "decentralized retrieval-augmented generation",
        "arxiv": f"(all:decentralized OR all:decentralised) AND {ARXIV_RAG}",
        "s2": f"(decentralized | decentralised) + {S2_RAG}",
    },
    {
        "id": "Q5",
        "nl": "edge-cloud retrieval-augmented generation edge RAG",
        "arxiv": (
            '(all:"edge RAG" OR all:"edge cloud" OR all:edge-cloud OR all:"edge device" '
            'OR all:"edge devices" OR all:"edge computing" OR all:"edge server" '
            f'OR all:"edge servers" OR ti:edge) AND {ARXIV_RAG}'
        ),
        "s2": (
            '("edge RAG" | "edge cloud" | "edge-cloud" | "edge device" | "edge devices" | '
            f'"edge computing" | "edge server" | "edge servers") + {S2_RAG}'
        ),
    },
    {
        "id": "Q6",
        "nl": "peer-to-peer retrieval-augmented generation",
        "arxiv": f'(all:"peer to peer" OR all:P2P) AND {ARXIV_RAG}',
        "s2": f'("peer-to-peer" | "peer to peer" | P2P) + {S2_RAG}',
    },
    {
        "id": "Q7",
        "nl": "privacy-preserving retrieval-augmented generation multiple data sources",
        "arxiv": (
            'all:privacy AND (all:"multiple data sources" OR all:"multi source" '
            f'OR all:"cross silo" OR all:"data sources") AND {ARXIV_RAG}'
        ),
        "s2": (
            'privacy + ("multiple data sources" | "multi-source" | "cross-silo" | '
            f'"data sources" | "multiple parties" | "multi-party") + {S2_RAG}'
        ),
    },
    {
        "id": "Q8",
        "nl": "RAG data silos",
        "arxiv": f"(all:silo OR all:silos OR all:siloed) AND {ARXIV_RAG}",
        "s2": f'(silo | silos | siloed | "data silos") + {S2_RAG}',
    },
]

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def http_get_text(url: str) -> tuple[int, str]:
    """GET url and return (status, body); HTTP errors are returned, not raised."""
    req = urllib.request.Request(url, headers={"User-Agent": "atrium-survey/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", errors="replace")


def fetch_arxiv(query: dict[str, str], raw_dir: Path) -> list[dict]:
    """Page through the arXiv API for one query and return hit records."""
    hits: list[dict] = []
    start = 0
    total = None
    while total is None or start < total:
        params = urllib.parse.urlencode(
            {
                "search_query": f"({query['arxiv']}) AND {ARXIV_DATE}",
                "start": start,
                "max_results": ARXIV_PAGE_SIZE,
            }
        )
        status, body = http_get_text(f"https://export.arxiv.org/api/query?{params}")
        time.sleep(ARXIV_SLEEP_SEC)
        if status != 200:
            raise RuntimeError(f"arXiv HTTP {status} for {query['id']}: {body[:200]}")
        (raw_dir / f"{query['id']}_{start}.xml").write_text(body)
        root = ET.fromstring(body)
        total = int(root.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults") or 0)
        entries = root.findall(f"{ATOM}entry")
        if not entries:
            break
        for e in entries:
            abs_url = e.findtext(f"{ATOM}id") or ""
            arxiv_id = re.sub(r"v\d+$", "", abs_url.rsplit("/abs/", 1)[-1])
            hits.append(
                {
                    "source": "arxiv",
                    "query_id": query["id"],
                    "query": query["arxiv"],
                    "title": " ".join((e.findtext(f"{ATOM}title") or "").split()),
                    "year": int((e.findtext(f"{ATOM}published") or "0000")[:4]),
                    "venue": e.findtext(f"{ARXIV_NS}journal_ref") or "arXiv",
                    "arxiv_id": arxiv_id,
                    "doi": e.findtext(f"{ARXIV_NS}doi"),
                    "s2_id": None,
                    "url": abs_url,
                    "abstract": " ".join((e.findtext(f"{ATOM}summary") or "").split()),
                }
            )
        start += len(entries)
    print(f"arxiv {query['id']}: total={total} fetched={len(hits)}", file=sys.stderr)
    return hits


def fetch_s2(query: dict[str, str], raw_dir: Path) -> list[dict]:
    """Page through Semantic Scholar /paper/search/bulk for one query, backing off on 429."""
    hits: list[dict] = []
    token = None
    page = 0
    while True:
        params = {
            "query": query["s2"],
            "year": "2020-2026",
            "fields": "title,year,venue,externalIds,abstract,publicationDate",
        }
        if token:
            params["token"] = token
        url = "https://api.semanticscholar.org/graph/v1/paper/search/bulk?" + urllib.parse.urlencode(params)
        for attempt in range(S2_MAX_RETRY):
            status, body = http_get_text(url)
            if status != 429:
                break
            time.sleep(S2_BACKOFF_SEC * (attempt + 1))
        time.sleep(S2_SLEEP_SEC)
        if status != 200:
            raise RuntimeError(f"S2 HTTP {status} for {query['id']}: {body[:200]}")
        (raw_dir / f"{query['id']}_{page}.json").write_text(body)
        data = json.loads(body)
        for p in data.get("data") or []:
            ext = p.get("externalIds") or {}
            pub_date = p.get("publicationDate") or ""
            # The bulk API year filter is by year; drop items dated after the search date.
            if pub_date and pub_date > "2026-10-26":
                continue
            hits.append(
                {
                    "source": "s2",
                    "query_id": query["id"],
                    "query": query["s2"],
                    "title": " ".join((p.get("title") or "").split()),
                    "year": p.get("year"),
                    "venue": p.get("venue") or "",
                    "arxiv_id": ext.get("ArXiv"),
                    "doi": ext.get("DOI"),
                    "s2_id": p.get("paperId"),
                    "url": f"https://www.semanticscholar.org/paper/{p.get('paperId')}",
                    "abstract": " ".join((p.get("abstract") or "").split()),
                }
            )
        token = data.get("token")
        page += 1
        if not token:
            print(f"s2 {query['id']}: total={data.get('total')} fetched={len(hits)}", file=sys.stderr)
            return hits


ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})")
DOI_URL_RE = re.compile(r"(10\.\d{4,9}/[^\s?#]+)")


def fetch_tavily(query: dict[str, str], raw_dir: Path, scope: str) -> list[dict]:
    """Run one Tavily search (scope 'web' or 'venues') and return hit records."""
    cmd = [
        "tvly", "search", query["nl"], "--json", "--depth", "advanced",
        "--max-results", str(TAVILY_MAX_RESULTS), "--start-date", "2020-01-01",
        "--end-date", "2026-10-26",
    ]
    if scope == "venues":
        cmd += ["--include-domains", "aclanthology.org,dl.acm.org,ieeexplore.ieee.org"]
    else:
        cmd += ["--include-domains", "arxiv.org,aclanthology.org,dl.acm.org,ieeexplore.ieee.org,"
                "openreview.net,link.springer.com,sciencedirect.com,mdpi.com,usenix.org"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    (raw_dir / f"{query['id']}_{scope}.json").write_text(proc.stdout)
    data = json.loads(proc.stdout)
    hits: list[dict] = []
    for r in data.get("results") or []:
        url = r.get("url") or ""
        m_arxiv = ARXIV_URL_RE.search(url)
        m_doi = DOI_URL_RE.search(url) if "doi" in url or "dl.acm.org" in url else None
        domain = urllib.parse.urlparse(url).netloc
        hits.append(
            {
                "source": f"tavily_{scope}",
                "query_id": query["id"],
                "query": query["nl"] + (" [domains: acl/acm/ieee]" if scope == "venues" else " [domains: scholarly]"),
                "title": " ".join((r.get("title") or "").split()),
                "year": None,
                "venue": domain,
                "arxiv_id": m_arxiv.group(1) if m_arxiv else None,
                "doi": m_doi.group(1).rstrip("/") if m_doi else None,
                "s2_id": None,
                "url": url,
                "abstract": " ".join((r.get("content") or "").split()),
            }
        )
    print(f"tavily_{scope} {query['id']}: fetched={len(hits)}", file=sys.stderr)
    return hits


def main() -> None:
    """Run every query on every source and write hits.jsonl."""
    out_dir = Path(sys.argv[1])
    sources = sys.argv[2].split(",") if len(sys.argv) > 2 else ["arxiv", "s2", "tavily"]
    for sub in ("arxiv", "s2", "tavily"):
        (out_dir / "raw" / sub).mkdir(parents=True, exist_ok=True)
    for src in sources:
        hits: list[dict] = []
        for q in QUERIES:
            if src == "arxiv":
                hits += fetch_arxiv(q, out_dir / "raw" / "arxiv")
            elif src == "s2":
                hits += fetch_s2(q, out_dir / "raw" / "s2")
            else:
                hits += fetch_tavily(q, out_dir / "raw" / "tavily", "web")
                hits += fetch_tavily(q, out_dir / "raw" / "tavily", "venues")
        with open(out_dir / f"hits_{src}.jsonl", "w") as f:
            for h in hits:
                f.write(json.dumps(h, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
