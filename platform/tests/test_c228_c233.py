"""C228–C233：图表提问、调研简报、习惯、上下游、路线。"""

from fengsuan.app.schemas.common import DirectionPatch
from fengsuan.app.services.agent.routing import classify_question
from fengsuan.app.services.sweep import build_sweep_brief


def test_c229_brief_keeps_line_timeline_question_and_coverage():
    brief = build_sweep_brief(
        {
            "topic": "水印",
            "hits": [
                {"title": "组里这篇", "year": 2024, "source": "lab"},
                {"title": "外面这篇", "year": 2023, "source": "openalex"},
            ],
            "themes": [{"name": "屏幕水印", "papers": ["组里这篇", "外面这篇"]}],
            "counts": {"by_source": {"openalex": 1, "lab": 1}, "found": 2, "stored": 2},
            "notes": ["有个来源没连上（Timeout）"],
        }
    )
    assert brief["lines"][0]["name"] == "屏幕水印"
    assert brief["timeline"][0]["year"] == "2023"
    assert brief["open_questions"][0]["source"] == "组里这篇"
    assert brief["in_lab"][0]["title"] == "组里这篇"
    assert brief["coverage"]["failed"]
    assert "openalex" in brief["coverage"]["sources"]


def test_c231_upstream_routes_to_landscape():
    assert classify_question("这篇的上下游论文有哪些") == "landscape"


def test_c233_direction_patch_accepts_route():
    body = DirectionPatch(route=[{"stage": "入门", "title": "A Survey", "item_id": "abc"}])
    assert body.route[0]["title"] == "A Survey"
