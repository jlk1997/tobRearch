"""C222 篇内检索、C223 版本合并与时效排序、C224 检索计划。"""

from fengsuan.app.services.agent.parse import search_plan_text
from fengsuan.app.services.agent.registry import get_tool
from fengsuan.app.services.analysis import TUTOR_AGENT_SYSTEM_EXTRA
from fengsuan.app.services.biblio import ref_key
from openlens_ai.utils.paper_dedupe import merge_versions


def test_c222_search_in_paper_is_registered():
    spec = get_tool("search_in_paper")
    assert spec is not None
    assert "paper" in spec.scopes
    assert "search_in_paper" in TUTOR_AGENT_SYSTEM_EXTRA


def test_c223_preprint_and_published_merge_and_short_title_keeps_a_key():
    rows = merge_versions(
        [
            {
                "title": "Learned Image Compression",
                "authors": ["Ada Lovelace"],
                "year": 2023,
                "arxiv_id": "2301.00001",
                "venue": "arXiv",
                "citation_count": 3,
            },
            {
                "title": "Learned Image Compression",
                "authors": [{"name": "Ada Lovelace"}],
                "year": 2024,
                "doi": "10.1000/lic",
                "venue": "CVPR",
                "citation_count": 20,
            },
        ]
    )
    assert len(rows) == 1
    assert rows[0]["doi"] == "10.1000/lic"
    assert len(rows[0]["versions"]) == 2
    assert any(bit.get("preprint") for bit in rows[0]["versions"])
    assert ref_key(title="短题", author="Ada Lovelace", year=2024).startswith("ty:")
    assert ref_key(title="A Sufficiently Long Paper Title", author="Ada", year=2020).startswith("t:")


def test_c223_recent_openalex_sorts_by_date(monkeypatch):
    from fengsuan.app.services.web import scholarly

    captured = {}

    def fake(_url, params, host=None):
        captured["sort"] = params.get("sort")
        return {"results": [], "meta": {"count": 0}}

    monkeypatch.setattr(scholarly, "_json", fake)
    scholarly.openalex_search_works("watermark", years=2)
    assert captured["sort"] == "publication_date:desc"
    scholarly.openalex_search_works("watermark")
    assert captured["sort"] == "cited_by_count:desc"


def test_c224_plan_says_steps_and_why():
    text = search_plan_text(
        {
            "summary": "要找水印的近作",
            "plan": {"openalex": ["watermark"], "cnki": ["图像水印"]},
        }
    )
    assert "分 2 步" in text
    assert "要找水印的近作" in text
    assert "OpenAlex" in text
    assert "中文库" in text
    assert search_plan_text({"plan": {}}) == ""
