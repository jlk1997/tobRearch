"""安全抓网页。每一跳都重新校验，遵守 robots，不碰盗版站。"""

from __future__ import annotations

import os
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.core.netguard import assert_http_url
from fengsuan.app.services.web.common import blocked_reason, contact_email, host_of, strip_contacts

_ROBOTS: dict[str, tuple[float, RobotFileParser | None]] = {}
_LAST: dict[str, float] = {}
_MAX_BYTES = 2 * 1024 * 1024
_UA = "fengsuan-research"


def _interval() -> float:
    try:
        return max(0.0, float(os.environ.get("FENGSUN_FETCH_MIN_INTERVAL") or 1))
    except ValueError:
        return 1.0


def _robots_allowed(url: str, client: httpx.Client) -> bool:
    host = host_of(url)
    if not host:
        return False
    now = time.time()
    cached = _ROBOTS.get(host)
    parser = cached[1] if cached and now - cached[0] < 86400 else None
    if parser is None:
        robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
        parser = RobotFileParser()
        try:
            resp = client.get(robots_url, headers={"User-Agent": _UA})
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
            else:
                parser.parse([])
        except Exception:
            parser.parse([])
        _ROBOTS[host] = (now, parser)
    try:
        return parser.can_fetch(_UA, url)
    except Exception:
        return True


def _wait(host: str) -> None:
    gap = _interval()
    if gap <= 0 or not host:
        return
    last = _LAST.get(host, 0)
    wait = gap - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    _LAST[host] = time.time()


def _text_from_html(html: str) -> tuple[str, str, list[str]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:300]
    chunks = []
    for node in soup.find_all(["p", "h1", "h2", "h3", "li"]):
        line = node.get_text(" ", strip=True)
        if len(line) >= 20:
            chunks.append(line)
    if not chunks:
        body = soup.get_text("\n", strip=True)
        chunks = [line.strip() for line in body.splitlines() if len(line.strip()) >= 20]
    links = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        if href.startswith("http"):
            links.append(href[:500])
        if len(links) >= 12:
            break
    return title, "\n".join(chunks)[:6000], links


def fetch_page(url: str, *, strip: bool = False, transport=None, _hops: int = 0) -> dict:
    raw = (url or "").strip()
    denied = blocked_reason(raw)
    if denied:
        return {"ok": False, "error": "这个站点不提供，已拒绝", "url": raw, "blocked": denied}
    try:
        clean = assert_http_url(raw, what="网页", allow_private=False)
    except ClassifiedHTTPException as exc:
        return {"ok": False, "error": str(exc.detail), "url": raw}
    if _hops == 0:
        _wait(host_of(clean))
    headers = {"User-Agent": f"{_UA} (mailto:{contact_email()})", "Accept": "text/html,application/xhtml+xml"}
    try:
        with httpx.Client(timeout=12.0, transport=transport, follow_redirects=False) as client:
            if not _robots_allowed(clean, client):
                return {"ok": False, "error": "robots 不允许", "url": clean}
            resp = client.get(clean, headers=headers)
    except Exception as exc:
        return {"ok": False, "error": f"打不开：{type(exc).__name__}", "url": clean}
    if resp.status_code in {301, 302, 303, 307, 308}:
        loc = resp.headers.get("location") or ""
        if _hops >= 3 or not loc:
            return {"ok": False, "error": "跳转太多次", "url": clean}
        return fetch_page(urljoin(clean, loc), strip=strip, transport=transport, _hops=_hops + 1)
    if resp.status_code >= 400:
        return {"ok": False, "error": f"网页返回 {resp.status_code}", "url": clean}
    content = resp.content[:_MAX_BYTES]
    ctype = (resp.headers.get("content-type") or "").lower()
    if "pdf" in ctype or content[:5] == b"%PDF":
        return {
            "ok": True,
            "url": str(resp.url),
            "site": host_of(str(resp.url)),
            "title": host_of(str(resp.url)),
            "text": "",
            "pdf": True,
            "links": [],
            "paper": None,
        }
    html = content.decode("utf-8", errors="ignore")
    title, text, links = _text_from_html(html)
    paper = None
    try:
        from fengsuan.app.services.capture import parse_paper_html

        parsed = parse_paper_html(html, str(resp.url))
        if parsed.get("doi") or parsed.get("cnki_id") or (parsed.get("title") and parsed.get("authors")):
            paper = parsed
            if parsed.get("title"):
                title = parsed["title"]
    except Exception:
        paper = None
    if strip:
        text = strip_contacts(text)
        title = strip_contacts(title)
    return {
        "ok": True,
        "url": str(resp.url),
        "site": host_of(str(resp.url)),
        "title": title,
        "text": text,
        "pdf": False,
        "links": links,
        "paper": paper,
        "error": "",
    }
