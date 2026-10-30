"""本人助手对话可以改名、删除，删不掉别人的。"""

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.identity import User


def _guest(client):
    guest = client.post("/api/v1/auth/guest")
    assert guest.status_code == 200, guest.text
    headers = {"Authorization": f"Bearer {guest.json()['access']}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    body = me.json()
    return headers, body["id"], body["labs"][0]["lab_id"]


def test_rename_and_delete_own_ask_session(client):
    headers, user_id, lab_id = _guest(client)
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == "admin@example.com").one()
        db.add(
            AgentTrace(
                lab_id=lab_id,
                user_id=user_id,
                session_id="sess-own",
                feature="kb_query",
                kind="answer",
                step=1,
                args_json=json.dumps({"question": "原来的问题"}, ensure_ascii=False),
                result_summary="原来的回答",
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab_id,
                user_id=admin.id,
                session_id="sess-own",
                feature="kb_query",
                kind="answer",
                step=1,
                args_json=json.dumps({"question": "别人的问题"}, ensure_ascii=False),
                result_summary="别人的回答",
            )
        )
        db.commit()
    finally:
        db.close()

    renamed = client.patch(
        f"/api/v1/labs/{lab_id}/ask/sessions/sess-own",
        headers=headers,
        json={"title": "给领导看的"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "给领导看的"

    home = client.get(f"/api/v1/labs/{lab_id}/assistant/home", headers=headers)
    assert home.status_code == 200, home.text
    row = next(s for s in home.json()["ask_sessions"] if s["session_id"] == "sess-own")
    assert row["title"] == "给领导看的"
    assert row["question"] == "原来的问题"

    blank = client.patch(
        f"/api/v1/labs/{lab_id}/ask/sessions/sess-own",
        headers=headers,
        json={"title": "   "},
    )
    assert blank.status_code == 400

    gone = client.delete(f"/api/v1/labs/{lab_id}/ask/sessions/sess-own", headers=headers)
    assert gone.status_code == 200, gone.text
    home = client.get(f"/api/v1/labs/{lab_id}/assistant/home", headers=headers)
    assert all(s["session_id"] != "sess-own" for s in home.json()["ask_sessions"])

    missing = client.delete(f"/api/v1/labs/{lab_id}/ask/sessions/sess-own", headers=headers)
    assert missing.status_code == 404

    db = SessionLocal()
    try:
        own = (
            db.query(AgentTrace)
            .filter(AgentTrace.session_id == "sess-own", AgentTrace.user_id == user_id)
            .all()
        )
        assert own and all(row.deleted_at is not None for row in own)
        other = (
            db.query(AgentTrace)
            .filter(AgentTrace.session_id == "sess-own", AgentTrace.user_id != user_id)
            .one()
        )
        assert other.deleted_at is None
        assert "别人的问题" in (other.args_json or "")
    finally:
        db.close()
