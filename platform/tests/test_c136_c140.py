from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
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


def _answer(db, *, lab, user_id, session_id, feature, kind, summary, question, when):
    db.add(
        AgentTrace(
            lab_id=lab,
            user_id=user_id,
            session_id=session_id,
            feature=feature,
            scope=feature[:20],
            step=90,
            kind="answer",
            args_json=json.dumps({"question": question, "kind": kind}, ensure_ascii=False),
            result_summary=summary if isinstance(summary, str) else json.dumps(summary, ensure_ascii=False),
            source_ids_json="[]",
            created_at=when,
        )
    )


def test_c136_c138_compare_section_do_not_cover_outline(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c136sep")
    ada = _invite_student(client, h, lab, "c136ada@example.com", "Ada")
    bob = _invite_student(client, h, lab, "c136bob@example.com", "Bob")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    bid = client.get("/api/v1/auth/me", headers=bob).json()["id"]
    start = utcnow()
    db = SessionLocal()
    try:
        _answer(
            db,
            lab=lab,
            user_id=aid,
            session_id="sess-c136-outline",
            feature="writing",
            kind="outline",
            summary={"markdown": "Ada 的大纲正文", "title": "残差大纲", "truncated": False},
            question="残差大纲",
            when=start,
        )
        _answer(
            db,
            lab=lab,
            user_id=aid,
            session_id="sess-c137-section",
            feature="writing",
            kind="section",
            summary={"markdown": "Ada 的章节草稿", "heading": "相关工作", "title": "相关工作", "truncated": False},
            question="相关工作",
            when=start + timedelta(seconds=2),
        )
        _answer(
            db,
            lab=lab,
            user_id=aid,
            session_id="sess-c136-compare",
            feature="compare",
            kind="compare",
            summary={
                "markdown": "Ada 的短综述",
                "gap_md": "Ada 的短综述",
                "table_md": "Ada 的对照表",
                "item_ids": ["lit-a", "lit-b"],
                "titles": ["论文甲", "论文乙"],
                "truncated": False,
            },
            question="对比",
            when=start,
        )
        _answer(
            db,
            lab=lab,
            user_id=bid,
            session_id="sess-c136-bob",
            feature="compare",
            kind="compare",
            summary={"markdown": "Bob 的短综述", "gap_md": "Bob 的短综述", "truncated": False},
            question="对比",
            when=start,
        )
        db.commit()
    finally:
        db.close()

    ada_w = client.get(f"/api/v1/labs/{lab}/writing/latest", headers=ada)
    assert ada_w.status_code == 200, ada_w.text
    assert "Ada 的大纲正文" in (ada_w.json().get("markdown") or "")
    assert "Ada 的章节草稿" not in (ada_w.json().get("markdown") or "")

    ada_s = client.get(f"/api/v1/labs/{lab}/writing/section/latest", headers=ada)
    assert ada_s.status_code == 200, ada_s.text
    assert ada_s.json().get("markdown") == "Ada 的章节草稿"
    assert ada_s.json().get("heading") == "相关工作"
    bob_s = client.get(f"/api/v1/labs/{lab}/writing/section/latest", headers=bob)
    assert "Ada 的章节草稿" not in (bob_s.json().get("markdown") or "")

    ada_c = client.get(f"/api/v1/labs/{lab}/literature/compare/latest", headers=ada)
    assert ada_c.status_code == 200, ada_c.text
    assert ada_c.json().get("gap_md") == "Ada 的短综述"
    assert ada_c.json().get("item_ids") == ["lit-a", "lit-b"]
    bob_c = client.get(f"/api/v1/labs/{lab}/literature/compare/latest", headers=bob)
    assert bob_c.json().get("gap_md") == "Bob 的短综述"
    assert "Ada 的短综述" not in (bob_c.json().get("markdown") or "")


def test_c139_c140_review_and_minutes_stay_with_owner(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c139own")
    ada = _invite_student(client, h, lab, "c139ada@example.com", "Ada")
    bob = _invite_student(client, h, lab, "c139bob@example.com", "Bob")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    this_ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "这篇", "kind": "conference", "content_md": "引言"},
        headers=ada,
    )
    other_ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "另一篇", "kind": "conference", "content_md": "方法"},
        headers=ada,
    )
    assert this_ms.status_code == 200, this_ms.text
    assert other_ms.status_code == 200, other_ms.text
    this_id = this_ms.json()["id"]
    other_id = other_ms.json()["id"]
    meet = client.post(
        f"/api/v1/labs/{lab}/meetings",
        json={"title": "周会", "notes_raw": "行动: 读论文"},
        headers=ada,
    )
    assert meet.status_code == 200, meet.text
    meet_id = meet.json()["id"]
    start = utcnow()
    db = SessionLocal()
    try:
        _answer(
            db,
            lab=lab,
            user_id=aid,
            session_id="sess-c139-this",
            feature="review",
            kind="review",
            summary={"markdown": "Ada 对这篇的提示", "manuscript_id": this_id, "title": "这篇", "truncated": False},
            question="这篇",
            when=start,
        )
        _answer(
            db,
            lab=lab,
            user_id=aid,
            session_id="sess-c139-other",
            feature="review",
            kind="review",
            summary={"markdown": "Ada 对另一篇的提示", "manuscript_id": other_id, "title": "另一篇", "truncated": False},
            question="另一篇",
            when=start + timedelta(seconds=3),
        )
        _answer(
            db,
            lab=lab,
            user_id=aid,
            session_id="sess-c140",
            feature="meeting",
            kind="minutes",
            summary={"markdown": "Ada 的纪要", "meeting_id": meet_id, "title": "周会", "truncated": False},
            question="周会",
            when=start,
        )
        db.commit()
    finally:
        db.close()

    ada_r = client.get(f"/api/v1/manuscripts/{this_id}/review/latest", headers=ada)
    assert ada_r.status_code == 200, ada_r.text
    assert ada_r.json().get("markdown") == "Ada 对这篇的提示"
    assert ada_r.json().get("session_id") == "sess-c139-this"
    assert "另一篇" not in (ada_r.json().get("markdown") or "")
    bob_r = client.get(f"/api/v1/manuscripts/{this_id}/review/latest", headers=bob)
    assert bob_r.status_code == 200, bob_r.text
    assert "Ada 对这篇的提示" not in (bob_r.json().get("markdown") or "")

    ada_m = client.get(f"/api/v1/meetings/{meet_id}", headers=ada)
    assert ada_m.status_code == 200, ada_m.text
    assert ada_m.json().get("my_trace_id") == "sess-c140"
    bob_m = client.get(f"/api/v1/meetings/{meet_id}", headers=bob)
    assert bob_m.status_code == 200, bob_m.text
    assert bob_m.json().get("my_trace_id") == ""
