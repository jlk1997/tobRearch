from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.services.insight import kb_session_history


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


def test_c133_history_follows_time_not_step(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c133tm")
    ada = _invite_student(client, h, lab, "c133ada@example.com", "Ada")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    start = utcnow()
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c133",
                feature="kb_query",
                scope="kb",
                step=1,
                kind="tool",
                tool="search_lab_literature",
                args_json=json.dumps({"q": "第一问"}, ensure_ascii=False),
                result_summary="1 篇",
                source_ids_json="[]",
                created_at=start,
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c133",
                feature="kb_query",
                scope="kb",
                step=9,
                kind="answer",
                args_json="{}",
                result_summary="第一答",
                source_ids_json="[]",
                created_at=start + timedelta(seconds=1),
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c133",
                feature="kb_query",
                scope="kb",
                step=1,
                kind="tool",
                tool="search_lab_literature",
                args_json=json.dumps({"q": "第二问"}, ensure_ascii=False),
                result_summary="0 篇",
                source_ids_json="[]",
                created_at=start + timedelta(seconds=2),
            )
        )
        db.commit()
        turns = kb_session_history(db, lab, aid, "sess-c133")
    finally:
        db.close()
    blob = " ".join(f"{t.get('question')} {t.get('answer')}" for t in turns)
    assert "第一问" in blob
    assert "第一答" in blob
    assert "第二问" not in blob


def test_c134_c135_latest_text_is_private(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c134tx")
    ada = _invite_student(client, h, lab, "c134ada@example.com", "Ada")
    bob = _invite_student(client, h, lab, "c134bob@example.com", "Bob")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    bid = client.get("/api/v1/auth/me", headers=bob).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c134-ada",
                feature="writing",
                scope="writing",
                step=90,
                kind="answer",
                args_json=json.dumps({"question": "残差大纲", "kind": "outline"}, ensure_ascii=False),
                result_summary=json.dumps(
                    {"markdown": "Ada 的大纲正文", "title": "残差大纲", "truncated": False},
                    ensure_ascii=False,
                ),
                source_ids_json="[]",
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=bid,
                session_id="sess-c135-bob",
                feature="language",
                scope="language",
                step=90,
                kind="answer",
                args_json=json.dumps({"question": "polish", "truncated": False}, ensure_ascii=False),
                result_summary="Bob 改过的句子",
                source_ids_json="[]",
            )
        )
        db.commit()
    finally:
        db.close()

    ada_w = client.get(f"/api/v1/labs/{lab}/writing/latest", headers=ada)
    assert ada_w.status_code == 200, ada_w.text
    assert "Ada 的大纲正文" in (ada_w.json().get("markdown") or "")
    bob_w = client.get(f"/api/v1/labs/{lab}/writing/latest", headers=bob)
    assert bob_w.status_code == 200, bob_w.text
    assert "Ada 的大纲正文" not in (bob_w.json().get("markdown") or "")
    assert "Ada 的大纲正文" not in (bob_w.json().get("text") or "")

    bob_l = client.get(f"/api/v1/labs/{lab}/language/latest", headers=bob)
    assert bob_l.status_code == 200, bob_l.text
    assert bob_l.json().get("text") == "Bob 改过的句子"
    ada_l = client.get(f"/api/v1/labs/{lab}/language/latest", headers=ada)
    assert ada_l.status_code == 200, ada_l.text
    assert "Bob 改过的句子" not in (ada_l.json().get("text") or "")
