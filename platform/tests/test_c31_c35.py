from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.services.activity import emit_activity


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


def _put_card(item_id: str, user_id: str, payload: dict) -> LiteratureAnalysis:
    db = SessionLocal()
    try:
        row = LiteratureAnalysis(
            item_id=item_id,
            analysis_type="method_card",
            result_json=json.dumps(payload, ensure_ascii=False),
            created_by=user_id,
            model="configured",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "出卡生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_c31_week_cards_ignore_abstract_ready(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c31abs")
    sh = _invite_student(client, h, lab, "c31abs@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Abstract Ready Pulse"}, headers=h).json()
    _put_card(paper["id"], sid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    db = SessionLocal()
    try:
        emit_activity(
            db,
            lab_id=lab,
            actor_id=sid,
            verb="analysis.ready",
            object_type="literature",
            object_id=paper["id"],
            summary="Abstract Ready Pulse",
        )
        db.commit()
    finally:
        db.close()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    assert card.get("week_cards_made") == 0
    assert not any((t.get("title") or "") == "Abstract Ready Pulse" for t in card.get("week_card_titles") or [])
    _put_card(paper["id"], sid, {"source": "pdf", "problem": {"text": "from pages", "pages": [1]}})
    cockpit2 = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card2 = next(m for m in cockpit2["members"] if m["user_id"] == sid)
    assert card2.get("week_cards_made") == 1
    assert any((t.get("title") or "") == "Abstract Ready Pulse" for t in card2.get("week_card_titles") or [])


def test_c32_weak_card_titles_from_touched_abstract(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c32weak")
    sh = _invite_student(client, h, lab, "c32weak@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Weak Abstract Paper"}, headers=h).json()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    assert client.post(f"/api/v1/literature/{paper['id']}/progress", json={"page_no": 2, "page_count": 10}, headers=sh).status_code == 200
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    assert any((t.get("title") or "") == "Weak Abstract Paper" for t in card.get("weak_card_titles") or [])
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert any((t.get("title") or "") == "Weak Abstract Paper" for t in member.get("weak_card_titles") or [])


def test_c34_direction_overview_stamps_card_state(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c34dir")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Hub Paper", "direction_ids": [did]},
        headers=h,
    ).json()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h).json()
    row = next(x for x in ov.get("literature") or [] if x["id"] == paper["id"])
    assert row.get("has_method_card") is False
    assert (row.get("card_empty") or {}).get("kind") == "abstract_only"
    assert row.get("card_href")
