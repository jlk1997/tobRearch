"""C225 按节、C226 引用扩展口径、C227 按目的。"""

from fengsuan.app.services.agent.loop import _research_followups
from fengsuan.app.services.agent.parse import infer_goal, shape_by_goal, validate_turn
from fengsuan.app.services.agent.registry import get_tool
from fengsuan.app.services.reading_tier import section_notes


def test_c225_section_tool_and_empty_notes():
    spec = get_tool("get_paper_section")
    assert spec is not None
    assert "paper" in spec.scopes


def test_c226_expand_note_is_honest(monkeypatch):
    from fengsuan.app.services import sweep

    saved = {}

    def load(_lab, _sid):
        return {"id": "s1", "hits": [{"title": "Seed", "openalex_id": "W1", "doi": "10.1/s"}]}

    def save(_lab, data):
        saved.update(data)

    monkeypatch.setattr(sweep, "load_sweep", load)
    monkeypatch.setattr(sweep, "save_sweep", save)
    monkeypatch.setattr(
        "fengsuan.app.services.web.scholarly.openalex_citing",
        lambda *_a, **_k: [{"title": "Citer", "doi": "10.1/c", "openalex_id": "W2"}],
    )
    monkeypatch.setattr(
        "fengsuan.app.services.web.scholarly.openalex_referenced",
        lambda *_a, **_k: [
            {"title": "Base", "doi": "10.1/b", "openalex_id": "W3", "authors": ["Ada"]},
            {"title": "Base", "doi": "10.1/b", "openalex_id": "W3", "authors": ["Ada"]},
        ],
    )
    out = sweep.sweep_expand("lab", "s1")
    assert out["counts"]["expand_hops"] == 1
    assert out["counts"]["expand_seeds"] == 1
    assert "去重后新增" in out["expand_note"]
    assert out["counts"]["expand_new"] == out["counts"]["expanded"]


def test_c227_goal_shapes_answer_and_chips():
    out = validate_turn(
        "水印入门先读什么",
        [],
        {},
        {"task": "field_reading", "topic": {"zh": "水印"}, "goal": "入门"},
    )
    assert out["goal"] == "入门"
    assert infer_goal("近两年水印有什么新论文", {}) == "跟最新"
    shaped = shape_by_goal(
        "入门",
        "外面有这些。",
        type("S", (), {"items": [{"kind": "external", "title": "A Survey"}]})(),
    )
    assert shaped.startswith("可以分三步读")
    chips = _research_followups("水印入门先读什么", {"goal": "入门", "topic_zh": "水印"}, "frontier")
    assert chips[0] == "下一步读哪一篇"


def test_c225_section_notes_empty_without_db_row():
    class _Q:
        def filter(self, *_a, **_k):
            return self

        def order_by(self, *_a, **_k):
            return self

        def first(self):
            return None

    class _DB:
        def query(self, *_a, **_k):
            return _Q()

    assert section_notes(_DB(), "missing") == []
