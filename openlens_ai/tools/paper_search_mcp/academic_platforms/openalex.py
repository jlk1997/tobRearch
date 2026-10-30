"""OpenAlex 公开接口。用来补作者机构、引用数，不需要密钥。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import httpx

from ..paper import Paper


class OpenAlexSearcher:
    def search(self, query: str, max_results: int = 8, **kwargs) -> List[Paper]:
        q = (query or "").strip()
        if not q:
            return []
        try:
            resp = httpx.get(
                "https://api.openalex.org/works",
                params={"search": q, "per-page": max(1, min(int(max_results or 8), 25))},
                headers={"User-Agent": "fengsuan-research/1.0 (mailto:local@localhost)"},
                timeout=12,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return []
        out: List[Paper] = []
        for row in (payload.get("results") or [])[: max_results]:
            paper = _to_paper(row)
            if paper is not None:
                out.append(paper)
        return out


def _to_paper(row: dict) -> Optional[Paper]:
    title = str(row.get("display_name") or "").strip()
    if not title:
        return None
    authors = []
    author_details = []
    institution = ""
    for ship in row.get("authorships") or []:
        author = (ship or {}).get("author") or {}
        name = str(author.get("display_name") or "").strip()
        insts = (ship or {}).get("institutions") or []
        affiliation = str((insts[0] or {}).get("display_name") or "").strip() if insts else ""
        if name:
            authors.append(name)
            author_details.append(
                {
                    "name": name,
                    "openalex": str(author.get("id") or "").rsplit("/", 1)[-1],
                    "orcid": str(author.get("orcid") or "").rsplit("/", 1)[-1],
                    "affiliation": affiliation,
                }
            )
        if not institution and affiliation:
            institution = affiliation
    year = row.get("publication_year")
    try:
        published = datetime(int(year), 1, 1) if year else datetime(1970, 1, 1)
    except (TypeError, ValueError):
        published = datetime(1970, 1, 1)
    doi = str(row.get("doi") or "")
    if doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/") :]
    ids = row.get("ids") or {}
    openalex_id = str(row.get("id") or ids.get("openalex") or "")
    host = ((row.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
    return Paper(
        paper_id=openalex_id or doi or title[:80],
        title=title,
        authors=authors,
        abstract=str(row.get("abstract") or ""),
        doi=doi,
        published_date=published,
        pdf_url=str(((row.get("open_access") or {}).get("oa_url")) or ""),
        url=str((row.get("primary_location") or {}).get("landing_page_url") or row.get("id") or ""),
        source="openalex",
        citations=int(row.get("cited_by_count") or 0),
        extra={
            "institution": institution,
            "venue": str(host or ""),
            "year": year,
            "author_details": author_details,
            "openalex_id": openalex_id.rsplit("/", 1)[-1],
        },
    )
