"""谷歌学术的 SerpApi。只认结构化结果，失败时让调用方决定要不要改用代理。"""

from __future__ import annotations

import os
import re

import httpx

_URL = "https://serpapi.com/search.json"


def serpapi_key() -> str:
    return (os.environ.get("FENGSUN_SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY") or "").strip()


def serpapi_get(params: dict, *, transport=None) -> tuple[dict | None, str, bool]:
    """返回 (数据, 说明, 是否可以改用代理)。说明里不带密钥。"""
    key = serpapi_key()
    if not key:
        return None, "", True
    query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    query["api_key"] = key
    query.setdefault("hl", "en")
    try:
        with httpx.Client(timeout=8.0, transport=transport) as client:
            resp = client.get(_URL, params=query)
    except Exception:
        return None, "谷歌学术接口这次没连上。", True
    if resp.status_code in {401, 403}:
        return None, "谷歌学术接口密钥无效。", True
    if resp.status_code == 429:
        return None, "谷歌学术接口这次额度不够。", True
    if resp.status_code >= 400:
        return None, f"谷歌学术接口返回 {resp.status_code}。", True
    try:
        data = resp.json()
    except Exception:
        return None, "谷歌学术接口没有返回结果。", True
    if not isinstance(data, dict):
        return None, "谷歌学术接口没有返回结果。", True
    if data.get("error"):
        return None, "谷歌学术接口没有结果。", True
    return data, "", False


def _year(text: str) -> int | None:
    matched = re.search(r"(19|20)\d{2}", text or "")
    return int(matched.group(0)) if matched else None


def _count(value) -> int:
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else 0


def parse_serpapi_search(data: dict) -> list[dict]:
    out = []
    for item in data.get("organic_results") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        info = item.get("publication_info") if isinstance(item.get("publication_info"), dict) else {}
        summary = str(info.get("summary") or "")
        inline = item.get("inline_links") if isinstance(item.get("inline_links"), dict) else {}
        cited = inline.get("cited_by") if isinstance(inline.get("cited_by"), dict) else {}
        versions = inline.get("versions") if isinstance(inline.get("versions"), dict) else {}
        pdf = ""
        for res in item.get("resources") or []:
            if isinstance(res, dict) and str(res.get("file_format") or "").upper() == "PDF" and res.get("link"):
                pdf = str(res["link"])
                break
        out.append(
            {
                "title": title[:500],
                "url": str(item.get("link") or "")[:1000],
                "authors_line": summary[:300],
                "year": _year(summary),
                "snippet": str(item.get("snippet") or "")[:500],
                "citation_count": _count(cited.get("total")),
                "cites_id": re.sub(r"\D", "", str(cited.get("cites_id") or "")),
                "cluster_id": re.sub(r"\D", "", str(versions.get("cluster_id") or "")),
                "pdf_url": pdf[:500],
                "source": "scholar",
            }
        )
    return out


def parse_serpapi_profiles(data: dict) -> list[dict]:
    rows = data.get("profiles") or []
    if isinstance(rows, dict):
        rows = [rows]
    out = []
    for item in rows:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        link = str(item.get("link") or "")
        author_id = str(item.get("author_id") or "")
        if not author_id:
            found = re.search(r"user=([^&]+)", link)
            author_id = found.group(1) if found else ""
        out.append(
            {
                "name": str(item["name"])[:200],
                "scholar_id": author_id,
                "affiliation": str(item.get("affiliations") or "")[:300],
                "url": link or (f"https://scholar.google.com/citations?user={author_id}" if author_id else ""),
                "source": "scholar",
            }
        )
    return [row for row in out if row["name"]]


def parse_serpapi_author(data: dict) -> dict:
    import time

    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    table: dict[str, dict] = {}
    cited = author.get("cited_by") if isinstance(author.get("cited_by"), dict) else {}
    for row in cited.get("table") or []:
        if not isinstance(row, dict):
            continue
        for key, val in row.items():
            if isinstance(val, dict):
                table[key] = val
    email = str(author.get("email") or "")
    found = re.search(r"@([A-Za-z0-9.\-]+)", email) or re.search(r"\bat\s+([A-Za-z0-9.\-]+\.[A-Za-z]{2,})", email, re.I)
    domain = found.group(1) if found else ""

    def _all(key: str) -> int:
        return _count((table.get(key) or {}).get("all"))

    def _since(key: str) -> int:
        cell = table.get(key) or {}
        for name, val in cell.items():
            if name != "all":
                return _count(val)
        return 0

    papers = []
    for row in data.get("articles") or []:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        cited_by = row.get("cited_by") if isinstance(row.get("cited_by"), dict) else {}
        publication = str(row.get("publication") or "")
        papers.append(
            {
                "title": str(row["title"])[:500],
                "url": str(row.get("link") or "")[:1000],
                "citation_count": _count(cited_by.get("value")),
                "year": _year(publication),
                "source": "scholar",
            }
        )
    interests = []
    for item in author.get("interests") or []:
        if isinstance(item, dict) and item.get("title"):
            interests.append(str(item["title"]))
        elif isinstance(item, str) and item.strip():
            interests.append(item.strip())
    return {
        "name": str(author.get("name") or ""),
        "affiliation": str(author.get("affiliations") or "")[:300],
        "email_domain": domain,
        "citations": _all("citations"),
        "citations_5y": _since("citations"),
        "h_index": _all("h_index"),
        "h_index_5y": _since("h_index"),
        "i10": _all("i10_index"),
        "i10_5y": _since("i10_index"),
        "interests": interests[:8],
        "papers": papers[:20],
        "source": "scholar",
        "as_of": time.strftime("%Y-%m-%d", time.gmtime()),
    }
