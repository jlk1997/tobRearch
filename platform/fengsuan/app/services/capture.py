from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, unquote, urlparse


def _meta(html: str, name: str) -> str:
    pat = (
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']'
        rf'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']{re.escape(name)}["\']'
    )
    m = re.search(pat, html or "", re.I)
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def _authors(html: str) -> list[str]:
    found = re.findall(
        r'<meta[^>]+name=["\']citation_author["\'][^>]+content=["\']([^"\']+)["\']'
        r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_author["\']',
        html or "",
        re.I,
    )
    out: list[str] = []
    for a, b in found:
        name = (a or b or "").strip()
        if name:
            out.append(name)
    return out


def adapter_from_url(page_url: str) -> str:
    host = (urlparse(page_url or "").hostname or "").lower()
    if "arxiv" in host:
        return "arxiv"
    if "semanticscholar" in host:
        return "s2"
    if "nih.gov" in host or "pubmed" in host:
        return "pubmed"
    if "ieee" in host:
        return "ieee"
    if "cnki" in host or "wanfang" in host:
        return "cnki"
    return "generic"


def extract_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    m = re.search(r"10\.\d{4,}/[^\s<>\"']+", raw.strip())
    if not m:
        return None
    return m.group(0).rstrip(".,;:)")


def ids_from_url(page_url: str) -> dict:
    url = page_url or ""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query or "")
    out: dict[str, str] = {}
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]+\.[0-9]+)(?:v\d+)?", url, re.I)
    if m:
        out["arxiv_id"] = m.group(1)
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url, re.I)
    if m:
        out["pubmed_id"] = m.group(1)
    fn = (qs.get("filename") or qs.get("FileName") or [None])[0]
    if fn:
        out["cnki_id"] = unquote(str(fn)).strip()
    m = re.search(r"/document/(\d+)", url)
    if m:
        out["ieee_id"] = m.group(1)
    return out


def _jsonld_title_authors(html: str) -> tuple[str, list[str]]:
    title = ""
    authors: list[str] = []
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        re.I | re.S,
    ):
        try:
            data = json.loads(m.group(1) or "")
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            data = next((x for x in data if isinstance(x, dict)), {})
        if not isinstance(data, dict):
            continue
        title = title or (data.get("name") or data.get("headline") or "")
        raw = data.get("author")
        if isinstance(raw, str) and raw.strip():
            authors.append(raw.strip())
        elif isinstance(raw, dict) and raw.get("name"):
            authors.append(str(raw["name"]))
        elif isinstance(raw, list):
            for a in raw:
                if isinstance(a, str) and a.strip():
                    authors.append(a.strip())
                elif isinstance(a, dict) and a.get("name"):
                    authors.append(str(a["name"]))
        if title:
            break
    return str(title or "").strip(), authors


def _clean_title(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"\s*[-_|]\s*(中国知网|知网空间|万方数据).*$", "", t)
    t = re.sub(r"\s*[\|-]\s*IEEE Xplore.*$", "", t, flags=re.I)
    t = re.sub(r"\s*\|\s*arXiv.*$", "", t, flags=re.I)
    t = re.sub(r"\s*\[PDF\]\s*$", "", t, flags=re.I)
    return t.strip()


def parse_paper_html(html: str, page_url: str = "") -> dict:
    """Parse Highwire / citation_* / JSON-LD / 页面标题。知网/IEEE 无完整 meta 时降级，不假装已得全文。"""
    ld_title, ld_authors = _jsonld_title_authors(html)
    raw_title = (
        _meta(html, "citation_title")
        or _meta(html, "dc.title")
        or _meta(html, "og:title")
        or ld_title
    )
    if not raw_title:
        m = re.search(r"<title>([^<]+)</title>", html or "", re.I)
        raw_title = (m.group(1) if m else "") or ""
    title = _clean_title(raw_title)
    year_raw = _meta(html, "citation_publication_date") or _meta(html, "citation_date") or _meta(html, "dc.date")
    year = None
    if year_raw:
        m = re.search(r"(19|20)\d{2}", year_raw)
        if m:
            year = int(m.group(0))
    doi = extract_doi(_meta(html, "citation_doi") or _meta(html, "dc.identifier") or "")
    adapter = adapter_from_url(page_url)
    ids = ids_from_url(page_url)
    authors = _authors(html) or ld_authors
    return {
        "title": title,
        "authors": authors,
        "doi": doi,
        "abstract": _meta(html, "citation_abstract") or _meta(html, "description") or "",
        "venue": _meta(html, "citation_journal_title") or _meta(html, "citation_conference_title") or "",
        "publish_year": year,
        "site_adapter": adapter,
        "pdf_hint": _meta(html, "citation_pdf_url") or None,
        "page_url": page_url,
        "arxiv_id": ids.get("arxiv_id"),
        "pubmed_id": ids.get("pubmed_id"),
        "cnki_id": ids.get("cnki_id"),
        "ieee_id": ids.get("ieee_id"),
    }
