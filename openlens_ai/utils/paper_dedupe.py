"""文献检索去重。不改工具节点名。"""

from __future__ import annotations

import re
import unicodedata


_ARXIV_RE = re.compile(r"(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_ARXIV_URL_RE = re.compile(
    r"(?:export\.)?arxiv\.org/(?:abs|pdf|html|src)/(\d{4}\.\d{4,5})(?:v\d+)?",
    re.I,
)
_DOI_RE = re.compile(r"10\.\d{1,9}/[^\s\"'<>]+", re.I)
_PMID_RE = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|pmid[:\s/])(\d{5,9})", re.I)
_PLACEHOLDER_TITLES = {
    "untitled",
    "untitled paper",
    "unknown",
    "unknown title",
    "n a",
    "na",
    "none",
    "title",
    "paper",
}

_ID_FIELDS = (
    "doi",
    "arxiv_id",
    "arxiv",
    "url",
    "pdf_url",
    "pdf_hint",
    "paper_id",
    "source_url",
    "pubmed_id",
    "pmid",
)
_ACADEMIC_HOST_RE = re.compile(
    r"(arxiv\.org|doi\.org|pubmed\.ncbi|pmc\.ncbi|biorxiv\.org|medrxiv\.org|"
    r"semanticscholar\.org|aclanthology\.org|iacr\.org|openreview\.net)",
    re.I,
)
_ACADEMIC_SOURCES = {
    "arxiv",
    "pubmed",
    "semantic",
    "medrxiv",
    "biorxiv",
    "iacr",
    "scholar",
}


def _clean_doi_token(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    text = text.split("?")[0].split("#")[0].rstrip(").,;")
    return text


def extract_doi_text(value: str) -> str:
    raw = _clean_doi_token(value)
    if not raw:
        return ""
    m = _DOI_RE.search(raw)
    if m:
        return _clean_doi_token(m.group(0)).lower()
    if raw.lower().startswith("10."):
        return raw.lower()
    return ""


def extract_arxiv_text(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    m = _ARXIV_URL_RE.search(raw) or _ARXIV_RE.search(raw)
    return m.group(1).lower() if m else ""


def extract_pmid_text(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    m = _PMID_RE.search(raw)
    if m:
        return m.group(1)
    if raw.isdigit() and 5 <= len(raw) <= 9:
        return raw
    return ""


def _id_blob(row: dict) -> str:
    parts: list[str] = []
    for key in _ID_FIELDS:
        val = row.get(key)
        if val:
            parts.append(str(val))
    return " ".join(parts)


def norm_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", str(title or "")).strip().lower()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def is_placeholder_title(title: str) -> bool:
    normalized = norm_title(title)
    return not normalized or normalized in _PLACEHOLDER_TITLES


def is_valid_bib_title(title: str) -> bool:
    raw = str(title or "").strip()
    if is_placeholder_title(raw):
        return False
    return len(raw) >= 8 and len(norm_title(raw)) >= 8


def extract_doi(row: dict) -> str:
    if not isinstance(row, dict):
        return ""
    return extract_doi_text(str(row.get("doi") or "")) or extract_doi_text(_id_blob(row))


def extract_arxiv(row: dict) -> str:
    if not isinstance(row, dict):
        return ""
    source = str(row.get("source") or "").strip().lower()
    paper_id = str(row.get("paper_id") or "").strip()
    arxiv = extract_arxiv_text(str(row.get("arxiv_id") or row.get("arxiv") or ""))
    if not arxiv and source == "arxiv" and paper_id:
        arxiv = extract_arxiv_text(paper_id)
    if not arxiv:
        arxiv = extract_arxiv_text(_id_blob(row))
    return arxiv


def extract_pmid(row: dict) -> str:
    if not isinstance(row, dict):
        return ""
    source = str(row.get("source") or "").strip().lower()
    paper_id = str(row.get("paper_id") or "").strip()
    pubmed = str(row.get("pubmed_id") or row.get("pmid") or "").strip()
    hit = extract_pmid_text(pubmed)
    if not hit and source == "pubmed" and paper_id:
        hit = extract_pmid_text(paper_id)
    if not hit:
        hit = extract_pmid_text(_id_blob(row))
    return hit.lower() if hit else ""


def paper_identity_keys(row: dict) -> list[str]:
    if not isinstance(row, dict):
        return []
    keys: list[str] = []
    doi = extract_doi(row)
    if doi:
        keys.append(f"doi:{doi}")
    arxiv = extract_arxiv(row)
    if arxiv:
        keys.append(f"arxiv:{arxiv}")
    pubmed = extract_pmid(row)
    if pubmed:
        keys.append(f"pmid:{pubmed}")
    title = norm_title(str(row.get("title") or ""))
    if title and title not in _PLACEHOLDER_TITLES and len(title) >= 4:
        keys.append(f"title:{title}")
    return keys


def paper_identity_key(row: dict) -> str:
    keys = paper_identity_keys(row)
    return keys[0] if keys else ""


def paper_is_identifiable(row: dict) -> bool:
    return bool(paper_identity_keys(row))


def has_academic_identifier(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    return bool(extract_doi(row) or extract_arxiv(row) or extract_pmid(row))


def has_academic_url(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    blob = " ".join(
        str(row.get(k) or "")
        for k in ("url", "pdf_url", "pdf_hint", "source_url")
    )
    return bool(_ACADEMIC_HOST_RE.search(blob))


def paper_usable_for_bib(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if not is_valid_bib_title(str(row.get("title") or "")):
        return False
    if has_academic_identifier(row) or has_academic_url(row):
        return True
    url = str(row.get("url") or row.get("pdf_url") or row.get("pdf_hint") or "").strip()
    if not url:
        return True
    source = str(row.get("source") or "").strip().lower()
    return source in _ACADEMIC_SOURCES


def enrich_paper_dict(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    out = dict(row)
    source = str(out.get("source") or "").strip().lower()
    paper_id = str(out.get("paper_id") or "").strip()
    arxiv = extract_arxiv(out)
    if arxiv and not out.get("arxiv_id"):
        out["arxiv_id"] = arxiv
    elif source == "arxiv" and paper_id and not out.get("arxiv_id"):
        out["arxiv_id"] = extract_arxiv_text(paper_id) or paper_id
    pmid = extract_pmid(out)
    if pmid and not out.get("pubmed_id"):
        out["pubmed_id"] = pmid
    elif source == "pubmed" and paper_id and not out.get("pubmed_id"):
        out["pubmed_id"] = paper_id
    doi = extract_doi(out)
    if doi and not out.get("doi"):
        out["doi"] = doi
    return out


def _first_author(paper: dict) -> str:
    authors = paper.get("authors") or []
    if isinstance(authors, str):
        authors = [part.strip() for part in authors.split(",") if part.strip()]
    if not isinstance(authors, list) or not authors:
        return ""
    first = authors[0]
    if isinstance(first, dict):
        return norm_title(str(first.get("name") or first.get("author") or ""))
    return norm_title(str(first))


def _year_num(paper: dict) -> int | None:
    try:
        year = int(str(paper.get("year") or paper.get("publish_year") or "")[:4])
    except (TypeError, ValueError):
        return None
    return year or None


def _is_published(paper: dict) -> bool:
    doi = str(paper.get("doi") or "").lower()
    venue = str(paper.get("venue") or "").lower()
    if paper.get("arxiv_id") and not doi:
        return False
    if "arxiv" in venue or "preprint" in venue:
        return bool(doi and "arxiv" not in doi)
    return bool(doi)


def merge_versions(papers: list) -> list:
    """题名、第一作者相同且年份相差不超过 1 的预印本和正式版并成一条。"""
    groups: list[list[dict]] = []
    for paper in papers or []:
        if not isinstance(paper, dict):
            continue
        title = norm_title(str(paper.get("title") or ""))
        author = _first_author(paper)
        year = _year_num(paper)
        placed = False
        if title and author and year is not None:
            for group in groups:
                head = group[0]
                head_year = _year_num(head)
                if norm_title(str(head.get("title") or "")) != title or _first_author(head) != author:
                    continue
                if head_year is None or abs(head_year - year) > 1:
                    continue
                group.append(paper)
                placed = True
                break
        if not placed:
            groups.append([paper])
    out: list[dict] = []
    for group in groups:
        if len(group) == 1:
            out.append(group[0])
            continue
        primary = sorted(
            group,
            key=lambda row: (1 if _is_published(row) else 0, int(row.get("citation_count") or 0)),
            reverse=True,
        )[0]
        merged = dict(primary)
        published = next((row for row in group if _is_published(row) and row.get("doi")), None)
        if published and published.get("doi"):
            merged["doi"] = published.get("doi")
        merged["versions"] = [
            {
                "title": row.get("title") or "",
                "doi": row.get("doi") or "",
                "arxiv_id": row.get("arxiv_id") or "",
                "year": row.get("year"),
                "venue": row.get("venue") or "",
                "preprint": not _is_published(row),
            }
            for row in group
        ]
        out.append(merged)
    return out


def dedupe_paper_dicts(papers: list) -> list:
    seen: set[str] = set()
    out: list = []
    for row in papers or []:
        if not isinstance(row, dict):
            continue
        keys = paper_identity_keys(row)
        if not keys or any(k in seen for k in keys):
            continue
        seen.update(keys)
        out.append(row)
    return out
