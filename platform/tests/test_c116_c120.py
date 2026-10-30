from __future__ import annotations

import json
from types import SimpleNamespace

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.services.analysis import analysis_ready_notice_followups, partial_running_card
from fengsuan.app.services.notify import serialize_notification
from fengsuan.app.services.research_engine import read_review_md


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


def test_c116_missing_review_is_empty(tmp_path):
    assert read_review_md(str(tmp_path)) == ""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature_review.md").write_text("# 综述\n\n已写好。", encoding="utf-8")
    text = read_review_md(str(tmp_path))
    assert "已写好" in text


def test_c117_partial_card_stays_running():
    card = partial_running_card(
        {"problem": [{"text": "残差", "pages": [1]}]},
        source="pdf",
        warning="",
        done=1,
        total=3,
    )
    assert card["status"] == "running"
    assert card["chunks_done"] == 1
    assert card["chunks_total"] == 3
    assert "还在写" in card["warning"]
    assert card["problem"][0]["text"] == "残差"


def test_c118_analysis_ready_notice_chips():
    chips = analysis_ready_notice_followups("lit-c118", "lab-c118")
    labels = [c["label"] for c in chips]
    assert labels == ["问这一页", "去写作"]
    assert chips[0]["href"] == "/literature/lit-c118?panel=tutor"
    assert "writing?lit=lit-c118" in chips[1]["href"]
    note = SimpleNamespace(
        id="n-c118",
        type="event",
        title="这篇怎么做已写好",
        content="analysis.ready",
        is_read=False,
        object_type="literature",
        object_id="lit-c118",
        lab_id="lab-c118",
        payload_json="{}",
        created_at=None,
    )
    packed = serialize_notification(note)
    packed_labels = [c["label"] for c in packed.get("followups") or []]
    assert packed_labels == ["问这一页", "去写作"]


def test_c119_kb_sessions_are_private(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c119kb")
    ada = _invite_student(client, h, lab, "c119ada@example.com", "Ada")
    bob = _invite_student(client, h, lab, "c119bob@example.com", "Bob")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    bid = client.get("/api/v1/auth/me", headers=bob).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c119-ada",
                feature="kb_query",
                scope="lab",
                step=1,
                kind="tool",
                tool="search_lab_literature",
                args_json=json.dumps({"q": "Ada 的残差问题"}, ensure_ascii=False),
                result_summary="1 篇",
                source_ids_json="[]",
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=aid,
                session_id="sess-c119-ada",
                feature="kb_query",
                scope="lab",
                step=2,
                kind="answer",
                result_summary="Ada 看到了材料。",
                source_ids_json="[]",
            )
        )
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=bid,
                session_id="sess-c119-bob",
                feature="kb_query",
                scope="lab",
                step=1,
                kind="tool",
                tool="search_lab_literature",
                args_json=json.dumps({"q": "Bob 的私密问题"}, ensure_ascii=False),
                result_summary="0 篇",
                source_ids_json="[]",
            )
        )
        db.commit()
    finally:
        db.close()

    meta = client.get(f"/api/v1/labs/{lab}/kb", headers=ada)
    assert meta.status_code == 200, meta.text
    sessions = meta.json().get("ask_sessions") or []
    questions = " ".join(s.get("question") or "" for s in sessions)
    assert "Ada 的残差问题" in questions
    assert "Bob 的私密问题" not in questions
    assert any(s.get("session_id") == "sess-c119-ada" for s in sessions)
    assert any("Ada 看到了材料" in (s.get("answer") or "") for s in sessions)
