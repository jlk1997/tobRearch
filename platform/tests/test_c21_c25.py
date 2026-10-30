from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureFile, LiteratureNote
from fengsuan.app.services.analysis import PREREAD_FAIL_RETRY_S, method_card_needs_preread
from fengsuan.app.services.literature import recommend_hit_why


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


def _put_card(item_id: str, user_id: str, payload: dict, *, created_at=None) -> LiteratureAnalysis:
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
        if created_at is not None:
            live = db.get(LiteratureAnalysis, row.id)
            live.created_at = created_at
            db.commit()
            db.refresh(live)
            return live
        return row
    finally:
        db.close()


def test_c21_abstract_upgrade_cools_after_failed_attempt(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c21cool")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Abstract Cool", "abstract": "only abstract"},
        headers=h,
    ).json()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    db = SessionLocal()
    try:
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
    failed = _put_card(paper["id"], uid, {"source": "parse_failed", "warning": "没提炼出这篇怎么做"})
    db = SessionLocal()
    try:
        assert method_card_needs_preread(db, paper["id"]) is False
        live = db.get(LiteratureAnalysis, failed.id)
        live.created_at = utcnow() - timedelta(seconds=PREREAD_FAIL_RETRY_S + 30)
        db.commit()
        assert method_card_needs_preread(db, paper["id"]) is True
    finally:
        db.close()


def test_c22_list_marks_abstract_only_when_pdf_ready(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c22abs")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Has PDF Still Abstract", "abstract": "abstract"},
        headers=h,
    ).json()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    db = SessionLocal()
    try:
        db.add(
            LiteratureFile(
                item_id=paper["id"],
                file_path=f"/tmp/{paper['id']}.pdf",
                filename="full.pdf",
                mime="application/pdf",
                parse_status="ok",
                text_hash="def456",
            )
        )
        db.commit()
    finally:
        db.close()
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    assert pack.status_code == 200, pack.text
    row = next(x for x in pack.json()["items"] if x["id"] == paper["id"])
    assert row.get("has_pdf") is True
    assert (row.get("card_empty") or {}).get("kind") == "abstract_only"


def test_c23_week_note_titles(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c23note")
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "c23s@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": "c23s@example.com",
            "password": "student99",
            "display_name": "笔记生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    sh = _h(reg.json()["access"])
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Week Note Paper"}, headers=h).json()
    db = SessionLocal()
    try:
        db.add(LiteratureNote(item_id=paper["id"], user_id=sid, content_md="读了方法"))
        db.commit()
    finally:
        db.close()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    assert any((t.get("title") or "") == "Week Note Paper" for t in card.get("week_note_titles") or [])
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert member.get("week_note_titles")


def test_c24_keyword_hints_include_method_tokens(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c24hint")
    a = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "A Novel Approach One"}, headers=h).json()
    b = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "A Novel Approach Two"}, headers=h).json()
    _put_card(a["id"], uid, {"source": "pdf", "methods": {"text": "residual detector baseline", "pages": [1]}})
    _put_card(b["id"], uid, {"source": "pdf", "methods": {"text": "residual detector network", "pages": [1]}})
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1}).json()
    hints = (pack.get("direction_gap") or {}).get("keyword_hints") or []
    assert "residual" in hints


def test_c25_recommend_why_uses_query(client, monkeypatch):
    assert recommend_hit_why(["residual"], "q", "direction") == "residual"
    assert recommend_hit_why([], "Residual Detector Lab", "titles").startswith("已挂题名：")
    assert recommend_hit_why([], "残差检测 residual", "direction").startswith("检索：")
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c25why")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    monkeypatch.setattr(
        "fengsuan.app.services.literature.search_sources",
        lambda query, sources, max_per_source=8, language=None: [
            {"title": "Quantum Primer Unrelated", "authors": ["Anon"], "abstract": "qubits", "doi": "10.1/c25"}
        ],
    )
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
    assert rec.status_code == 200, rec.text
    hit = next(x for x in rec.json().get("items") or [] if x.get("title") == "Quantum Primer Unrelated")
    assert (hit.get("why") or "").startswith("检索：") or (hit.get("why") or "").startswith("已挂题名：")
