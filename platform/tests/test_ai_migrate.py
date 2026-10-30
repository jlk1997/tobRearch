"""六个 AI 功能接入 Agent 运行时后，必须留下可复算的工具追踪。"""

from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab_paper(client, h, code: str) -> tuple[str, str]:
    lab = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    item = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Graph convolution networks", "abstract": "We study graph convolution.", "language": "en"},
        headers=h,
    )
    assert item.status_code == 200, item.text
    return lab_id, item.json()["id"]


def _traces(feature: str) -> list[AgentTrace]:
    db = SessionLocal()
    try:
        return db.query(AgentTrace).filter(AgentTrace.feature == feature).all()
    finally:
        db.close()


def test_method_card_records_tool_trace(client):
    h = _h(_admin(client))
    _lab, item_id = _lab_paper(client, h, "aimc")
    r = client.post(f"/api/v1/literature/{item_id}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert r.status_code == 200, r.text
    tools = {t.tool for t in _traces("analysis")}
    assert "get_paper_page_text" in tools


def test_compare_records_tool_trace(client):
    h = _h(_admin(client))
    lab_id, a = _lab_paper(client, h, "aicmp")
    b = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        json={"title": "Attention is all you need", "abstract": "Transformers.", "language": "en"},
        headers=h,
    )
    assert b.status_code == 200, b.text
    r = client.post(f"/api/v1/labs/{lab_id}/literature/compare", json={"item_ids": [a, b.json()["id"]]}, headers=h)
    assert r.status_code == 200, r.text
    tools = {t.tool for t in _traces("compare")}
    assert "get_method_card" in tools or "search_lab_literature" in tools
    assert "get_method_compare" in tools


def test_outline_records_writing_tools(client):
    h = _h(_admin(client))
    lab_id, item_id = _lab_paper(client, h, "aiout")
    r = client.post(
        f"/api/v1/labs/{lab_id}/writing/outline",
        json={"kind": "survey", "title": "GNN", "literature_item_ids": [item_id]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    tools = {t.tool for t in _traces("writing")}
    assert "search_lab_literature" in tools or "get_method_card" in tools
    assert "get_related_work" in tools


def test_section_records_writing_tools(client):
    h = _h(_admin(client))
    lab_id, item_id = _lab_paper(client, h, "aisec")
    r = client.post(
        f"/api/v1/labs/{lab_id}/writing/section",
        json={"heading": "相关工作", "notes": "GNN", "literature_item_ids": [item_id]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    tools = {t.tool for t in _traces("writing")}
    assert "search_lab_kb" in tools or "get_method_card" in tools
    assert "get_related_work" in tools


def test_kb_query_records_tool_trace(client):
    h = _h(_admin(client))
    lab_id, _item = _lab_paper(client, h, "aikb")
    r = client.post(f"/api/v1/labs/{lab_id}/kb/query", json={"q": "组里方向是什么"}, headers=h)
    assert r.status_code == 200, r.text
    tools = {t.tool for t in _traces("kb_query")}
    assert "search_lab_kb" in tools


def test_translate_and_summarize_record_tool_trace(client):
    h = _h(_admin(client))
    lab_id, item_id = _lab_paper(client, h, "aitrsum")
    tr = client.post(
        f"/api/v1/labs/{lab_id}/language/translate",
        json={"text": "Graph convolution.", "target": "zh", "literature_item_id": item_id, "page_no": 1},
        headers=h,
    )
    assert tr.status_code == 200, tr.text
    assert tr.json().get("trace_id") or tr.json().get("session_id")
    tools = {t.tool for t in _traces("language")}
    assert "get_paper_page_text" in tools or "search_lab_kb" in tools
    assert "get_lab_glossary" in tools
    assert "get_page_translation" in tools

    sm = client.post(f"/api/v1/labs/{lab_id}/kb/summarize", headers=h)
    assert sm.status_code == 200, sm.text
    assert sm.json().get("trace_id") or sm.json().get("session_id")
    kb_tools = {t.tool for t in _traces("kb_query")}
    assert "search_lab_kb" in kb_tools
    assert "get_lab_glossary" in kb_tools


def test_meeting_minutes_records_tool_trace(client):
    h = _h(_admin(client))
    lab_id, _item = _lab_paper(client, h, "aimeet")
    m = client.post(f"/api/v1/labs/{lab_id}/meetings", json={"title": "周会", "notes_raw": "行动: 读一篇论文"}, headers=h)
    assert m.status_code == 200, m.text
    out = client.post(f"/api/v1/meetings/{m.json()['id']}/minutes", json={"notes_raw": "行动: 读一篇论文"}, headers=h)
    assert out.status_code == 200, out.text
    tools = {t.tool for t in _traces("meeting")}
    assert "get_lab_members" in tools
    assert "get_lab_activity" in tools
    assert "get_lab_tasks" in tools
