"""平台检索与引擎工具共用的学术源适配。禁止再复制一套爬虫。"""

from __future__ import annotations

import importlib
from typing import Any

from .paper_dedupe import dedupe_paper_dicts, paper_identity_key

SOURCE_SPECS: list[tuple[str, str, str, str]] = [
    ("arxiv", "openlens_ai.tools.paper_search_mcp.academic_platforms.arxiv", "ArxivSearcher", "arXiv"),
    ("pubmed", "openlens_ai.tools.paper_search_mcp.academic_platforms.pubmed", "PubMedSearcher", "PubMed"),
    ("semantic", "openlens_ai.tools.paper_search_mcp.academic_platforms.semantic", "SemanticSearcher", "Semantic Scholar"),
    ("medrxiv", "openlens_ai.tools.paper_search_mcp.academic_platforms.medrxiv", "MedRxivSearcher", "medRxiv"),
    ("biorxiv", "openlens_ai.tools.paper_search_mcp.academic_platforms.biorxiv", "BioRxivSearcher", "bioRxiv"),
    ("iacr", "openlens_ai.tools.paper_search_mcp.academic_platforms.iacr", "IACRSearcher", "IACR"),
    ("scholar", "openlens_ai.tools.paper_search_mcp.academic_platforms.google_scholar", "GoogleScholarSearcher", "Google Scholar"),
    ("openalex", "openlens_ai.tools.paper_search_mcp.academic_platforms.openalex", "OpenAlexSearcher", "OpenAlex"),
]


def _from_paper(p: Any, source: str, venue: str | None = None) -> dict[str, Any]:
    year = None
    published = getattr(p, "published_date", None)
    if published is not None and hasattr(published, "year"):
        year = published.year
    paper_id = getattr(p, "paper_id", None)
    extra = getattr(p, "extra", None) or {}
    journal = ""
    if isinstance(extra, dict):
        journal = str(extra.get("venue") or extra.get("journal") or "").strip()
    journal = journal or str(getattr(p, "journal", None) or "").strip()
    try:
        cites = int(getattr(p, "citations", 0) or 0)
    except (TypeError, ValueError):
        cites = 0
    details = extra.get("author_details") if isinstance(extra, dict) else None
    return {
        "author_details": [d for d in (details or []) if isinstance(d, dict) and d.get("name")],
        "openalex_id": (extra.get("openalex_id") if isinstance(extra, dict) else None) or None,
        "title": getattr(p, "title", "") or "",
        "authors": getattr(p, "authors", None) or [],
        "abstract": getattr(p, "abstract", "") or "",
        "doi": getattr(p, "doi", None) or None,
        "url": getattr(p, "url", None),
        "pdf_hint": getattr(p, "pdf_url", None),
        "source": source,
        "year": year,
        "venue": venue or getattr(p, "journal", None),
        "journal": journal,
        "citation_count": max(0, cites),
        "paper_id": paper_id,
        "arxiv_id": paper_id if source == "arxiv" else None,
        "pubmed_id": paper_id if source == "pubmed" else None,
    }


def search_academic_sources(
    query: str,
    sources: list[str],
    max_per_source: int = 8,
    *,
    failures=None,
) -> list[dict[str, Any]]:
    """同一套 MCP Searcher。引擎工具与平台 search_sources 都走这里。

    D-HONEST：某个源抛错时把源名记进 failures，不要让空结果看起来像「确实没有」。
    """
    results: list[dict[str, Any]] = []
    if sources is None:
        wanted = ["arxiv"]
    else:
        wanted = list(sources)
    if not wanted:
        return []
    for key, mod_name, cls_name, venue in SOURCE_SPECS:
        if key not in wanted:
            continue
        try:
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name)
            papers = cls().search(query, max_results=max_per_source)
        except Exception as exc:
            _note(failures, venue, f"{type(exc).__name__}")
            continue
        results.extend(_from_paper(p, key, venue) for p in papers or [])
    return dedupe_search_hits(results)


def _note(failures, source: str, reason: str) -> None:
    if failures is None:
        return
    note = getattr(failures, "note", None)
    if callable(note):
        note(source, reason)


def dedupe_search_hits(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return dedupe_paper_dicts(rows)


def hit_already_key(row: dict[str, Any]) -> str:
    return paper_identity_key(row)
