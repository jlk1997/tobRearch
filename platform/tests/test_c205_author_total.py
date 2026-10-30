"""C205：作者作品要报公开库总数，短名单多于两篇。"""

from fengsuan.app.services.agent.loop import _compose_person
from fengsuan.app.services.web.scholarly import work_list, work_total


def test_author_pack_keeps_total():
    pack = {"works": [{"title": "A"}, {"title": "B"}], "total": 300, "page": 1}
    assert work_total(pack) == 300
    assert len(work_list(pack)) == 2
    assert work_list([{"title": "Old"}])[0]["title"] == "Old"


def test_profile_says_how_many_and_lists_more_than_two(monkeypatch):
    from fengsuan.app.services.agent.registry import ToolContext
    from fengsuan.app.services.agent.tools_web import scholar_person_profile

    monkeypatch.setattr(
        "fengsuan.app.services.web.scholarly.openalex_author",
        lambda *_a, **_k: {"name": "Ada", "affiliations": ["某大学"], "works_count": 300, "openalex_id": "A1"},
    )
    monkeypatch.setattr(
        "fengsuan.app.services.web.scholarly.openalex_author_works",
        lambda *_a, **_k: {
            "works": [{"title": f"Paper {i}", "year": 2020 + i, "authors": ["Ada"], "doi": f"10.1000/{i}"} for i in range(1, 6)],
            "total": 300,
            "page": 1,
        },
    )
    ctx = ToolContext(db=None, user_id="u", lab_id="lab", scope="lab", extra={})
    monkeypatch.setattr("fengsuan.app.services.agent.tools_web._member", lambda *_a, **_k: {"id": "u"})
    result = scholar_person_profile(ctx, {"openalex_id": "A1", "name": "Ada"})
    assert "约 300 篇" in result.text
    titles = [src.get("title") for src in result.sources if src.get("kind") != "identity"]
    assert len(titles) >= 3


def test_compose_mentions_the_catalog_size():
    sources = type("S", (), {})()
    sources.items = [
        {
            "kind": "identity",
            "title": "Ada",
            "label": "S1",
            "identity": {"affiliations": ["某大学"], "works_count": 300, "confirmed": False},
        }
    ]
    sources.items += [
        {
            "kind": "external",
            "title": f"Paper {i}",
            "year": 2020 + i,
            "authors": ["Ada"],
            "matched_person": True,
            "label": f"S{i+1}",
            "excerpt": "水印",
        }
        for i in range(1, 5)
    ]
    text = _compose_person("Ada 写过什么", sources, [], {"scope": "outside", "person_name": "Ada"})
    assert "约 300 篇" in text
    assert "Paper 1" in text and "Paper 4" in text
    assert "组里的方向" not in text
