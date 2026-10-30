from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace


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


def test_c129_empty_history_uses_own_trace(client, monkeypatch):
    seen: dict = {}

    def fake_chat(db, **kwargs):
        seen["messages"] = list(kwargs.get("messages") or [])
        return "下一轮回答"

    monkeypatch.setattr("fengsuan.app.api.v1.kb.chat", fake_chat)
    monkeypatch.setattr(
        "fengsuan.app.api.v1.kb.query_kb",
        lambda *args, **kwargs: (
            [
                {
                    "page_no": 1,
                    "citation": "残差块",
                    "content": "残差块写在方法里",
                    "filename": "net.pdf",
                    "literature_item_id": "lit-x",
                }
            ],
            "",
        ),
    )
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c129kb")
    ada = _invite_student(client, h, lab, "c129ada@example.com", "Ada")
    bob = _invite_student(client, h, lab, "c129bob@example.com", "Bob")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c129-ada",
                feature="kb_query",
                scope="kb",
                step=2,
                kind="answer",
                args_json=json.dumps({"question": "Ada 完整的上一问"}, ensure_ascii=False),
                result_summary="Ada 的回答。",
                source_ids_json="[]",
            )
        )
        db.commit()
    finally:
        db.close()

    asked = client.post(
        f"/api/v1/labs/{lab}/kb/query",
        json={"q": "那实验呢", "session_id": "sess-c129-ada"},
        headers=ada,
    )
    assert asked.status_code == 200, asked.text
    blob = " ".join(str(m.get("content") or "") for m in seen.get("messages") or [])
    assert "Ada 完整的上一问" in blob
    assert "Ada 的回答" in blob

    db = SessionLocal()
    try:
        rows = (
            db.query(AgentTrace)
            .filter(AgentTrace.session_id == "sess-c129-ada", AgentTrace.user_id == aid, AgentTrace.kind == "answer")
            .all()
        )
    finally:
        db.close()
    questions = []
    for row in rows:
        try:
            args = json.loads(row.args_json or "{}")
        except json.JSONDecodeError:
            args = {}
        questions.append(str(args.get("question") or ""))
    assert "那实验呢" in questions

    seen.clear()
    other = client.post(
        f"/api/v1/labs/{lab}/kb/query",
        json={"q": "Bob 自己的问", "session_id": "sess-c129-ada"},
        headers=bob,
    )
    assert other.status_code == 200, other.text
    bob_blob = " ".join(str(m.get("content") or "") for m in seen.get("messages") or [])
    assert "Ada 完整的上一问" not in bob_blob
    assert "Ada 的回答" not in bob_blob
