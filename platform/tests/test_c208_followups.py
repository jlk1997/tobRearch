"""C208–C210：答完追问像科研助手，并把短名单收进组里。"""

from fengsuan.app.services.agent.loop import _followups
from fengsuan.app.services.agent.understand import recipe_for


def test_frontier_followups_guess_the_next_read():
    chips = _followups(
        "帮我检索一下图片水印方向的前沿论文",
        [],
        [],
        [],
        {"topic_zh": "图片水印", "scope": "outside", "wants": ["recent"], "inferred": []},
        "frontier",
    )
    assert chips
    assert len(chips) <= 3
    joined = " ".join(chips)
    assert "谁在读" not in joined
    assert "收进组里" in joined
    assert "经典" in joined or "作者" in joined or "综述" in joined


def test_who_cites_uses_the_remembered_paper():
    got = recipe_for(
        "谁引用了这篇",
        {"papers": [{"n": 1, "title": "Robust Image Watermarking", "doi": "10.1000/a", "openalex_id": "W1"}]},
    )
    assert got[0][0] == "get_paper_landscape"
    assert got[0][1]["doi"] == "10.1000/a"
    assert got[0][1]["page"] == 1


def test_next_citing_page_advances():
    got = recipe_for(
        "下一批引用",
        {"papers": [{"n": 1, "title": "Robust Image Watermarking", "doi": "10.1000/a"}], "citing_page": 1},
    )
    assert got[0][0] == "get_paper_landscape"
    assert got[0][1]["page"] == 2
    assert got[0][1]["doi"] == "10.1000/a"
    got = recipe_for(
        "下一批",
        {"person": {"name": "秦川", "openalex_id": "A9"}, "works_page": 1},
    )
    assert got[0][0] == "scholar_person_profile"
    assert got[0][1]["page"] == 2
    assert got[0][1]["openalex_id"] == "A9"


def test_cited_followup_sorts_by_citations():
    got = recipe_for(
        "秦川被引最高的论文",
        {"person": {"name": "秦川", "openalex_id": "A9"}},
    )
    assert got[0][0] == "scholar_person_profile"
    assert got[0][1]["sort"] == "cited_by_count:desc"


def test_classic_followup_focuses_the_frontier():
    got = recipe_for("图片水印再给几篇奠基经典", {"topic": "图片水印"})
    front = next(args for name, args in got if name == "get_direction_frontier")
    assert front.get("focus") == "classic"
    assert front.get("topic") == "图片水印"
    memory = {
        "papers": [
            {"n": 1, "title": "Robust Image Watermarking", "doi": "10.1000/a", "openalex_id": "W1"},
            {"n": 2, "title": "Another Watermark", "doi": "10.1000/b"},
        ]
    }
    got = recipe_for("把上面这几篇收进组里", memory)
    assert got[0][0] == "import_paper"
    refs = got[0][1]["refs"]
    assert refs[0]["doi"] == "10.1000/a"
    assert len(refs) == 2
