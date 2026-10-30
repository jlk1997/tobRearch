from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.literature import LiteratureAnalysis


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


def test_c51_c52_member_ai_and_stuck_align(client):
    token = _admin(client)
    h = _h(token)
    admin_id = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c51ai")
    sh = _invite_student(client, h, lab, "c51ai@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    admin_card = next(m for m in cockpit["members"] if m["user_id"] == admin_id)
    student_card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    assert "ai_calls" in admin_card and "tokens" in admin_card
    assert "ai_calls" in student_card
    peer = client.get(f"/api/v1/labs/{lab}/cockpit", headers=sh).json()
    own = next(m for m in peer["members"] if m["user_id"] == sid)
    other = next(m for m in peer["members"] if m["user_id"] == admin_id)
    assert "ai_calls" in own
    assert "ai_calls" not in other
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h).json()
    stat_row = next(m for m in stats.get("by_member") or [] if m["user_id"] == sid)
    assert stat_row.get("stuck_count") == int((student_card.get("stuck") or {}).get("count") or 0)


def test_c53_recent_ai_teacher_or_self(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c53ai")
    sh = _invite_student(client, h, lab, "c53ai@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    other = _invite_student(client, h, lab, "c53peer@example.com", "同伴")
    db = SessionLocal()
    try:
        db.add(
            AgentTrace(
                lab_id=lab,
                user_id=sid,
                session_id="c53-sess-1",
                feature="tutor",
                scope="paper",
                step=0,
                kind="tool",
                tool="get_page_text",
                args_json=json.dumps({"question": "这篇残差怎么做"}, ensure_ascii=False),
                result_summary="ok",
            )
        )
        db.commit()
    finally:
        db.close()
    mine = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=sh).json()
    assert any(x.get("trace_id") == "c53-sess-1" for x in mine.get("recent_ai") or [])
    assert any("残差" in (x.get("question") or "") for x in mine.get("recent_ai") or [])
    teacher = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert any(x.get("trace_id") == "c53-sess-1" for x in teacher.get("recent_ai") or [])
    hidden = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=other).json()
    assert hidden.get("recent_ai") == []


def test_c54_direction_filters_people_not_events(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c54dir")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    sh_a = _invite_student(client, h, lab, "c54a@example.com", "甲")
    sh_b = _invite_student(client, h, lab, "c54b@example.com", "乙")
    sid_a = client.get("/api/v1/auth/me", headers=sh_a).json()["id"]
    sid_b = client.get("/api/v1/auth/me", headers=sh_b).json()["id"]
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh_a).status_code == 200
    client.post(f"/api/v1/labs/{lab}/literature", json={"title": "C54 Event Paper"}, headers=h)
    wide = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    scoped = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h, params={"direction_id": did}).json()
    assert wide.get("events_scope") == "lab"
    assert scoped.get("events_scope") == "lab"
    assert scoped.get("events_follows_direction") is False
    assert scoped.get("members_follows_direction") is True
    assert wide.get("recent_events") == scoped.get("recent_events")
    scoped_ids = {m["user_id"] for m in scoped.get("members") or []}
    assert sid_a in scoped_ids
    assert sid_b not in scoped_ids
    assert scoped.get("members_counts_note")


def test_c55_query_via_and_hangable_score(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c55why")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Anomaly Detector C55", "abstract": "residual detector", "direction_ids": []},
        headers=h,
    ).json()
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=paper["id"],
                analysis_type="method_card",
                result_json=json.dumps(
                    {"source": "pdf", "methods": {"text": "residual detector 残差检测", "pages": [1]}},
                    ensure_ascii=False,
                ),
                created_by=uid,
                model="configured",
            )
        )
        db.commit()
    finally:
        db.close()
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1}).json()
    hang = next(x for x in (pack.get("direction_gap") or {}).get("hangable") or [] if x["item_id"] == paper["id"])
    assert hang.get("matched")
    assert hang.get("score") is not None
    assert hang.get("direction_name")
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
    assert rec.status_code == 200, rec.text
    assert rec.json().get("query_via") == "direction"
