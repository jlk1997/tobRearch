"""C207：理解层记下综述、经典、前沿和还想收进组里。"""

from fengsuan.app.services.agent.parse import validate_turn
from fengsuan.app.services.agent.understand import recipe_for, view_from_turn


def test_wants_cover_review_classic_and_ingest():
    out = validate_turn(
        "图片水印有哪些经典综述",
        [],
        {},
        {
            "task": "field_reading",
            "scope": "outside",
            "topic": {"zh": "图片水印"},
            "wants": ["reviews", "classic", "ingest", "not-a-want"],
            "inferred": ["再看看近两年代表作", "这个方向有哪些人"],
            "years": None,
        },
    )
    assert set(out["wants"]) >= {"reviews", "classic", "ingest"}
    assert "not-a-want" not in out["wants"]
    assert out["inferred"][0].startswith("再看看")
    assert out["years"] is None


def test_recent_frontier_sets_two_years_and_skips_lab():
    out = validate_turn(
        "图片水印方向的前沿论文",
        [],
        {},
        {"task": "field_reading", "topic": {"zh": "图片水印"}, "wants": ["recent"]},
    )
    view = view_from_turn("图片水印方向的前沿论文", None, out)
    assert view["years"] == 2
    assert view["scope"] == "outside"
    assert view["wants_recent"] is True
    steps = recipe_for("图片水印方向的前沿论文", None, out)
    assert steps[0][0] == "get_direction_frontier"
    assert "get_direction_tree" not in {name for name, _ in steps}
