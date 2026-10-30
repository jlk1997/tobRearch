"""把外面的论文收进组里。只走开放全文，付费的留给文献捕手。"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

import httpx

from fengsuan.app.core.netguard import assert_http_url
from fengsuan.app.services.web.common import blocked_reason

_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
_ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
_MAX_PDF = 60 * 1024 * 1024


def _as_ref(ref) -> dict:
    if isinstance(ref, dict):
        return ref
    text = str(ref or "").strip()
    doi = _DOI.search(text)
    arxiv = _ARXIV.search(text)
    if text.startswith("http"):
        return {"url": text[:1000], "doi": doi.group(0) if doi else "", "arxiv_id": arxiv.group(1) if arxiv else ""}
    if doi:
        return {"doi": doi.group(0)}
    if arxiv:
        return {"arxiv_id": arxiv.group(1)}
    return {"title": text[:300]}


def resolve_ref(ref) -> dict:
    raw = _as_ref(ref)
    meta = {
        "title": str(raw.get("title") or "")[:500],
        "authors": raw.get("authors") or [],
        "abstract": str(raw.get("abstract") or raw.get("snippet") or "")[:1000],
        "doi": str(raw.get("doi") or "")[:128],
        "arxiv_id": str(raw.get("arxiv_id") or "")[:64],
        "url": str(raw.get("url") or "")[:1000],
        "year": raw.get("year") or raw.get("publish_year"),
        "venue": str(raw.get("venue") or "")[:200],
        "cnki_id": str(raw.get("cnki_id") or "")[:128],
        "source": str(raw.get("source") or "")[:40],
        "openalex_id": str(raw.get("openalex_id") or "")[:40],
        "pdf_url": str(raw.get("pdf_url") or "")[:500],
        "cluster_id": str(raw.get("cluster_id") or "")[:40],
    }
    try:
        from fengsuan.app.services.web import scholarly

        if meta["openalex_id"] and not meta["doi"]:
            work = scholarly.openalex_work(meta["openalex_id"])
            if work:
                meta = {**meta, **{k: v for k, v in work.items() if v}}
        if meta["doi"]:
            work = scholarly.openalex_work_by_doi(meta["doi"])
            if work:
                meta = {**meta, **{k: v for k, v in work.items() if v}}
        elif meta["title"] and not meta["doi"]:
            rows = scholarly.openalex_search_works(meta["title"], per_page=3)
            if rows:
                top = rows[0]
                top.pop("_search_total", None)
                if _same_title(meta["title"], top.get("title") or ""):
                    meta = {**meta, **{k: v for k, v in top.items() if v and k != "_search_total"}}
        if meta["url"] and (not meta["title"] or not meta["doi"]):
            from fengsuan.app.services.web.fetch import fetch_page

            page = fetch_page(meta["url"], strip=True)
            paper = page.get("paper") or {}
            if paper.get("title") and not meta["title"]:
                meta["title"] = paper["title"]
            if paper.get("doi") and not meta["doi"]:
                meta["doi"] = paper["doi"]
            if paper.get("abstract") and not meta["abstract"]:
                meta["abstract"] = paper["abstract"]
            if paper.get("pdf_url") and not meta["pdf_url"]:
                meta["pdf_url"] = str(paper["pdf_url"])[:500]
    except Exception:
        pass
    meta.pop("_search_total", None)
    return meta


def _same_title(a: str, b: str) -> bool:
    def norm(text: str) -> str:
        return "".join(ch for ch in (text or "").lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return na == nb or na[:40] == nb[:40]


def find_open_pdf(meta: dict) -> dict:
    """按开放渠道找 PDF。已经有的地址先用，网络失败不能把已有地址丢掉。"""
    arxiv = str(meta.get("arxiv_id") or "")
    if arxiv and "/" not in arxiv and ".." not in arxiv:
        return {"pdf_url": f"https://arxiv.org/pdf/{arxiv}.pdf", "via": "arxiv", "license": "", "host": "arxiv.org", "version": "preprint"}
    pdf = str(meta.get("pdf_url") or "")
    if pdf and not blocked_reason(pdf):
        return {"pdf_url": pdf, "via": meta.get("source") or "oa", "license": "", "host": "", "version": ""}
    doi = str(meta.get("doi") or "")
    try:
        from fengsuan.app.services.web import scholarly

        if doi:
            up = scholarly.unpaywall(doi)
            if up and up.get("pdf_url"):
                return {
                    "pdf_url": up["pdf_url"],
                    "via": "unpaywall",
                    "license": up.get("license") or "",
                    "host": up.get("host") or "",
                    "version": up.get("version") or "",
                }
        cluster = str(meta.get("cluster_id") or "")
        if cluster:
            from fengsuan.app.services.web.scholar import scholar_versions

            pack = scholar_versions(cluster)
            for row in pack.get("papers") or []:
                if row.get("pdf_url"):
                    return {"pdf_url": row["pdf_url"], "via": "scholar_versions", "license": "", "host": "", "version": "author_copy"}
    except Exception:
        pass
    url = str(meta.get("url") or "")
    if any(bit in url for bit in ("chinaxiv.org", "ncpssd.org", "paper.edu.cn")) and url.lower().endswith(".pdf"):
        return {"pdf_url": url, "via": "cn_open", "license": "", "host": "", "version": ""}
    return {"pdf_url": "", "via": "", "note": "没有找到开放 PDF。订阅全文用文献捕手，在你自己的浏览器里收。"}


def _download(url: str) -> bytes:
    if blocked_reason(url):
        raise ValueError("blocked")
    current = url
    for _ in range(3):
        clean = assert_http_url(current, what="PDF", allow_private=False)
        with httpx.Client(timeout=15.0, follow_redirects=False) as client:
            resp = client.get(clean, headers={"User-Agent": "fengsuan-research"})
        if resp.status_code in {301, 302, 303, 307, 308}:
            current = resp.headers.get("location") or ""
            if not current:
                raise ValueError("redirect")
            continue
        if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
            raise ValueError("not_pdf")
        if len(resp.content) > _MAX_PDF:
            raise ValueError("too_big")
        return resp.content
    raise ValueError("redirect")


_PREVIEW_CACHE: dict[str, tuple[float, dict, dict]] = {}
_CACHE_LOCK = threading.Lock()


def _ref_key(ref) -> str:
    raw = _as_ref(ref)
    return "|".join(
        [
            str(raw.get("doi") or ""),
            str(raw.get("arxiv_id") or ""),
            str(raw.get("openalex_id") or ""),
            str(raw.get("title") or "")[:180],
            str(raw.get("url") or "")[:180],
        ]
    )


def _meta_and_pdf(ref) -> tuple[dict, dict]:
    key = _ref_key(ref)
    now = time.time()
    if key.strip("|"):
        with _CACHE_LOCK:
            hit = _PREVIEW_CACHE.get(key)
        if hit and now - hit[0] < 600:
            return hit[1], hit[2]
    meta = resolve_ref(ref)
    oa = find_open_pdf(meta)
    if key.strip("|"):
        with _CACHE_LOCK:
            _PREVIEW_CACHE[key] = (now, meta, oa)
    return meta, oa


def _preview_from(db, lab_id: str, meta: dict, oa: dict, direction_id: str) -> dict:
    from fengsuan.app.services.literature import find_duplicate

    dup = None
    if meta.get("title") or meta.get("doi") or meta.get("arxiv_id"):
        dup = find_duplicate(
            db,
            lab_id,
            doi=meta.get("doi") or None,
            title=meta.get("title") or "",
            year=meta.get("year") if isinstance(meta.get("year"), int) else None,
            arxiv_id=meta.get("arxiv_id") or None,
            title_match=True,
        )
    return {
        "title": meta.get("title") or "未命名",
        "authors": meta.get("authors") or [],
        "year": meta.get("year"),
        "doi": meta.get("doi") or "",
        "arxiv_id": meta.get("arxiv_id") or "",
        "openalex_id": meta.get("openalex_id") or "",
        "cnki_id": meta.get("cnki_id") or "",
        "url": meta.get("url") or "",
        "already": bool(dup),
        "item_id": dup.id if dup is not None else "",
        "oa": oa,
        "direction_id": direction_id,
        "meta": meta,
        "capture": not oa.get("pdf_url"),
    }


def preview_import(db, lab_id: str, ref, *, direction_id: str = "") -> dict:
    meta, oa = _meta_and_pdf(ref)
    return _preview_from(db, lab_id, meta, oa, direction_id)


def preview_many(db, lab_id: str, refs, *, direction_id: str = "") -> list[dict]:
    """一批预览同时去对公开全文，12 秒内先用已经回来的。库里查重仍在这一条连接上做。"""
    refs = list(refs or [])[:10]
    packed: list[tuple[dict, dict] | None] = [None] * len(refs)
    pool = ThreadPoolExecutor(max_workers=4)
    futures = {pool.submit(_meta_and_pdf, ref): i for i, ref in enumerate(refs)}
    done, _pending = wait(futures, timeout=12)
    pool.shutdown(wait=False, cancel_futures=True)
    for fut in done:
        try:
            packed[futures[fut]] = fut.result()
        except Exception:
            packed[futures[fut]] = None
    rows = []
    for i, ref in enumerate(refs):
        if packed[i]:
            meta, oa = packed[i]
        else:
            raw = _as_ref(ref)
            meta = {
                "title": str(raw.get("title") or "未命名")[:500],
                "authors": raw.get("authors") or [],
                "doi": str(raw.get("doi") or ""),
                "arxiv_id": str(raw.get("arxiv_id") or ""),
                "openalex_id": str(raw.get("openalex_id") or ""),
                "pdf_url": str(raw.get("pdf_url") or ""),
                "cluster_id": str(raw.get("cluster_id") or ""),
                "cnki_id": str(raw.get("cnki_id") or ""),
                "url": str(raw.get("url") or ""),
                "year": raw.get("year") or raw.get("publish_year"),
            }
            oa = {"pdf_url": "", "via": "", "note": "这次没赶上公开全文，先按题录预览。"}
        rows.append(_preview_from(db, lab_id, meta, oa, direction_id))
    return rows


def do_import(db, lab_id: str, user_id: str, ref, *, direction_id: str = "") -> dict:
    preview = preview_import(db, lab_id, ref, direction_id=direction_id)
    meta = preview["meta"]
    if not meta.get("title"):
        return {"ok": False, "error": "没有题名，没法收", "preview": preview}
    from fengsuan.app.api.v1.literature import _attach_pdf, _create_item, _hang_import_dirs
    from fengsuan.app.models.literature import LiteratureItem

    item, already = _create_item(
        db,
        lab_id,
        user_id,
        {
            "title": meta.get("title"),
            "authors": meta.get("authors") or [],
            "abstract": meta.get("abstract") or "",
            "doi": meta.get("doi") or None,
            "arxiv_id": meta.get("arxiv_id") or None,
            "url": meta.get("url") or None,
            "venue": meta.get("venue") or None,
            "publish_year": meta.get("year") if isinstance(meta.get("year"), int) else None,
            "openalex_id": meta.get("openalex_id") or None,
            "cnki_id": meta.get("cnki_id") or None,
        },
        "agent",
        False,
    )
    oa = preview.get("oa") or {}
    pdf_state = ""
    if oa.get("pdf_url"):
        try:
            blob = _download(oa["pdf_url"])
            _attach_pdf(db, item, lab_id, blob, f"{(meta.get('arxiv_id') or item.id[:8])}.pdf")
            pdf_state = "attached"
        except Exception:
            ids = {}
            try:
                ids = json.loads(item.external_ids_json or "{}")
            except json.JSONDecodeError:
                ids = {}
            if not isinstance(ids, dict):
                ids = {}
            ids["oa_pdf"] = oa["pdf_url"][:500]
            ids["oa_via"] = oa.get("via") or ""
            item.external_ids_json = json.dumps(ids, ensure_ascii=False)
            pdf_state = "queued"
    if direction_id:
        _hang_import_dirs(db, lab_id, user_id, item, {}, [direction_id])
    db.flush()
    if not isinstance(item, LiteratureItem):
        return {"ok": False, "error": "没写成"}
    return {
        "ok": True,
        "item_id": item.id,
        "title": item.title,
        "already": already,
        "pdf": pdf_state or ("none" if not oa.get("pdf_url") else ""),
        "href": f"/literature/{item.id}",
        "capture": preview.get("capture"),
    }
