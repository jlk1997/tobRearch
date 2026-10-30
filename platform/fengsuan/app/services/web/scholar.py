"""谷歌学术。有 SerpApi 密钥时优先走接口；没有或接口失败时，再用海外代理直抓。"""

from __future__ import annotations

import os
import random
import re
import time
from urllib.parse import urljoin

import httpx

from fengsuan.app.services.web.common import blocked_reason, cache_get, cache_put

_COOLDOWN_UNTIL = 0.0
_COOLDOWN_STEP = 0
_PROXY_I = 0
_LAST = 0.0
_SESSION_LEFT: dict[str, int] = {}
_SERP_LEFT: dict[str, int] = {}

_CAPTCHA = ("unusual traffic", "gs_captcha", "/sorry/", "recaptcha")


def scholar_cooldown_until() -> str:
    if _COOLDOWN_UNTIL <= time.time():
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_COOLDOWN_UNTIL))


def _proxies() -> list[str]:
    return [p.strip() for p in (os.environ.get("FENGSUN_SCHOLAR_PROXY") or "").split(",") if p.strip()]


def _mode() -> str:
    raw = (os.environ.get("FENGSUN_SCHOLAR_MODE") or "auto").strip().lower()
    return raw if raw in {"auto", "serpapi", "proxy"} else "auto"


def _serpapi_on() -> bool:
    from fengsuan.app.services.web.serpapi_scholar import serpapi_key

    return _mode() in {"auto", "serpapi"} and bool(serpapi_key())


def _proxy_on() -> bool:
    return _mode() in {"auto", "proxy"} and bool(_proxies())


def _serp_budget(session: str) -> str:
    if not session:
        return ""
    left = _SERP_LEFT.get(session, 8)
    if left <= 0:
        return "这一轮谷歌学术接口已经查了 8 次，其余改用其他源。"
    _SERP_LEFT[session] = left - 1
    return ""


def _serpapi_json(params: dict, *, session: str, transport=None) -> tuple[dict | None, str, bool]:
    """(数据, 说明, 是否改用代理)。"""
    if not _serpapi_on():
        return None, "", _proxy_on()
    limited = _serp_budget(session)
    if limited:
        return None, limited, False
    from fengsuan.app.services.web.serpapi_scholar import serpapi_get

    data, err, retry = serpapi_get(params, transport=transport)
    if data is not None:
        return data, "", False
    if retry and _proxy_on() and _mode() != "serpapi":
        return None, err, True
    return None, err or "谷歌学术接口没有结果。", False


def _interval() -> float:
    try:
        return max(0.0, float(os.environ.get("FENGSUN_SCHOLAR_INTERVAL") or 8))
    except ValueError:
        return 8.0


def _next_proxy() -> str:
    global _PROXY_I
    proxies = _proxies()
    if not proxies:
        return ""
    proxy = proxies[_PROXY_I % len(proxies)]
    _PROXY_I += 1
    return proxy


def _cool_down() -> None:
    global _COOLDOWN_UNTIL, _COOLDOWN_STEP
    waits = (30 * 60, 2 * 3600, 6 * 3600)
    _COOLDOWN_UNTIL = time.time() + waits[min(_COOLDOWN_STEP, len(waits) - 1)]
    _COOLDOWN_STEP += 1


def _budget(session: str) -> str:
    if _COOLDOWN_UNTIL > time.time():
        return "谷歌学术暂时被限流，这次用了其他源。"
    if not _proxies():
        return "还没配谷歌学术代理，这次没查谷歌学术。"
    if session:
        left = _SESSION_LEFT.get(session, 3)
        if left <= 0:
            return "这一轮谷歌学术已经查了 3 次，其余改用其他源。"
        _SESSION_LEFT[session] = left - 1
    return ""


def _get(path: str, *, session: str = "", transport=None) -> tuple[str, str]:
    global _LAST
    note = _budget(session)
    if note:
        return "", note
    gap = _interval()
    wait = gap - (time.time() - _LAST)
    if wait > 0:
        time.sleep(min(wait, gap))
    _LAST = time.time()
    proxy = _next_proxy()
    url = "https://scholar.google.com" + path
    try:
        client_kwargs = {"timeout": 20.0, "follow_redirects": True}
        if transport is not None:
            client_kwargs["transport"] = transport
        elif proxy:
            client_kwargs["proxy"] = proxy
        with httpx.Client(**client_kwargs) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; fengsuan-research)", "Accept-Language": "en"})
    except Exception:
        return "", "谷歌学术这次没连上。"
    text = resp.text or ""
    if resp.status_code == 429 or any(bit in text.lower() for bit in _CAPTCHA):
        _cool_down()
        return "", "谷歌学术暂时被限流，这次用了其他源。"
    if resp.status_code >= 400:
        return "", f"谷歌学术返回 {resp.status_code}。"
    return text, ""


def _int(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def parse_search_html(html: str) -> list[dict]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for item in soup.select("div.gs_r"):
        title_el = item.select_one("h3.gs_rt")
        if title_el is None:
            continue
        link = title_el.find("a", href=True)
        title = title_el.get_text(" ", strip=True)
        title = re.sub(r"^\[(PDF|HTML|BOOK|CITATION)\]\s*", "", title, flags=re.I).strip()
        info = item.select_one("div.gs_a")
        info_text = info.get_text(" ", strip=True) if info else ""
        year = None
        matched = re.search(r"(19|20)\d{2}", info_text)
        if matched:
            year = int(matched.group(0))
        snippet = item.select_one("div.gs_rs")
        cites = item.find("a", href=re.compile(r"cites="))
        versions = item.find("a", href=re.compile(r"cluster="))
        pdf = item.select_one("div.gs_or_ggsm a")
        cites_href = cites.get("href") if cites else ""
        cluster_href = versions.get("href") if versions else ""
        cites_id = ""
        cluster_id = ""
        if cites_href:
            found = re.search(r"cites=(\d+)", cites_href)
            cites_id = found.group(1) if found else ""
        if cluster_href:
            found = re.search(r"cluster=(\d+)", cluster_href)
            cluster_id = found.group(1) if found else ""
        out.append(
            {
                "title": title[:500],
                "url": (link.get("href") if link else "")[:1000],
                "authors_line": info_text[:300],
                "year": year,
                "snippet": snippet.get_text(" ", strip=True)[:500] if snippet else "",
                "citation_count": _int(cites.get_text(" ", strip=True) if cites else ""),
                "cites_id": cites_id,
                "cluster_id": cluster_id,
                "pdf_url": (pdf.get("href") if pdf else "")[:500],
                "source": "scholar",
            }
        )
    return out


def parse_author_html(html: str) -> dict:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    name = soup.select_one("#gsc_prf_in")
    aff = soup.select_one(".gsc_prf_il")
    email = soup.select_one("#gsc_prf_ivh")
    email_text = email.get_text(" ", strip=True) if email else ""
    domain = ""
    found = re.search(r"@([A-Za-z0-9.\-]+)", email_text) or re.search(
        r"\bat\s+([A-Za-z0-9.\-]+\.[A-Za-z]{2,})", email_text, re.I
    )
    if found:
        domain = found.group(1)
    stats = []
    for cell in soup.select("#gsc_rsb_st td.gsc_rsb_std"):
        stats.append(_int(cell.get_text()))
    years = []
    for bar in soup.select(".gsc_g_a"):
        year = bar.get("href") or bar.get_text()
        found = re.search(r"(19|20)\d{2}", str(year))
        years.append(int(found.group(0)) if found else None)
    papers = []
    for row in soup.select("tr.gsc_a_tr"):
        title = row.select_one("a.gsc_a_at")
        if title is None:
            continue
        cited = row.select_one("a.gsc_a_ac")
        year_el = row.select_one("span.gsc_a_h")
        papers.append(
            {
                "title": title.get_text(" ", strip=True)[:500],
                "url": urljoin("https://scholar.google.com", title.get("href") or ""),
                "citation_count": _int(cited.get_text() if cited else ""),
                "year": _int(year_el.get_text() if year_el else "") or None,
                "source": "scholar",
            }
        )
    interests = [a.get_text(" ", strip=True) for a in soup.select("a.gsc_prf_inta")]
    return {
        "name": name.get_text(" ", strip=True) if name else "",
        "affiliation": aff.get_text(" ", strip=True)[:300] if aff else "",
        "email_domain": domain,
        "citations": stats[0] if len(stats) > 0 else 0,
        "citations_5y": stats[1] if len(stats) > 1 else 0,
        "h_index": stats[2] if len(stats) > 2 else 0,
        "h_index_5y": stats[3] if len(stats) > 3 else 0,
        "i10": stats[4] if len(stats) > 4 else 0,
        "i10_5y": stats[5] if len(stats) > 5 else 0,
        "interests": interests[:8],
        "papers": papers[:20],
        "source": "scholar",
        "as_of": time.strftime("%Y-%m-%d", time.gmtime()),
    }


def _missing_note() -> str:
    return "还没配谷歌学术。可以填 SerpApi 密钥，或配海外代理。"


def _finish_proxy(html: str, proxy_note: str, serp_note: str, parse) -> dict:
    if html:
        papers = parse(html)
        note = f"{serp_note}改用了代理。" if serp_note else proxy_note
        return {"papers": papers, "note": note, "via": "proxy"}
    return {"papers": [], "note": proxy_note or serp_note or _missing_note()}


def scholar_search(query: str, *, ylo: int | None = None, yhi: int | None = None, session: str = "", transport=None) -> dict:
    q = (query or "").strip()
    if not q:
        return {"papers": [], "note": "没有检索词"}
    key = f"q:{q}:{ylo or ''}:{yhi or ''}"
    cached = cache_get("scholar", key, 3 * 86400)
    if cached:
        cached["cached"] = True
        return cached
    from fengsuan.app.services.web.serpapi_scholar import parse_serpapi_search

    params = {"engine": "google_scholar", "q": q[:180], "num": 10}
    if ylo:
        params["as_ylo"] = int(ylo)
    if yhi:
        params["as_yhi"] = int(yhi)
    data, note, try_proxy = _serpapi_json(params, session=session, transport=transport)
    if data is not None:
        papers = parse_serpapi_search(data)
        out = {"papers": papers, "note": "", "via": "serpapi"}
        if papers:
            cache_put("scholar", key, out)
        return out
    if not try_proxy:
        return {"papers": [], "note": note or _missing_note()}
    from urllib.parse import urlencode

    html_params = {"q": q[:180], "hl": "en", "as_sdt": "0,5"}
    if ylo:
        html_params["as_ylo"] = int(ylo)
    if yhi:
        html_params["as_yhi"] = int(yhi)
    html, proxy_note = _get("/scholar?" + urlencode(html_params), session=session, transport=transport)
    out = _finish_proxy(html, proxy_note, note, parse_search_html)
    if out["papers"]:
        cache_put("scholar", key, {"papers": out["papers"], "note": out["note"]})
    return out


def scholar_cites(cites_id: str, *, session: str = "", transport=None) -> dict:
    cid = re.sub(r"\D", "", cites_id or "")
    if not cid:
        return {"papers": [], "note": "没有被引编号"}
    cached = cache_get("scholar", f"cites:{cid}", 3 * 86400)
    if cached:
        return cached
    from fengsuan.app.services.web.serpapi_scholar import parse_serpapi_search

    data, note, try_proxy = _serpapi_json(
        {"engine": "google_scholar", "cites": cid, "num": 10}, session=session, transport=transport
    )
    if data is not None:
        out = {"papers": parse_serpapi_search(data), "note": "", "via": "serpapi"}
        if out["papers"]:
            cache_put("scholar", f"cites:{cid}", out)
        return out
    if not try_proxy:
        return {"papers": [], "note": note or _missing_note()}
    html, proxy_note = _get(f"/scholar?cites={cid}&hl=en", session=session, transport=transport)
    out = _finish_proxy(html, proxy_note, note, parse_search_html)
    if out["papers"]:
        cache_put("scholar", f"cites:{cid}", {"papers": out["papers"], "note": out["note"]})
    return out


def scholar_versions(cluster_id: str, *, session: str = "", transport=None) -> dict:
    cid = re.sub(r"\D", "", cluster_id or "")
    if not cid:
        return {"papers": [], "note": "没有版本编号"}
    from fengsuan.app.services.web.serpapi_scholar import parse_serpapi_search

    data, note, try_proxy = _serpapi_json(
        {"engine": "google_scholar", "cluster": cid, "num": 10}, session=session, transport=transport
    )
    if data is not None:
        return {"papers": parse_serpapi_search(data), "note": "", "via": "serpapi"}
    if not try_proxy:
        return {"papers": [], "note": note or _missing_note()}
    html, proxy_note = _get(f"/scholar?cluster={cid}&hl=en", session=session, transport=transport)
    return _finish_proxy(html, proxy_note, note, parse_search_html)


def scholar_author_search(name: str, *, session: str = "", transport=None) -> dict:
    q = (name or "").strip()
    if len(q) < 2:
        return {"authors": [], "note": "名字太短"}
    from fengsuan.app.services.web.serpapi_scholar import parse_serpapi_profiles

    data, note, try_proxy = _serpapi_json(
        {"engine": "google_scholar_profiles", "mauthors": q[:80]}, session=session, transport=transport
    )
    if data is not None:
        return {"authors": parse_serpapi_profiles(data), "note": "", "via": "serpapi"}
    if not try_proxy:
        return {"authors": [], "note": note or _missing_note()}
    from urllib.parse import urlencode

    html, proxy_note = _get(
        "/citations?" + urlencode({"view_op": "search_authors", "mauthors": q[:80], "hl": "en"}),
        session=session,
        transport=transport,
    )
    if not html:
        return {"authors": [], "note": proxy_note or note or _missing_note()}
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    authors = []
    for item in soup.select(".gsc_1usr, .gs_ai"):
        link = item.select_one("h3 a")
        aff = item.select_one(".gs_ai_aff")
        href = link.get("href") if link else ""
        found = re.search(r"user=([^&]+)", href or "")
        authors.append(
            {
                "name": link.get_text(" ", strip=True) if link else "",
                "scholar_id": found.group(1) if found else "",
                "affiliation": aff.get_text(" ", strip=True) if aff else "",
                "url": urljoin("https://scholar.google.com", href or ""),
                "source": "scholar",
            }
        )
    return {
        "authors": [a for a in authors if a["name"]],
        "note": f"{note}改用了代理。" if note else proxy_note,
        "via": "proxy",
    }


def scholar_author(user_id: str, *, session: str = "", transport=None) -> dict:
    uid = re.sub(r"[^A-Za-z0-9_\-]", "", user_id or "")
    if not uid:
        return {"note": "没有作者编号"}
    cached = cache_get("scholar", f"author:{uid}", 7 * 86400)
    if cached:
        return cached
    from fengsuan.app.services.web.serpapi_scholar import parse_serpapi_author

    data, note, try_proxy = _serpapi_json(
        {"engine": "google_scholar_author", "author_id": uid, "num": 20}, session=session, transport=transport
    )
    if data is not None:
        profile = parse_serpapi_author(data)
        profile["scholar_id"] = uid
        profile["url"] = f"https://scholar.google.com/citations?user={uid}"
        profile["note"] = ""
        profile["via"] = "serpapi"
        if profile.get("name"):
            cache_put("scholar", f"author:{uid}", profile)
        return profile
    if not try_proxy:
        return {"note": note or _missing_note()}
    html, proxy_note = _get(f"/citations?user={uid}&hl=en&cstart=0&pagesize=20", session=session, transport=transport)
    if not html:
        return {"note": proxy_note or note or _missing_note()}
    profile = parse_author_html(html)
    profile["scholar_id"] = uid
    profile["url"] = f"https://scholar.google.com/citations?user={uid}"
    profile["note"] = f"{note}改用了代理。" if note else proxy_note
    profile["via"] = "proxy"
    if profile.get("name"):
        cache_put("scholar", f"author:{uid}", profile)
    return profile


def reset_session(session: str) -> None:
    if session:
        _SESSION_LEFT[session] = 3
        _SERP_LEFT[session] = 8


def reset_scholar_state() -> None:
    global _COOLDOWN_UNTIL, _COOLDOWN_STEP, _PROXY_I, _LAST
    _COOLDOWN_UNTIL = 0.0
    _COOLDOWN_STEP = 0
    _PROXY_I = 0
    _LAST = 0.0
    _SESSION_LEFT.clear()
    _SERP_LEFT.clear()
