"""助手回答不把英文草稿和工具摘要当成给人看的话。"""

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.services.agent.loop import _compose_directions, _strip_process_notes
from fengsuan.app.services.insight import kb_ask_sessions


def test_strip_drops_english_scratchpad():
    raw = (
        "组里现在主要做水印。\n"
        "I should present this clearly in Chinese, like talking to a lab mate. Mark sources.\n"
        "The rule says to state conclusion first."
    )
    kept = _strip_process_notes(raw)
    assert "水印" in kept
    assert "I should" not in kept
    assert "The rule says" not in kept


def test_compose_directions_is_chinese():
    sources = type("S", (), {})()
    sources.items = [
        {
            "id": "dir:1",
            "title": "水印",
            "excerpt": "要解决打印后再拍的问题",
            "label": "S1",
        },
        {
            "id": "lab:x",
            "title": "整组",
            "excerpt": "组里现在主要做这几件事。水印，水印",
        },
    ]
    text = _compose_directions(sources)
    assert "水印" in text
    assert "方向节点" not in text
    assert text.startswith("组里现在主要在做")


def test_ask_session_title_is_the_question(client):
    login = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert login.status_code == 200, login.text
    token = login.json()["access"]
    h = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=h).json()
    lab = client.post("/api/v1/labs", json={"name": "问法组", "code": "asktitle"}, headers=h)
    assert lab.status_code == 200, lab.text
    lab_id = lab.json()["id"]
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab_id,
                user_id=me["id"],
                session_id="sess-speak",
                feature="kb_query",
                scope="lab",
                step=1,
                kind="tool",
                tool="get_direction_tree",
                args_json="{}",
                result_summary="共 0 个方向节点",
                source_ids_json="[]",
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab_id,
                user_id=me["id"],
                session_id="sess-speak",
                feature="kb_query",
                scope="lab",
                step=2,
                kind="answer",
                args_json=json.dumps({"question": "实验室是做什么方向的"}, ensure_ascii=False),
                result_summary="组里现在主要做水印。",
                source_ids_json="[]",
            )
        )
        db.commit()
        rows = kb_ask_sessions(db, lab_id, me["id"])
    finally:
        db.close()
    assert rows and rows[0]["question"] == "实验室是做什么方向的"
    assert "方向节点" not in rows[0]["question"]
