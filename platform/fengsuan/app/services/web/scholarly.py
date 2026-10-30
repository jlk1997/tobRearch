"""结构化学术库。断网或限流就空着，不编数字。"""

from __future__ import annotations

import os
from typing import Any

from fengsuan.app.services.web.common import contact_email

OPENALEX = "https://api.openalex.org"


def _json(url: str, params: dict | None = None, *, host: str) -> dict | None:
    from fengsuan.app.services.biblio import _get_json

    try:
        return _get_json(url, params, host=host)
    except ConnectionError:
        return None


def _mailto_params(extra: dict | None = None) -> dict:
    out = {"mailto": contact_email()}
    if extra:
        out.update(extra)
    return out


def _paper(row: dict, source: str) -> dict:
    authors = []
    for item in row.get("authors") or row.get("authorships") or []:
        if isinstance(item, str):
            authors.append(item)
        elif isinstance(item, dict):
            # authorships 里是 {"author": {"id": ..., "display_name": ...}}，
            # 但 author 本身可能是 null 或缺 display_name。
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            name = author.get("display_name") or item.get("name")
            if name:
                authors.append(str(name))
    year = row.get("publication_year") or row.get("year")
    first_author_id = ""
    ships = row.get("authorships") if isinstance(row.get("authorships"), list) else []
    if ships and isinstance(ships[0], dict):
        author = ships[0].get("author") if isinstance(ships[0].get("author"), dict) else {}
        first_author_id = str(author.get("id") or "").rsplit("/", 1)[-1]
    ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
    doi = str(row.get("doi") or ids.get("doi") or "").replace("https://doi.org/", "")
    oa = row.get("open_access") if isinstance(row.get("open_access"), dict) else {}
    loc = row.get("best_oa_location") if isinstance(row.get("best_oa_location"), dict) else {}
    # primary_location / source 都可能是 null，缺省成空 dict 再取。
    primary = row.get("primary_location") if isinstance(row.get("primary_location"), dict) else {}
    primary_src = primary.get("source") if isinstance(primary.get("source"), dict) else {}
    venue = str(primary_src.get("display_name") or row.get("venue") or "")[:200]
    # C218：撤回与勘误要标出来，不能和正常论文同等呈现。
    retracted = bool(row.get("is_retracted"))
    version = "retracted" if retracted else (str(row.get("type") or "")[:40] if row.get("type") else "")
    return {
        "title": str(row.get("display_name") or row.get("title") or "")[:500],
        "authors": authors[:12],
        "year": int(year) if str(year or "").isdigit() else year or None,
        "doi": doi[:128],
        "url": str(row.get("id") or row.get("url") or "")[:500],
        "abstract": str(row.get("abstract") or "")[:600],
        "citation_count": int(row.get("cited_by_count") or row.get("citation_count") or 0),
        "source": source,
        "openalex_id": str(row.get("id") or "").rsplit("/", 1)[-1],
        "author_openalex_id": first_author_id,
        "arxiv_id": str(ids.get("arxiv") or "")[:64],
        "pdf_url": str(loc.get("pdf_url") or oa.get("oa_url") or "")[:500],
        "venue": venue,
        "retracted": retracted,
        "work_type": version,
    }


def _author(row: dict, source: str) -> dict:
    inst = []
    for item in row.get("affiliations") or row.get("last_known_institutions") or []:
        if isinstance(item, dict):
            name = (item.get("institution") or {}).get("display_name") if isinstance(item.get("institution"), dict) else item.get("display_name")
            if name:
                inst.append(str(name))
        elif isinstance(item, str):
            inst.append(item)
    stats = row.get("summary_stats") if isinstance(row.get("summary_stats"), dict) else {}
    topics = []
    for topic in row.get("topics") or []:
        if isinstance(topic, dict) and topic.get("display_name"):
            topics.append(str(topic["display_name"]))
    ids = row.get("ids") if isinstance(row.get("ids"), dict) else {}
    return {
        "name": str(row.get("display_name") or row.get("name") or "")[:200],
        "alternatives": [str(x)[:200] for x in (row.get("display_name_alternatives") or []) if x][:6],
        "openalex_id": str(row.get("id") or "").rsplit("/", 1)[-1],
        "orcid": str(ids.get("orcid") or row.get("orcid") or "").rsplit("/", 1)[-1],
        "s2_id": str(row.get("authorId") or row.get("s2_id") or "")[:40],
        "dblp": str(row.get("dblp") or "")[:200],
        "affiliations": inst[:6],
        "topics": topics[:8],
        "works_count": int(row.get("works_count") or row.get("paperCount") or 0),
        "cited_by": int(row.get("cited_by_count") or row.get("citationCount") or 0),
        "h_index": int(stats.get("h_index") or row.get("hIndex") or 0),
        "source": source,
        "url": str(row.get("id") or "")[:300],
    }


def openalex_institution(name: str) -> dict:
    """中文校名解析成 OpenAlex 机构编号和官网域名。缓存 30 天。"""
    q = (name or "").strip()
    if len(q) < 2:
        return {}
    from urllib.parse import urlparse

    from fengsuan.app.services.web.common import cache_get, cache_put

    cached = cache_get("inst", q.lower(), 30 * 86400)
    if isinstance(cached, dict) and cached.get("id"):
        return cached
    data = _json(f"{OPENALEX}/institutions", _mailto_params({"search": q[:80], "per-page": 3}), host="openalex")
    rows = (data or {}).get("results") or []
    if not rows:
        return {}
    row = rows[0]
    home = str(row.get("homepage_url") or "")
    host = (urlparse(home).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    display = str(row.get("display_name") or "")[:200]
    names = [display] if display else []
    for alt in row.get("display_name_alternatives") or []:
        text = str(alt or "").strip()
        if text and text not in names:
            names.append(text[:200])
    out = {
        "id": str(row.get("id") or "").rsplit("/", 1)[-1],
        "name": display,
        "names": names[:8],
        "homepage": home[:300],
        "domain": host,
    }
    if out["id"]:
        cache_put("inst", q.lower(), out)
    return out


def openalex_search_authors(name: str, affiliation: str = "", *, institution_id: str = "") -> list[dict]:
    q = (name or "").strip()
    if len(q) < 2:
        return []
    if institution_id:
        filt = f"display_name.search:{q[:80]},affiliations.institution.id:{institution_id}"
        data = _json(f"{OPENALEX}/authors", _mailto_params({"filter": filt, "per-page": 8, "sort": "cited_by_count:desc"}), host="openalex")
        out = []
        for row in (data or {}).get("results") or []:
            author = _author(row, "openalex")
            if author.get("name"):
                out.append(author)
        return out

    def _fetch(with_aff: bool) -> list[dict]:
        filt = f"display_name.search:{q[:80]}"
        if with_aff and affiliation:
            filt += f",last_known_institutions.display_name.search:{affiliation[:40]}"
        data = _json(f"{OPENALEX}/authors", _mailto_params({"filter": filt, "per-page": 8, "sort": "cited_by_count:desc"}), host="openalex")
        out = []
        for row in (data or {}).get("results") or []:
            author = _author(row, "openalex")
            if author.get("name"):
                out.append(author)
        return out

    rows = _fetch(bool(affiliation))
    if affiliation and not rows:
        rows = _fetch(False)
        for author in rows:
            blob = " ".join(author.get("affiliations") or [])
            if affiliation.lower() not in blob.lower():
                author["affiliation_mismatch"] = True
    elif affiliation:
        for author in rows:
            blob = " ".join(author.get("affiliations") or [])
            if affiliation.lower() not in blob.lower():
                author["affiliation_mismatch"] = True
    return rows


def openalex_author(author_id: str) -> dict | None:
    aid = (author_id or "").strip().rsplit("/", 1)[-1]
    if not aid:
        return None
    data = _json(f"{OPENALEX}/authors/{aid}", _mailto_params(), host="openalex")
    if not data:
        return None
    return _author(data, "openalex")


def openalex_work(work_id: str) -> dict | None:
    wid = (work_id or "").strip().rsplit("/", 1)[-1]
    if not wid:
        return None
    data = _json(f"{OPENALEX}/works/{wid}", _mailto_params(), host="openalex")
    if not data or not (data.get("display_name") or data.get("title")):
        return None
    return _paper(data, "openalex")


def work_list(pack) -> list[dict]:
    """作者作品接口改成带总数的字典后，旧调用仍能拿到列表。"""
    if isinstance(pack, dict) and "works" in pack:
        return [row for row in (pack.get("works") or []) if isinstance(row, dict)]
    if isinstance(pack, list):
        return [row for row in pack if isinstance(row, dict)]
    return []


def work_total(pack) -> int:
    if isinstance(pack, dict):
        try:
            return int(pack.get("total") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def openalex_author_works(
    author_id: str,
    *,
    years: int | None = None,
    limit: int = 20,
    institution_id: str = "",
    page: int = 1,
    sort: str = "cited_by_count:desc",
) -> dict:
    """一位作者的作品。返回作品短名单和公开库总数，不把几百篇一次倒出来。"""
    aid = (author_id or "").strip().rsplit("/", 1)[-1]
    if not aid:
        return {"works": [], "total": 0, "page": 1}
    filt = f"author.id:{aid}"
    iid = (institution_id or "").strip().rsplit("/", 1)[-1]
    if iid:
        filt += f",institutions.id:{iid}"
    if years:
        from fengsuan.app.db.base import utcnow

        filt += f",from_publication_date:{utcnow().year - int(years)}-01-01"
    page_n = max(1, int(page or 1))
    sort_by = sort if sort in {"cited_by_count:desc", "publication_date:desc"} else "cited_by_count:desc"
    cache_key = f"{aid}|{filt}|{sort_by}|{max(1, min(50, int(limit or 20)))}|{page_n}"
    from fengsuan.app.services.web.common import cache_get, cache_put

    cached = cache_get("author_works", cache_key, 6 * 3600)
    if isinstance(cached, dict) and "works" in cached:
        return cached
    data = _json(
        f"{OPENALEX}/works",
        _mailto_params(
            {
                "filter": filt,
                "sort": sort_by,
                "per-page": max(1, min(50, int(limit or 20))),
                "page": page_n,
            }
        ),
        host="openalex",
    )
    rows = [_paper(row, "openalex") for row in (data or {}).get("results") or []]
    meta = (data or {}).get("meta") if isinstance((data or {}).get("meta"), dict) else {}
    try:
        total = int((meta or {}).get("count") or 0)
    except (TypeError, ValueError):
        total = 0
    if not total:
        total = len(rows)
    pack = {"works": rows, "total": total, "page": page_n}
    if data is not None:
        cache_put("author_works", cache_key, pack)
    return pack


def _search_total(rows: list[dict]) -> int:
    if rows and isinstance(rows[0], dict):
        try:
            return int(rows[0].pop("_search_total", 0) or 0)
        except (TypeError, ValueError):
            rows[0].pop("_search_total", None)
    return 0


def openalex_search_works(query: str, *, years: int | None = None, per_page: int = 20) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    # 给了年份（最新、近两年）就按发表日期，否则仍按被引。
    sort = "publication_date:desc" if years else "cited_by_count:desc"
    params: dict[str, Any] = {"search": q[:180], "per-page": max(1, min(40, per_page)), "sort": sort}
    if years:
        from fengsuan.app.db.base import utcnow

        params["filter"] = f"from_publication_date:{utcnow().year - int(years)}-01-01"
    data = _json(f"{OPENALEX}/works", _mailto_params(params), host="openalex")
    rows = [_paper(row, "openalex") for row in (data or {}).get("results") or []]
    meta = (data or {}).get("meta") if isinstance((data or {}).get("meta"), dict) else {}
    try:
        total = int((meta or {}).get("count") or 0)
    except (TypeError, ValueError):
        total = 0
    if rows and total:
        rows[0]["_search_total"] = total
    return rows


def openalex_topic_authors(topic: str, *, years: int = 5, limit: int = 8) -> list[dict]:
    q = (topic or "").strip()
    if len(q) < 2:
        return []
    from fengsuan.app.db.base import utcnow

    filt = f"default.search:{q[:120]},from_publication_date:{utcnow().year - int(years)}-01-01"
    data = _json(
        f"{OPENALEX}/works",
        _mailto_params({"filter": filt, "group_by": "authorships.author.id", "per-page": max(1, min(20, limit))}),
        host="openalex",
    )
    out = []
    for row in (data or {}).get("group_by") or []:
        key = str(row.get("key") or "")
        name = str(row.get("key_display_name") or "")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "openalex_id": key.rsplit("/", 1)[-1],
                "works_in_topic": int(row.get("count") or 0),
                "source": "openalex",
                "said": f"近 {years} 年这个主题下有 {int(row.get('count') or 0)} 篇",
            }
        )
    return out


def openalex_referenced(openalex_id: str, *, limit: int = 5) -> list[dict]:
    """这篇引用了谁。用来找被多篇种子共同引用的奠基作。"""
    wid = (openalex_id or "").rsplit("/", 1)[-1]
    if not wid:
        return []
    work = _json(f"{OPENALEX}/works/{wid}", _mailto_params({"select": "referenced_works"}), host="openalex")
    ids = []
    for item in (work or {}).get("referenced_works") or []:
        raw = item.get("id") if isinstance(item, dict) else item
        rid = str(raw or "").rsplit("/", 1)[-1]
        if rid:
            ids.append(rid)
        if len(ids) >= max(1, min(8, limit)):
            break
    if not ids:
        return []
    data = _json(
        f"{OPENALEX}/works",
        _mailto_params({"filter": "openalex:" + "|".join(ids), "per-page": len(ids)}),
        host="openalex",
    )
    return [_paper(row, "openalex") for row in (data or {}).get("results") or [] if isinstance(row, dict)]


def openalex_citing(openalex_id: str, *, limit: int = 8, page: int = 1) -> list[dict]:
    wid = (openalex_id or "").rsplit("/", 1)[-1]
    if not wid:
        return []
    data = _json(
        f"{OPENALEX}/works",
        _mailto_params(
            {
                "filter": f"cites:{wid}",
                "sort": "cited_by_count:desc",
                "per-page": max(1, min(20, limit)),
                "page": max(1, int(page or 1)),
            }
        ),
        host="openalex",
    )
    return [_paper(row, "openalex") for row in (data or {}).get("results") or []]


def openalex_related(openalex_id: str, *, limit: int = 6) -> list[dict]:
    wid = (openalex_id or "").rsplit("/", 1)[-1]
    if not wid:
        return []
    work = _json(f"{OPENALEX}/works/{wid}", _mailto_params({"select": "related_works"}), host="openalex")
    ids = []
    for item in (work or {}).get("related_works") or []:
        # related_works 是 [{"id": "https://openalex.org/W...", "score": ...}]，不是字符串列表。
        raw = item.get("id") if isinstance(item, dict) else item
        rid = str(raw or "").rsplit("/", 1)[-1]
        if rid:
            ids.append(rid)
        if len(ids) >= limit:
            break
    if not ids:
        return []
    data = _json(
        f"{OPENALEX}/works",
        _mailto_params({"filter": "openalex:" + "|".join(ids), "per-page": len(ids)}),
        host="openalex",
    )
    return [_paper(row, "openalex") for row in (data or {}).get("results") or [] if isinstance(row, dict)]


def openalex_work_by_doi(doi: str) -> dict | None:
    d = (doi or "").strip()
    if not d:
        return None
    data = _json(f"{OPENALEX}/works/doi:{d}", _mailto_params(), host="openalex")
    if not data or not data.get("display_name"):
        return None
    return _paper(data, "openalex")


def orcid_person(orcid: str) -> dict | None:
    oid = (orcid or "").strip().rsplit("/", 1)[-1]
    if not oid:
        return None
    import httpx

    try:
        with httpx.Client(timeout=12.0, follow_redirects=True) as client:
            resp = client.get(
                f"https://pub.orcid.org/v3.0/{oid}/record",
                headers={"Accept": "application/json", "User-Agent": f"fengsuan-research (mailto:{contact_email()})"},
            )
        if resp.status_code >= 400:
            return None
        data = resp.json()
    except Exception:
        return None
    person = (data if isinstance(data, dict) else {}).get("person") or {}
    data = data if isinstance(data, dict) else {}
    name = person.get("name") or {}
    given = ((name.get("given-names") or {}).get("value") or "").strip()
    family = ((name.get("family-name") or {}).get("value") or "").strip()
    employments = []
    for group in ((data.get("activities-summary") or {}).get("employments") or {}).get("affiliation-group") or []:
        for item in group.get("summaries") or []:
            emp = (item.get("employment-summary") or {})
            org = ((emp.get("organization") or {}).get("name") or "").strip()
            role = (emp.get("role-title") or "").strip()
            if org:
                employments.append(" ".join(x for x in (role, org) if x))
    return {
        "name": (given + " " + family).strip(),
        "orcid": oid,
        "employments": employments[:8],
        "source": "orcid",
        "url": f"https://orcid.org/{oid}",
    }


def dblp_authors(name: str) -> list[dict]:
    q = (name or "").strip()
    if len(q) < 2:
        return []
    data = _json("https://dblp.org/search/author/api", {"q": q[:120], "format": "json", "h": 5}, host="dblp")
    hits = (((data or {}).get("result") or {}).get("hits") or {}).get("hit") or []
    out = []
    for hit in hits:
        info = hit.get("info") or {}
        out.append(
            {
                "name": str(info.get("author") or "")[:200],
                "dblp": str(info.get("url") or "")[:300],
                "affiliations": [str(info.get("notes") or "")] if info.get("notes") else [],
                "source": "dblp",
                "openalex_id": "",
                "orcid": "",
            }
        )
    return [row for row in out if row["name"]]


def dblp_pubs(query: str, *, limit: int = 10) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    data = _json(
        "https://dblp.org/search/publ/api",
        {"q": q[:160], "format": "json", "h": max(1, min(20, limit))},
        host="dblp",
    )
    hits = (((data or {}).get("result") or {}).get("hits") or {}).get("hit") or []
    out = []
    for hit in hits:
        info = hit.get("info") or {}
        authors = info.get("authors") or {}
        author = authors.get("author") if isinstance(authors, dict) else []
        if isinstance(author, dict):
            author = [author]
        names = [str(a.get("text") or a.get("@text") or "") for a in author or [] if isinstance(a, dict)]
        year = info.get("year")
        out.append(
            {
                "title": str(info.get("title") or "")[:500],
                "authors": [n for n in names if n][:12],
                "year": int(year) if str(year or "").isdigit() else None,
                "doi": str(info.get("doi") or "")[:128],
                "url": str(info.get("ee") or info.get("url") or "")[:500],
                "venue": str(info.get("venue") or "")[:200],
                "source": "dblp",
                "citation_count": 0,
            }
        )
    return [row for row in out if row["title"]]


def s2_citing(doi: str, *, limit: int = 5) -> list[dict]:
    """有 Semantic Scholar 密钥时补引用和一句话摘要。没有密钥就空着。"""
    key = (os.environ.get("S2_API_KEY") or "").strip()
    doi = (doi or "").strip()
    if not key or not doi:
        return []
    import httpx

    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}/citations",
                params={"limit": max(1, min(10, limit)), "fields": "title,year,externalIds,url,tldr,authors"},
                headers={"x-api-key": key},
            )
        if resp.status_code >= 400:
            return []
        data = resp.json()
    except Exception:
        return []
    out = []
    for row in (data if isinstance(data, dict) else {}).get("data") or []:
        paper = row.get("citingPaper") if isinstance(row, dict) else None
        if not isinstance(paper, dict) or not paper.get("title"):
            continue
        ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
        authors = []
        for author in paper.get("authors") or []:
            if isinstance(author, dict) and author.get("name"):
                authors.append(str(author["name"]))
        tldr = paper.get("tldr") if isinstance(paper.get("tldr"), dict) else {}
        out.append(
            {
                "title": str(paper.get("title") or "")[:500],
                "year": paper.get("year"),
                "doi": str(ids.get("DOI") or "")[:128],
                "url": str(paper.get("url") or "")[:500],
                "authors": authors[:8],
                "abstract": str(tldr.get("text") or "")[:400],
                "source": "s2",
            }
        )
    return out


def s2_authors(name: str) -> list[dict]:
    q = (name or "").strip()
    if len(q) < 2:
        return []
    headers = {}
    key = (os.environ.get("S2_API_KEY") or "").strip()
    if key:
        headers["x-api-key"] = key
    import httpx

    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(
                "https://api.semanticscholar.org/graph/v1/author/search",
                params={"query": q[:120], "limit": 5, "fields": "name,affiliations,paperCount,citationCount,hIndex,url"},
                headers=headers,
            )
        if resp.status_code >= 400:
            return []
        data = resp.json()
    except Exception:
        return []
    return [_author(row, "s2") for row in (data if isinstance(data, dict) else {}).get("data") or [] if isinstance(row, dict)]


def unpaywall(doi: str) -> dict | None:
    d = (doi or "").strip()
    if not d:
        return None
    data = _json(f"https://api.unpaywall.org/v2/{d}", {"email": contact_email()}, host="unpaywall")
    if not data:
        return None
    loc = data.get("best_oa_location") if isinstance(data.get("best_oa_location"), dict) else {}
    return {
        "doi": d,
        "is_oa": bool(data.get("is_oa")),
        "pdf_url": str(loc.get("url_for_pdf") or loc.get("url") or "")[:500],
        "host": str(loc.get("host_type") or "")[:40],
        "version": str(loc.get("version") or "")[:40],
        "license": str(loc.get("license") or "")[:40],
    }


def crossref_search(query: str, *, limit: int = 8) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    data = _json(
        "https://api.crossref.org/works",
        {"query.bibliographic": q[:180], "rows": max(1, min(20, limit)), "mailto": contact_email()},
        host="crossref",
    )
    items = ((data or {}).get("message") or {}).get("items") or []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or [""])[0]
        authors = []
        for a in item.get("author") or []:
            name = " ".join(x for x in (a.get("given"), a.get("family")) if x).strip()
            if name:
                authors.append(name)
        parts = (item.get("issued") or {}).get("date-parts") or []
        year = parts[0][0] if parts and parts[0] else None
        out.append(
            {
                "title": str(title)[:500],
                "authors": authors[:12],
                "year": year,
                "doi": str(item.get("DOI") or "")[:128],
                "url": str(item.get("URL") or "")[:500],
                "venue": str((item.get("container-title") or [""])[0] or "")[:200],
                "source": "crossref",
                "citation_count": int(item.get("is-referenced-by-count") or 0),
            }
        )
    return [row for row in out if row["title"]]


def europepmc_search(query: str, *, limit: int = 8) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    data = _json(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        {"query": q[:180], "format": "json", "pageSize": max(1, min(20, limit)), "resultType": "core"},
        host="europepmc",
    )
    out = []
    for row in ((data or {}).get("resultList") or {}).get("result") or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "title": str(row.get("title") or "")[:500],
                "authors": [a.strip() for a in str(row.get("authorString") or "").split(",") if a.strip()][:12],
                "year": int(row["pubYear"]) if str(row.get("pubYear") or "").isdigit() else None,
                "doi": str(row.get("doi") or "")[:128],
                "url": str(row.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url") or "")[:500]
                if isinstance(row.get("fullTextUrlList"), dict)
                else "",
                "abstract": str(row.get("abstractText") or "")[:600],
                "source": "europepmc",
                "citation_count": int(row.get("citedByCount") or 0),
                "pdf_url": "",
            }
        )
    return [row for row in out if row["title"]]


def core_search(query: str, *, limit: int = 5) -> list[dict]:
    key = (os.environ.get("CORE_API_KEY") or "").strip()
    q = (query or "").strip()
    if not key or len(q) < 2:
        return []
    import httpx

    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(
                "https://api.core.ac.uk/v3/search/works",
                params={"q": q[:160], "limit": max(1, min(10, limit))},
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code >= 400:
            return []
        data = resp.json()
    except Exception:
        return []
    out = []
    for row in (data if isinstance(data, dict) else {}).get("results") or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "title": str(row.get("title") or "")[:500],
                "authors": [str(a.get("name") or "") for a in row.get("authors") or [] if isinstance(a, dict)][:12],
                "year": row.get("yearPublished"),
                "doi": str(row.get("doi") or "")[:128],
                "url": str(row.get("downloadUrl") or row.get("sourceFulltextUrls") or "")[:500],
                "pdf_url": str(row.get("downloadUrl") or "")[:500],
                "source": "core",
                "citation_count": int(row.get("citationCount") or 0),
            }
        )
    return [row for row in out if row["title"]]


def openreview_search(query: str, *, limit: int = 5) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    data = _json(
        "https://api2.openreview.net/notes/search",
        {"term": q[:120], "limit": max(1, min(10, limit))},
        host="openreview",
    )
    notes = (data or {}).get("notes") or (data or {}).get("results") or []
    out = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        content = note.get("content") if isinstance(note.get("content"), dict) else {}
        title = content.get("title")
        if isinstance(title, dict):
            title = title.get("value")
        if not title:
            continue
        out.append(
            {
                "title": str(title)[:500],
                "authors": [],
                "year": None,
                "url": f"https://openreview.net/forum?id={note.get('id')}" if note.get("id") else "",
                "source": "openreview",
                "citation_count": 0,
                "venue": str(content.get("venue") or "")[:120],
            }
        )
    return out


def hf_papers(limit: int = 5) -> list[dict]:
    data = _json("https://huggingface.co/api/daily_papers", {"limit": max(1, min(10, limit))}, host="hf")
    rows = data if isinstance(data, list) else (data or {}).get("papers") or []
    out = []
    for row in rows:
        paper = row.get("paper") if isinstance(row, dict) and isinstance(row.get("paper"), dict) else row
        if not isinstance(paper, dict):
            continue
        title = paper.get("title") or ""
        if title:
            out.append(
                {
                    "title": str(title)[:500],
                    "url": "https://huggingface.co/papers/" + str(paper.get("id") or ""),
                    "source": "hf",
                    "citation_count": int(paper.get("upvotes") or 0),
                    "authors": [],
                    "year": None,
                }
            )
    return out


def github_repos(query: str, *, limit: int = 3) -> list[dict]:
    q = (query or "").strip()
    if len(q) < 2:
        return []
    data = _json(
        "https://api.github.com/search/repositories",
        {"q": q[:120], "sort": "stars", "per_page": max(1, min(5, limit))},
        host="github",
    )
    out = []
    for row in (data or {}).get("items") or []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "title": str(row.get("full_name") or "")[:200],
                "url": str(row.get("html_url") or "")[:300],
                "stars": int(row.get("stargazers_count") or 0),
                "source": "github",
            }
        )
    return out
