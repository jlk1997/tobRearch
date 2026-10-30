from __future__ import annotations

import json
from types import SimpleNamespace

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.services.insight import _pack_ai_session, ask_history_for_agent, kb_session_history


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str):
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _invite_student(client, h, lab: str, email: str, name: str = "学生"):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": name,
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_c121_kb_last_ai_opens_same_session():
    row = SimpleNamespace(
        lab_id="lab-c121",
        user_id="u1",
        session_id="sess-c121",
        feature="kb_query",
        scope="lab",
        args_json=json.dumps({"q": "组里残差"}, ensure_ascii=False),
        result_summary="",
        error=None,
        tool="search_lab_literature",
        kind="tool",
        source_ids_json="[]",
        id="k1",
    )
    packed = _pack_ai_session([row])
    assert packed["href"].startswith("/labs/lab-c121/assistant?")
    assert "session=sess-c121" in packed["href"]


def test_c122_client_history_wins():
    client = [SimpleNamespace(q="上一问", a="上一答")]
    stored = [{"question": "追踪里的问", "answer": "追踪里的答"}]
    turns = ask_history_for_agent(client, stored)
    assert turns == [{"question": "上一问", "answer": "上一答"}]
    assert ask_history_for_agent([], stored) == stored


def test_c122_c125_session_history_is_private(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c122kb")
    ada = _invite_student(client, h, lab, "c122ada@example.com", "Ada")
    bob = _invite_student(client, h, lab, "c122bob@example.com", "Bob")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    bid = client.get("/api/v1/auth/me", headers=bob).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c122-ada",
                feature="kb_query",
                scope="lab",
                step=1,
                kind="tool",
                tool="search_lab_literature",
                args_json=json.dumps({"q": "工具里的短词"}, ensure_ascii=False),
                result_summary="1 篇",
                source_ids_json="[]",
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c122-ada",
                feature="kb_query",
                scope="lab",
                step=2,
                kind="answer",
                args_json=json.dumps({"question": "Ada 完整的上一问"}, ensure_ascii=False),
                result_summary="Ada 的回答。",
                source_ids_json="[]",
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=bid,
                session_id="sess-c122-ada",
                feature="kb_query",
                scope="lab",
                step=2,
                kind="answer",
                args_json=json.dumps({"question": "Bob 不该被看见"}, ensure_ascii=False),
                result_summary="Bob 的回答。",
                source_ids_json="[]",
            )
        )
        db.commit()
        turns = kb_session_history(db, lab, aid, "sess-c122-ada")
    finally:
        db.close()
    blob = " ".join(f"{t.get('question')} {t.get('answer')}" for t in turns)
    assert "Ada 完整的上一问" in blob
    assert "Ada 的回答" in blob
    assert "Bob 不该被看见" not in blob
    assert "Bob 的回答" not in blob
