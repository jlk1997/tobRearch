"""网页检索：先原生联网，不通再博查，再 Tavily。"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from fengsuan.app.services.web.common import SourceFailures, cache_get, cache_put
from fengsuan.app.services.web.native import native_search


def _site(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _fail(source: str, reason: str = "") -> None:
    holder = SourceFailures.current()
    if holder is not None:
        holder.note(source, reason)


def _pack(hits: list[dict], provider: str, *, note: str = "", queries: list[str] | None = None) -> dict:
    out = []
    for hit in hits or []:
        url = str(hit.get("url") or "")
        out.append(
            {
                "title": str(hit.get("title") or url)[:300],
                "url": url[:1000],
                "site": hit.get("site") or _site(url),
                "snippet": str(hit.get("snippet") or hit.get("abstract") or "")[:800],
                "published": str(hit.get("published") or "")[:40],
                "provider": provider,
            }
        )
    failures = SourceFailures.current()
    failed = failures.as_dict() if failures is not None else {}
    if failed:
        prefix = f"{note} " if note else ""
        note = f"{prefix}{failures.note_text()}".strip()
    return {"hits": out, "provider": provider, "note": note, "queries": queries or [], "failed_sources": failed}


def search_bocha(query: str, *, site: str = "", recent: bool = False, transport=None) -> list[dict]:
    key = (os.environ.get("BOCHA_API_KEY") or "").strip()
    if not (query or "").strip():
        return []
    if not key:
        return []
    q = f"site:{site} {query}".strip() if site else query.strip()
    body = {
        "query": q[:300],
        "freshness": "oneYear" if recent else "noLimit",
        "summary": True,
        "count": 8,
    }
    try:
        with httpx.Client(timeout=8.0, transport=transport) as client:
            resp = client.post(
                "https://api.bochaai.com/v1/web-search",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
            )
        if resp.status_code >= 400:
            _fail("博查", f"接口 {resp.status_code}")
            return []
        data = resp.json()
    except Exception as exc:
        _fail("博查", type(exc).__name__)
        return []
    pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or data.get("results") or []
    hits = []
    for row in pages:
        if not isinstance(row, dict):
            continue
        url = row.get("url") or ""
        hits.append(
            {
                "title": row.get("name") or row.get("title") or url,
                "url": url,
                "snippet": row.get("snippet") or row.get("summary") or "",
                "site": row.get("siteName") or _site(url),
                "published": row.get("datePublished") or "",
            }
        )
    return hits


def search_tavily_hits(query: str, *, transport=None) -> list[dict]:
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not (query or "").strip():
        return []
    if not key:
        return []
    try:
        with httpx.Client(timeout=8.0, transport=transport) as client:
            resp = client.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query[:300], "search_depth": "basic", "max_results": 5},
            )
        if resp.status_code >= 400:
            _fail("Tavily", f"接口 {resp.status_code}")
            return []
        data = resp.json()
    except Exception as exc:
        _fail("Tavily", type(exc).__name__)
        return []
    hits = []
    for row in data.get("results") or []:
        if not isinstance(row, dict):
            continue
        url = row.get("url") or ""
        hits.append(
            {
                "title": row.get("title") or url,
                "url": url,
                "snippet": row.get("content") or "",
                "published": "",
            }
        )
    return hits


def search_web(query: str, *, site: str = "", recent: bool = False, llm=None, transport=None) -> dict:
    q = (query or "").strip()
    if not q:
        return _pack([], "", note="没有检索词")
    with SourceFailures.scoped():
        return _search_web(q, site=site, recent=recent, llm=llm, transport=transport)


def _search_web(q: str, *, site: str = "", recent: bool = False, llm=None, transport=None) -> dict:
    instruction = f"site:{site} {q}" if site else q
    if recent:
        instruction += " 最近一年"
    cache_key = instruction.lower()[:240]
    cached = cache_get("web", cache_key, 86400)
    if cached and cached.get("hits") and not cached.get("failed_sources"):
        cached = dict(cached)
        cached["cached"] = True
        return cached
    note = ""
    failures = SourceFailures.current()
    if llm is not None:
        native = native_search(llm, f"检索并列出来源：{instruction}", transport=transport)
        if native.get("ok") and native.get("hits"):
            packed = _pack(native["hits"], "minimax", queries=native.get("queries") or [], note="")
            packed["text"] = native.get("text") or ""
            cache_put("web", cache_key, packed)
            return packed
        if native.get("error") and native.get("error") not in {"unsupported", "adapter_pending"}:
            note = "原生联网没连上，改用备用检索。"
            if failures is not None:
                failures.note("原生联网", "这次没连上")
    bocha = search_bocha(q, site=site, recent=recent, transport=transport)
    if bocha:
        packed = _pack(bocha, "bocha", note=note)
        if not packed.get("failed_sources"):
            cache_put("web", cache_key, packed)
        return packed
    tavily = search_tavily_hits(instruction, transport=transport)
    if tavily:
        packed = _pack(tavily, "tavily", note=note or "博查没有结果，用了 Tavily。")
        if not packed.get("failed_sources"):
            cache_put("web", cache_key, packed)
        return packed
    if not (os.environ.get("BOCHA_API_KEY") or os.environ.get("TAVILY_API_KEY")) and llm is None:
        note = "联网检索没开。"
    elif not note:
        note = "这次没搜到网页。"
    if failures is not None and not failures.ok:
        packed = _pack([], "", note=note)
        return packed
    packed = _pack([], "", note=note)
    cache_put("web", cache_key, packed)
    return packed
