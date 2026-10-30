"""C206：组外问题不把实验室方向写进答案，也不去查方向树。"""

from fengsuan.app.services.agent.loop import _compose_field
from fengsuan.app.services.agent.parse import asks_outside_field, validate_turn
from fengsuan.app.services.agent.understand import recipe_for


def test_frontier_question_is_outside():
    assert asks_outside_field("帮我检索一下图片水印方向的前沿论文")
    assert not asks_outside_field("组里图片水印方向有哪些论文")


def test_outside_recipe_skips_the_lab_tree():
    turn = {
        "from_model": True,
        "intent": "frontier",
        "topic_zh": "图片水印",
        "scope": "outside",
        "years": 2,
        "wants": ["papers", "recent"],
    }
    steps = recipe_for("帮我检索一下图片水印方向的前沿论文，推荐几篇", None, turn)
    names = [name for name, _args in steps]
    assert "get_direction_frontier" in names
    assert "get_direction_tree" not in names
    assert "find_lab_expert" not in names
    assert "search_lab_literature" not in names


def test_outside_field_answer_does_not_name_a_lab_direction():
    sources = type("S", (), {})()
    sources.items = [
        {"kind": "direction", "title": "Camera Resilient Watermarking", "label": "S9", "excerpt": "组内方向"},
        {
            "kind": "external",
            "title": "Robust Image Watermarking",
            "label": "S1",
            "year": 2024,
            "shelf": "近两年代表",
            "why": "抗旋转",
            "excerpt": "抗旋转水印",
        },
    ]
    text = _compose_field(
        "帮我检索一下图片水印方向的前沿论文",
        sources,
        [{"tool": "get_direction_frontier", "args": {"topic": "图片水印"}}],
        {"scope": "outside", "topic_zh": "图片水印"},
    )
    assert "Camera Resilient" not in text
    assert "组里已经有方向" not in text
    assert "这个人正在做" not in text
    assert "Robust Image Watermarking" in text


def test_validate_marks_frontier_outside():
    out = validate_turn(
        "帮我检索一下图片水印方向的前沿论文",
        [],
        {},
        {"task": "field_reading", "scope": "both", "topic": {"zh": "图片水印"}, "wants": ["recent", "papers"]},
    )
    assert out["scope"] == "outside"
    assert out["topic_zh"] == "图片水印"
    assert "recent" in out["wants"]
    assert out["years"] == 2
