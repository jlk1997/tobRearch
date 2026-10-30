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
            "display_name": "主张生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_c37_claims_abstract_card_is_not_written(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c37claim")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Claim Abstract Residual"},
        headers=h,
    ).json()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Claim Draft", "kind": "conference", "content_md": "引言\n我们引用 residualabs。"},
        headers=h,
    ).json()
    cite = client.post(
        f"/api/v1/manuscripts/{ms['id']}/citations",
        json={"literature_item_id": paper["id"], "citation_key": "residualabs"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    claims = client.get(f"/api/v1/manuscripts/{ms['id']}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    row = next(x for x in claims.json()["items"] if x["literature_item_id"] == paper["id"])
    assert row.get("has_method_card") is False
    assert (row.get("card_source") or "") == "abstract_only"
    missing = claims.json().get("missing_method_cards") or []
    assert any(x.get("literature_item_id") == paper["id"] for x in missing)


def test_c38_weak_titles_carry_why(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c38why")
    sh = _invite_student(client, h, lab, "c38why@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Why Abstract Paper"}, headers=h).json()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    assert client.post(f"/api/v1/literature/{paper['id']}/progress", json={"page_no": 1, "page_count": 8}, headers=sh).status_code == 200
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    row = next(t for t in card.get("weak_card_titles") or [] if (t.get("title") or "") == "Why Abstract Paper")
    assert row.get("why")
    assert row.get("kind") == "abstract_only"
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    mrow = next(t for t in member.get("weak_card_titles") or [] if (t.get("title") or "") == "Why Abstract Paper")
    assert mrow.get("why")
