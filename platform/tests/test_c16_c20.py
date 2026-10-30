from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureFile
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.analysis import method_card_needs_preread


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


def _put_card(item_id: str, user_id: str, payload: dict) -> None:
    db = SessionLocal()
    try:
        db.add(
            LiteratureAnalysis(
                item_id=item_id,
                analysis_type="method_card",
                result_json=json.dumps(payload, ensure_ascii=False),
                created_by=user_id,
                model="configured",
            )
        )
        db.commit()
    finally:
        db.close()


def test_c16_abstract_only_needs_preread_when_pdf_ready(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c16pdf")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Abstract Then PDF", "abstract": "only abstract"},
        headers=h,
    ).json()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    db = SessionLocal()
    try:
        assert method_card_needs_preread(db, paper["id"]) is False
        db.add(
            LiteratureFile(
                item_id=paper["id"],
                file_path=f"/tmp/{paper['id']}.pdf",
                filename="full.pdf",
                mime="application/pdf",
                parse_status="ok",
                text_hash="abc123",
            )
        )
        db.commit()
        assert method_card_needs_preread(db, paper["id"]) is True
    finally:
        db.close()


def test_c18_week_reading_titles(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c18read")
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "c18s@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": "c18s@example.com",
            "password": "student99",
            "display_name": "开读生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    sh = _h(reg.json()["access"])
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Week Open Read"}, headers=h).json()
    db = SessionLocal()
    try:
        emit_activity(
            db,
            lab_id=lab,
            actor_id=sid,
            verb="reading.started",
            object_type="literature",
            object_id=paper["id"],
            summary="Week Open Read",
        )
        db.commit()
    finally:
        db.close()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    assert any((t.get("title") or "") == "Week Open Read" for t in card.get("week_reading_titles") or [])
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert member.get("week_reading_titles")


def test_c19_hung_method_snippet_helps_hangable(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c19snip")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": [], "description": ""},
        headers=h,
    ).json()["id"]
    hung = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "A Novel Approach to Imaging", "direction_ids": [did]},
        headers=h,
    ).json()
    _put_card(
        hung["id"],
        uid,
        {
            "source": "pdf",
            "methods": {"text": "we use a residual detector", "pages": [2]},
            "claims": [{"text": "residual detector works", "pages": [2]}],
        },
    )
    nxt = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Networks for Detection"},
        headers=h,
    ).json()
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1}).json()
    hang_ids = {x["item_id"] for x in (pack.get("direction_gap") or {}).get("hangable") or []}
    assert hung["id"] not in hang_ids
    assert nxt["id"] in hang_ids
    row = next(x for x in pack["items"] if x["id"] == nxt["id"])
    assert did not in (row.get("direction_ids") or [])


def test_c20_recommend_dedupes_and_author_sample(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c20dup")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    hung = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Lab Residual Paper", "authors": ["He Kaiming"], "direction_ids": [did]},
        headers=h,
    ).json()
    monkeypatch.setattr(
        "fengsuan.app.services.literature.search_sources",
        lambda query, sources, max_per_source=8, language=None: [
            {"title": "Same Residual Hit", "authors": ["He Kaiming"], "abstract": "residual", "doi": "10.1/a"},
            {"title": "Same Residual Hit", "authors": ["Other"], "abstract": "residual", "doi": "10.1/b"},
        ],
    )
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
    assert rec.status_code == 200, rec.text
    body = rec.json()
    titles = [x.get("title") for x in body.get("items") or []]
    assert titles.count("Same Residual Hit") == 1
    he = next(row for row in body.get("authors") or [] if row["name"] == "He Kaiming")
    assert he.get("sample_href") == f"/literature/{hung['id']}"
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h, params={"direction_id": did}).json()
    lit = next(n for n in graph.get("nodes") or [] if n.get("id") == hung["id"])
    assert lit.get("first_author") == "He Kaiming"
