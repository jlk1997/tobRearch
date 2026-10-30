"""引用这篇之后能接着看作者，短名单超过十篇可以再收一批。"""

from fengsuan.app.services.agent.loop import _followups
from fengsuan.app.services.agent.understand import recipe_for
from fengsuan.app.services.web.scholarly import _paper


def test_landscape_author_chip_uses_the_remembered_person():
    got = recipe_for(
        "这篇作者还写过什么",
        {"person": {"name": "李明", "openalex_id": "A9"}, "papers": [{"n": 1, "title": "Watermark", "doi": "10.1000/a"}]},
    )
    assert got[0][0] == "scholar_person_profile"
    assert got[0][1]["openalex_id"] == "A9"
    assert got[0][1]["name"] == "李明"


def test_second_import_skips_the_first_ten():
    papers = [{"n": i, "title": f"P{i}", "doi": f"10.1000/{i}"} for i in range(1, 13)]
    got = recipe_for("再收下一批", {"papers": papers})
    assert got[0][0] == "import_paper"
    refs = got[0][1]["refs"]
    assert [row["title"] for row in refs] == ["P11", "P12"]
    chips = _followups("把上面这几篇收进组里", [], [], [], {}, "import", 12)
    assert chips == ["再收下一批"]
    short = _followups("把上面这几篇收进组里", [], [], [], {}, "import", 4)
    assert short != ["再收下一批"]


def test_author_next_page_is_not_an_import():
    got = recipe_for("下一批", {"person": {"name": "秦川", "openalex_id": "A9"}, "works_page": 1})
    assert got[0][0] == "scholar_person_profile"
    assert got[0][1]["page"] == 2


def test_model_cannot_redirect_the_author_chip():
    got = recipe_for(
        "这篇作者还写过什么",
        {"person": {"name": "李明", "openalex_id": "A9"}},
        {"from_model": True, "intent": "frontier", "topic_zh": "图片水印", "person_name": "图片"},
    )
    assert got[0][0] == "scholar_person_profile"
    assert got[0][1]["openalex_id"] == "A9"


def test_author_chip_is_not_a_biblio_lookup():
    from fengsuan.app.services.agent.registry import stub_plan
    from fengsuan.app.services.agent.routing import classify_question

    assert classify_question("这篇作者还写过什么", {"from_model": True, "intent": "passage"}, {"person": {"name": "李明", "openalex_id": "A9"}}) == "web_person"
    assert "get_paper_biblio" not in stub_plan("lab", "这篇作者还写过什么")
    row = _paper(
        {
            "display_name": "A Paper",
            "id": "https://openalex.org/W1",
            "authorships": [{"author": {"display_name": "李明", "id": "https://openalex.org/A9"}}],
        },
        "openalex",
    )
    assert row["authors"][0] == "李明"
    assert row["author_openalex_id"] == "A9"
