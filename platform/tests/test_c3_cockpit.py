from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "脉搏生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_c3_member_pulse_has_page_card_note_and_profile(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c3pulse")
    sh = _invite_student(client, h, lab, "c3pulse@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Pulse Paper", "abstract": "A residual detector.", "authors": ["Ada"]},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    pid = paper.json()["id"]
    assert client.post(f"/api/v1/literature/{pid}/progress", json={"page_no": 7, "page_count": 20}, headers=sh).status_code == 200
    assert client.post(
        f"/api/v1/literature/{pid}/notes",
        json={"content_md": "本页在讲残差。", "page_no": 7},
        headers=sh,
    ).status_code == 200
    assert client.patch(
        f"/api/v1/labs/{lab}/members/{sid}/profile",
        json={"bio": "做残差检测", "interests": ["residual"]},
        headers=sh,
    ).status_code == 200
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=pid,
                analysis_type="method_card",
                result_json=json.dumps(
                    {
                        "problem": {"text": "small objects", "pages": [1]},
                        "methods": {"text": "residual detector", "pages": [2]},
                        "source": "pdf",
                    },
                    ensure_ascii=False,
                ),
                created_by=sid,
            )
        )
        db.commit()
    finally:
        db.close()

    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    body = cockpit.json()
    assert body.get("events_scope") == "lab"
    assert body.get("events_follows_direction") is False
    card = next(m for m in body["members"] if m["user_id"] == sid)
    assert card["method_card_count"] == 1
    assert card["touched_count"] == 1
    assert card["note_count"] >= 1
    assert card["week_reading_started"] >= 1
    assert card["week_notes"] >= 1
    preview = next(r for r in card["readings"] if r["id"] == pid)
    assert preview["last_page_no"] == 7
    assert preview.get("has_method_card") is True
    assert preview.get("has_note") is True

    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h)
    assert member.status_code == 200, member.text
    detail = member.json()
    assert (detail.get("profile") or {}).get("bio") == "做残差检测"
    assert "residual" in ((detail.get("profile") or {}).get("interests") or [])
    now = next(r for r in detail["reading_now"] if r["id"] == pid)
    assert now["last_page_no"] == 7
    assert now.get("has_method_card") is True
    assert now.get("has_note") is True
    assert detail.get("week_reading_started") >= 1
    assert "reader_id=" in (detail.get("literature_href") or "")

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    row = next(m for m in stats.json().get("by_member") or [] if m["user_id"] == sid)
    assert row["week_reading_started"] >= 1
    assert row["week_notes"] >= 1


def test_c3_cockpit_events_ignore_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c3ev")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "无关方向", "keywords": ["zzz"], "description": ""},
        headers=h,
    ).json()["id"]
    a = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    b = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h, params={"direction_id": did}).json()
    assert a.get("events_scope") == "lab"
    assert b.get("events_scope") == "lab"
    assert a.get("recent_events") == b.get("recent_events")
