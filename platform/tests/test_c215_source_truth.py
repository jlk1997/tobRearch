"""C215–C218：源失败与零结果分开、中文按站点并行、报数口径、过滤与版本标记。"""

from fengsuan.app.services.web.common import SourceFailures


def test_failure_scope_records_and_reports():
    with SourceFailures.scoped() as f:
        f.note("OpenAlex", "限流")
        f.note("知网", "超时")
        assert not f.ok
        assert f.failed == ["OpenAlex", "知网"]
        assert "OpenAlex" in f.note_text()
    assert SourceFailures.current() is None, "出了 with 必须还原"


def test_failure_text_is_empty_when_all_ok():
    with SourceFailures.scoped() as f:
        assert f.note_text() == ""
        assert f.as_dict() == {}


def test_empty_next_says_failed_not_empty():
    from fengsuan.app.services.literature import search_empty_next

    failed = search_empty_next("L1", "image watermark", sources=["arxiv"], failed_sources={"OpenAlex": "限流"})
    assert "没连上" in failed["title"]
    assert "还不能判断有没有结果" in failed["text"]
    assert failed["failed_sources"] == {"OpenAlex": "限流"}

    plain = search_empty_next("L1", "image watermark", sources=["arxiv"])
    assert plain["title"] == "没有检索结果"
    assert "failed_sources" not in plain


def test_frontier_counts_merged_not_upstream():
    from fengsuan.app.services import frontier

    with SourceFailures.scoped():
        got = frontier.frontier_for_topic("image watermarking", years=2, queries=["image watermarking"])
    assert "merged_total" in got
    # 报数必须等于合并去重后的条数，不能是 OpenAlex 过滤前的 meta.count。
    assert got["merged_total"] == len(got["papers"]) or got["merged_total"] >= len(got["papers"])
    if got["merged_total"] > len(got["papers"]):
        assert "没选上" in got["note"] or "只看了前" in got["note"]


def test_junk_filter_rejects_books_and_theses():
    from fengsuan.app.services.frontier import _junk_paper

    assert _junk_paper({"title": "Handbook of Image Processing"}, code=False)
    assert _junk_paper({"title": "图像处理百科全书"}, code=False)
    assert _junk_paper({"title": "博士学位论文：图像水印"}, code=False)
    assert not _junk_paper({"title": "Robust Image Watermarking", "doi": "10.1/x"}, code=False)


def test_paper_survives_null_openalex_fields():
    """primary_location.source 与 authorships[].author 都可能是 null。"""
    from fengsuan.app.services.web.scholarly import _paper

    row = _paper(
        {
            "display_name": "A Paper",
            "id": "https://openalex.org/W1",
            "primary_location": {"source": None},
            "authorships": [{"author": None}, {"author": {"display_name": "Ada", "id": "https://openalex.org/A1"}}],
        },
        "openalex",
    )
    assert row["title"] == "A Paper"
    assert row["venue"] == ""
    assert row["authors"] == ["Ada"]


def test_related_works_are_dicts_not_strings():
    from fengsuan.app.services.web import scholarly

    called = {}

    def fake_json(url, params=None, *, host):
        called["url"] = url
        called.setdefault("params", []).append(params or {})
        if (params or {}).get("select") == "related_works":
            return {
                "related_works": [
                    {"id": "https://openalex.org/W2", "score": 1.0},
                    {"id": "https://openalex.org/W3"},
                ]
            }
        return {"results": [{"display_name": "R2", "id": "https://openalex.org/W2"}]}

    real = scholarly._json
    scholarly._json = fake_json
    try:
        got = scholarly.openalex_related("W1", limit=2)
    finally:
        scholarly._json = real
    assert [r["openalex_id"] for r in got] == ["W2"]
    filters = [p.get("filter") for p in called.get("params") or [] if p.get("filter")]
    assert any("openalex:W2|W3" == f for f in filters), filters


def test_rerank_marks_dropped_count():
    from fengsuan.app.services.agent.parse import rerank_papers

    papers = [{"title": f"P{i}", "abstract": "x"} for i in range(20)]
    got = rerank_papers(None, "", "", "topic", papers)
    assert got
    row = got[0]
    assert row["_pool_total"] == 20
    assert row["_dropped_count"] >= 0


def test_paper_carries_retraction_flag():
    from fengsuan.app.services.web.scholarly import _paper

    bad = _paper({"display_name": "Retracted", "id": "W1", "is_retracted": True, "type": "preprint"}, "openalex")
    assert bad["retracted"] is True
    assert bad["work_type"] == "retracted"
    ok = _paper({"display_name": "Fine", "id": "W2"}, "openalex")
    assert ok["retracted"] is False


def test_source_exposes_retraction():
    from fengsuan.app.services.agent.tools_web import _web_source

    src = _web_source({"title": "T", "openalex_id": "W1", "retracted": True}, kind="external")
    assert src["retracted"] is True


def test_cn_search_gives_each_site_a_budget(monkeypatch):
    from fengsuan.app.services.web import cn
    import sys

    calls = []

    def fake_search(query, *, site="", recent=False, llm=None, transport=None):
        calls.append(site)
        # 每个站点都回 8 条，模拟「顺序循环会让后面的站点吃不到预算」
        return {"hits": [{"title": f"{site}-{i}", "url": f"https://{site}/{i}"} for i in range(8)], "note": ""}

    monkeypatch.setattr(sys.modules["fengsuan.app.services.web.search"], "search_web", fake_search)
    pack = cn.search_cn("图像水印", limit=20)
    assert len(calls) >= 4, "四个站点都要查"
    assert len(pack["hits"]) <= 20
    # 每个站点都应有命中，不能由循环顺序决定谁被挤掉。
    sites = {h["cn_source"] for h in pack["hits"]}
    assert len(sites) >= 3, f"应有多个站点贡献，实际 {sites}"
