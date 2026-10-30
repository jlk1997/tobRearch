"""中文库：知网、万方、维普拿题录和摘要；开放库可以拿全文地址。"""

from __future__ import annotations

import re

_CN_SITES = {
    "cnki": "cnki.net",
    "wanfang": "wanfangdata.com.cn",
    "vip": "cqvip.com",
    "chinaxiv": "chinaxiv.org",
    "ncpssd": "ncpssd.org",
    "paper.edu.cn": "paper.edu.cn",
}

_OPEN_FULLTEXT = ("chinaxiv.org", "ncpssd.org", "paper.edu.cn")


def site_of_source(name: str) -> str:
    return _CN_SITES.get((name or "").strip().lower(), "")


def parse_cn_html(html: str, url: str = "") -> dict:
    """从详情页抽题录。抽不到摘要就明说，不编。"""
    from fengsuan.app.services.capture import parse_paper_html

    parsed = parse_paper_html(html or "", url or "")
    text = re.sub(r"<script[\s\S]*?</script>", " ", html or "", flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain)

    def _field(label: str) -> str:
        matched = re.search(label + r"\s*[:：]\s*(.{2,240}?)(?:摘要|关键词|基金|作者|DOI|专辑|专题|$)", plain)
        return (matched.group(1).strip() if matched else "")[:400]

    abstract = parsed.get("abstract") or _field("摘要")
    keywords = _field("关键词") or _field("关键字")
    fund = _field("基金")
    cited = re.search(r"被引[^0-9]{0,6}(\d+)", plain)
    downloads = re.search(r"下载[^0-9]{0,6}(\d+)", plain)
    host = ""
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    open_pdf = ""
    if any(bit in host for bit in _OPEN_FULLTEXT):
        pdf = re.search(r"href=['\"]([^'\"]+\.pdf[^'\"]*)['\"]", html or "", flags=re.I)
        if pdf:
            open_pdf = pdf.group(1)
    source = "cn"
    for name, domain in _CN_SITES.items():
        if domain in host:
            source = name
            break
    return {
        "title": parsed.get("title") or "",
        "authors": parsed.get("authors") or [],
        "abstract": abstract,
        "keywords": keywords,
        "fund": fund,
        "venue": parsed.get("venue") or "",
        "year": parsed.get("publish_year"),
        "doi": parsed.get("doi") or "",
        "cnki_id": parsed.get("cnki_id") or "",
        "url": url,
        "cited_by": int(cited.group(1)) if cited else None,
        "downloads": int(downloads.group(1)) if downloads else None,
        "pdf_url": open_pdf or parsed.get("pdf_hint") or "",
        "source": source,
        "fulltext": "open" if open_pdf else "metadata",
        "note": "" if abstract else "详情页没有公开摘要。",
    }


def paper_from_hit(hit: dict) -> dict:
    """中文检索条目收成题录。订阅全文不在这里下载。"""
    from fengsuan.app.services.capture import ids_from_url

    url = str(hit.get("url") or "")
    ids = ids_from_url(url)
    doi = str(hit.get("doi") or "")
    if not doi:
        found = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", url, re.I)
        doi = found.group(0) if found else ""
    pdf = str(hit.get("pdf_url") or "")
    if not pdf and str(hit.get("fulltext") or "") == "open" and url.lower().endswith(".pdf"):
        pdf = url
    return {
        "title": hit.get("title") or "",
        "abstract": hit.get("snippet") or hit.get("abstract") or "",
        "url": url,
        "year": hit.get("year"),
        "authors": hit.get("authors") or [],
        "doi": doi[:128],
        "cnki_id": str(hit.get("cnki_id") or ids.get("cnki_id") or "")[:128],
        "arxiv_id": str(hit.get("arxiv_id") or ids.get("arxiv_id") or "")[:64],
        "pdf_url": pdf[:500],
        "source": "cn",
    }


def search_cn(
    query: str,
    *,
    sources: list[str] | None = None,
    llm=None,
    limit: int = 20,
) -> dict:
    """用原生联网限定站点找条目。全文订阅仍走文献捕手。

    D-HONEST：各站点按同一预算并行查，不再由循环顺序决定谁拿得到。
    """
    from concurrent.futures import ThreadPoolExecutor

    from fengsuan.app.services.web.common import SourceFailures
    from fengsuan.app.services.web.search import search_web

    wanted = [s for s in (sources or ["cnki", "wanfang", "vip", "chinaxiv"]) if site_of_source(s)]
    if not wanted:
        return {"hits": [], "note": "没指定中文来源。", "failed_sources": {}}
    cap = max(1, int(limit or 20))
    per_site = max(3, cap // len(wanted))

    def _one(name: str) -> tuple[str, dict]:
        try:
            return name, search_web(query, site=site_of_source(name), llm=llm)
        except Exception as exc:
            holder = SourceFailures.current()
            if holder is not None:
                holder.note(name, type(exc).__name__)
            return name, {"hits": [], "note": ""}

    rows: list[dict] = []
    notes: list[str] = []
    failed: dict = {}
    with SourceFailures.scoped() as holder:
        with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            for name, pack in pool.map(_one, wanted):
                failed.update(pack.get("failed_sources") or {})
                if pack.get("note"):
                    notes.append(f"{name}：{pack['note']}")
                for hit in (pack.get("hits") or [])[:per_site]:
                    row = dict(hit)
                    row["cn_source"] = name
                    row["fulltext"] = (
                        "open" if any(bit in (row.get("url") or "") for bit in _OPEN_FULLTEXT) else "metadata"
                    )
                    rows.append(row)
        failed.update(holder.as_dict())

    note = "中文库先给题录和摘要。订阅全文用文献捕手，在你自己的浏览器里收。"
    if failed:
        names = "、".join(list(failed)[:4])
        more = "等" if len(failed) > 4 else ""
        note = f"这次 {names}{more} 没连上，中文结果只覆盖其余站点。" + note
    elif notes and not rows:
        note = notes[0]
    return {"hits": rows[:cap], "note": note, "failed_sources": failed, "per_site": per_site}
