"""C153–C154：原生联网、抓取、谷歌学术、中文库。外网全部打桩。"""

from __future__ import annotations

import os

import httpx

from fengsuan.app.services.llm import ResolvedLLM
from fengsuan.app.services.web import cn, fetch, native, scholar, scholarly, search
from fengsuan.app.services.web.common import blocked_reason, strip_contacts


def _llm(base="https://api.minimaxi.com/v1"):
    return ResolvedLLM(base, "sk-test", "MiniMax-M3", "lab", provider="minimax")


def test_native_capability_and_parse():
    cap = native.native_capability(_llm())
    assert cap["kind"] == "minimax"
    assert cap["base"].endswith("/anthropic")
    assert native.native_capability(ResolvedLLM("https://api.openai.com/v1", "k", "gpt", "env")) is None
    parsed = native.parse_minimax_message(
        {
            "content": [
                {"type": "text", "text": "先搜一下"},
                {"type": "server_tool_use", "name": "web_search", "input": {"query": "图神经网络"}},
                {
                    "type": "web_search_tool_result",
                    "content": [
                        {
                            "type": "web_search_result",
                            "title": "一篇论文",
                            "url": "https://arxiv.org/abs/1",
                            "page_age": "2024",
                            "content": "摘要",
                        }
                    ],
                },
                {"type": "text", "text": "搜到了"},
            ]
        }
    )
    assert parsed["queries"] == ["图神经网络"]
    assert parsed["hits"][0]["url"] == "https://arxiv.org/abs/1"
    assert "搜到了" in parsed["text"]


def test_native_failure_falls_back_to_bocha(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if "bochaai" in str(request.url):
            return httpx.Response(
                200,
                json={"data": {"webPages": {"value": [{"name": "博查结果", "url": "https://example.org/a", "snippet": "片段"}]}}},
            )
        return httpx.Response(500, json={})

    monkeypatch.setenv("BOCHA_API_KEY", "bocha-test")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    transport = httpx.MockTransport(handler)
    packed = search.search_web("图神经网络", llm=_llm(), transport=transport)
    assert packed["provider"] == "bocha"
    assert packed["hits"][0]["title"] == "博查结果"
    assert "备用" in (packed.get("note") or "")


def test_search_without_keys_says_so(monkeypatch):
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    packed = search.search_web("随便", llm=None)
    assert packed["hits"] == []
    assert "没开" in packed["note"]


def test_fetch_blocks_private_metadata_and_scihub():
    assert blocked_reason("https://sci-hub.se/10.1/abc")
    denied = fetch.fetch_page("https://sci-hub.se/10.1/abc")
    assert denied["ok"] is False
    private = fetch.fetch_page("http://127.0.0.1/secret")
    assert private["ok"] is False
    meta = fetch.fetch_page("http://169.254.169.254/latest")
    assert meta["ok"] is False


def test_fetch_stops_redirect_to_private_and_robots():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            if request.url.host == "example.org":
                return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        if path == "/go":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        if path == "/secret":
            return httpx.Response(200, text="<html><title>no</title><body><p>should not be read at all here ok</p></body></html>")
        return httpx.Response(200, text="<html><title>Hi</title><body><p>hello from the public page body</p></body></html>")

    os.environ["FENGSUN_FETCH_MIN_INTERVAL"] = "0"
    transport = httpx.MockTransport(handler)
    jumped = fetch.fetch_page("https://example.com/go", transport=transport)
    assert jumped["ok"] is False
    blocked = fetch.fetch_page("https://example.org/secret", transport=transport)
    assert blocked["ok"] is False
    assert "robots" in blocked["error"]


def test_fetch_strips_contacts_and_reads_text():
    os.environ["FENGSUN_FETCH_MIN_INTERVAL"] = "0"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        html = "<html><title>主页</title><body><p>联系 mail@example.com 或 13800138000 欢迎合作交流访问实验室。</p></body></html>"
        return httpx.Response(200, text=html)

    page = fetch.fetch_page("https://example.com/home", strip=True, transport=httpx.MockTransport(handler))
    assert page["ok"] is True
    assert "mail@" not in page["text"]
    assert "13800138000" not in page["text"]
    assert "已隐去" in strip_contacts("a@b.com")


def test_scholar_parse_and_captcha(monkeypatch):
    html = """
    <div class="gs_r"><div class="gs_ri">
      <h3 class="gs_rt"><a href="https://example.org/p">Graph Nets</a></h3>
      <div class="gs_a">A Person - Venue, 2021 - example.org</div>
      <div class="gs_rs">a snippet about graphs</div>
      <div class="gs_fl"><a href="/scholar?cites=99">Cited by 12</a>
      <a href="/scholar?cluster=77">All 3 versions</a></div>
    </div></div>
    """
    papers = scholar.parse_search_html(html)
    assert papers[0]["title"] == "Graph Nets"
    assert papers[0]["citation_count"] == 12
    assert papers[0]["cites_id"] == "99"
    assert papers[0]["cluster_id"] == "77"
    profile = scholar.parse_author_html(
        """
        <div id="gsc_prf_in">Ada</div>
        <div class="gsc_prf_il">某大学</div>
        <div id="gsc_prf_ivh">Verified email at uni.edu</div>
        <table id="gsc_rsb_st">
          <tr><td class="gsc_rsb_std">100</td><td class="gsc_rsb_std">40</td></tr>
          <tr><td class="gsc_rsb_std">8</td><td class="gsc_rsb_std">5</td></tr>
          <tr><td class="gsc_rsb_std">6</td><td class="gsc_rsb_std">3</td></tr>
        </table>
        """
    )
    assert profile["email_domain"] == "uni.edu"
    assert profile["h_index"] == 8
    assert "＠" not in str(profile) and "@" not in profile["email_domain"].join(["", ""]) or profile["email_domain"] == "uni.edu"

    scholar.reset_scholar_state()
    monkeypatch.delenv("FENGSUN_SERPAPI_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setenv("FENGSUN_SCHOLAR_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("FENGSUN_SCHOLAR_INTERVAL", "0")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>unusual traffic detected</html>")

    out = scholar.scholar_search("gnn", session="t-cap", transport=httpx.MockTransport(handler))
    assert "限流" in out["note"]
    assert scholar.scholar_cooldown_until()
    again = scholar.scholar_search("gnn2", session="t-cap2", transport=httpx.MockTransport(handler))
    assert "限流" in again["note"]
    scholar.reset_scholar_state()


def test_serpapi_scholar_is_preferred_and_can_fall_back(monkeypatch):
    monkeypatch.setenv("FENGSUN_SERPAPI_KEY", "test-key")
    monkeypatch.setenv("FENGSUN_SCHOLAR_INTERVAL", "0")
    monkeypatch.delenv("FENGSUN_SCHOLAR_PROXY", raising=False)
    scholar.reset_scholar_state()

    def handler(request: httpx.Request) -> httpx.Response:
        engine = request.url.params.get("engine")
        assert request.url.params.get("api_key") == "test-key"
        if engine == "google_scholar" and request.url.params.get("q") == "graph nets":
            return httpx.Response(
                200,
                json={
                    "organic_results": [
                        {
                            "title": "Graph Nets",
                            "link": "https://example.org/p",
                            "snippet": "graphs",
                            "publication_info": {"summary": "A Person - Venue, 2021"},
                            "inline_links": {
                                "cited_by": {"total": 12, "cites_id": "99"},
                                "versions": {"cluster_id": "77"},
                            },
                            "resources": [{"file_format": "PDF", "link": "https://example.org/p.pdf"}],
                        }
                    ]
                },
            )
        if engine == "google_scholar_profiles":
            return httpx.Response(
                200,
                json={"profiles": [{"name": "Ada", "author_id": "abc", "affiliations": "某大学", "link": "https://scholar.google.com/citations?user=abc"}]},
            )
        if engine == "google_scholar_author":
            return httpx.Response(
                200,
                json={
                    "author": {
                        "name": "Ada",
                        "affiliations": "某大学",
                        "email": "Verified email at uni.edu",
                        "interests": [{"title": "graphs"}],
                        "cited_by": {
                            "table": [
                                {"citations": {"all": 100, "since_2021": 40}},
                                {"h_index": {"all": 8, "since_2021": 5}},
                                {"i10_index": {"all": 6, "since_2021": 3}},
                            ]
                        },
                    },
                    "articles": [{"title": "Graph Nets", "link": "https://example.org/p", "publication": "Venue, 2021", "cited_by": {"value": 12}}],
                },
            )
        return httpx.Response(429, json={"error": "limit"})

    transport = httpx.MockTransport(handler)
    found = scholar.scholar_search("graph nets", session="serp", transport=transport)
    assert found["via"] == "serpapi"
    assert found["papers"][0]["cites_id"] == "99"
    assert found["papers"][0]["pdf_url"].endswith(".pdf")
    people = scholar.scholar_author_search("Ada Lovelace", session="serp-people", transport=transport)
    assert people["authors"][0]["scholar_id"] == "abc"
    profile = scholar.scholar_author("abc", session="serp-author", transport=transport)
    assert profile["h_index"] == 8
    assert profile["email_domain"] == "uni.edu"
    assert profile["via"] == "serpapi"

    monkeypatch.setenv("FENGSUN_SCHOLAR_PROXY", "http://proxy.example:8080")

    def fallback(request: httpx.Request) -> httpx.Response:
        if "serpapi.com" in str(request.url):
            return httpx.Response(429, json={"error": "limit"})
        return httpx.Response(
            200,
            text="""<div class="gs_r"><div class="gs_ri"><h3 class="gs_rt"><a href="https://example.org/p">Proxy Paper</a></h3>
            <div class="gs_a">A Person - Venue, 2020</div></div></div>""",
        )

    backed = scholar.scholar_search("proxy fallback paper", session="serp-fallback", transport=httpx.MockTransport(fallback))
    assert backed["via"] == "proxy"
    assert backed["papers"][0]["title"] == "Proxy Paper"
    assert "改用了代理" in backed["note"]

    monkeypatch.delenv("FENGSUN_SERPAPI_KEY", raising=False)
    monkeypatch.delenv("FENGSUN_SCHOLAR_PROXY", raising=False)
    scholar.reset_scholar_state()
    missing = scholar.scholar_search("scholar-not-configured", session="none")
    assert "还没配谷歌学术" in missing["note"]
    scholar.reset_scholar_state()


def test_cn_detail_keeps_abstract_and_open_pdf():
    html = """
    <html><head>
      <meta name="citation_title" content="中文论文">
      <meta name="citation_author" content="张三">
      <meta name="citation_abstract" content="这是摘要">
    </head><body>被引：4 下载：9 <a href="https://chinaxiv.org/a.pdf">pdf</a></body></html>
    """
    row = cn.parse_cn_html(html, "https://chinaxiv.org/abs/1")
    assert row["title"] == "中文论文"
    assert row["abstract"] == "这是摘要"
    assert row["cited_by"] == 4
    assert row["pdf_url"].endswith(".pdf")
    assert row["fulltext"] == "open"


def test_openalex_author_shape(monkeypatch):
    def fake(url, params=None, *, host):
        assert host == "openalex"
        return {
            "results": [
                {
                    "id": "https://openalex.org/A1",
                    "display_name": "Ada Lovelace",
                    "ids": {"orcid": "https://orcid.org/0000-0001-0002-0003"},
                    "last_known_institutions": [{"display_name": "某实验室"}],
                    "summary_stats": {"h_index": 3},
                    "cited_by_count": 10,
                    "works_count": 2,
                    "topics": [{"display_name": "graphs"}],
                }
            ]
        }

    monkeypatch.setattr(scholarly, "_json", fake)
    rows = scholarly.openalex_search_authors("Ada")
    assert rows[0]["openalex_id"] == "A1"
    assert rows[0]["orcid"] == "0000-0001-0002-0003"
    assert rows[0]["h_index"] == 3
    assert rows[0]["affiliations"] == ["某实验室"]


def test_open_pdf_keeps_known_url_when_lookup_fails(monkeypatch):
    def boom(_doi):
        raise RuntimeError("down")

    monkeypatch.setattr("fengsuan.app.services.web.scholarly.unpaywall", boom)
    from fengsuan.app.services.paper_import import find_open_pdf

    known = find_open_pdf({"title": "T", "doi": "10.1000/known", "pdf_url": "https://example.org/paper.pdf"})
    assert known["pdf_url"] == "https://example.org/paper.pdf"
    arxiv = find_open_pdf({"title": "T", "arxiv_id": "2401.00001", "doi": "10.1000/ax"})
    assert arxiv["pdf_url"] == "https://arxiv.org/pdf/2401.00001.pdf"
    assert arxiv["via"] == "arxiv"
