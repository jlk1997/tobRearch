from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.kb import KbDocument
from fengsuan.app.services.kb import chunk_pages, lexical_score, query_kb


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


def test_g134_chunk_overlap_and_heading():
    pages = ["[sec.1] Methods\n" + ("attention " * 400)]
    chunks = chunk_pages(pages, max_chars=200, overlap=30)
    assert len(chunks) > 1
    assert chunks[0]["heading_path"].startswith("Methods")
    assert chunks[0]["page_no"] == 1


def test_g135_direction_kb_empty_is_named(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g134kb")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "检索方向"}, headers=h).json()
    asked = client.post(
        f"/api/v1/labs/{lab}/kb/query",
        json={"q": "attention", "direction_id": d["id"]},
        headers=h,
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body.get("empty_reason") == "no_indexed"
    assert (body.get("empty_next") or {}).get("title") == "这些论文的正文还不能问"


def test_g136_hybrid_and_scope(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g136hy")
    d1 = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "甲"}, headers=h).json()["id"]
    d2 = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "乙"}, headers=h).json()["id"]
    up = client.post(
        f"/api/v1/labs/{lab}/kb/documents",
        files={"file": ("note.md", b"Transformer attention is all you need. BERT uses bidirectional encoders.", "text/markdown")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    db = SessionLocal()
    try:
        doc = db.get(KbDocument, up.json()["id"])
        doc.direction_id = d1
        db.commit()
        hits, _fb = query_kb(db, lab, "attention transformer", user_id="", direction_id=d1)
        assert hits
        assert lexical_score("attention transformer", hits[0]["content"]) > 0
        other, _ = query_kb(db, lab, "attention transformer", user_id="", direction_id=d2)
        assert other == []
    finally:
        db.close()
    asked = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "attention transformer"}, headers=h)
    assert asked.status_code == 200, asked.text
    assert asked.json().get("retrieval_mode") in {"simple", "hybrid", "vector"}
    fb = client.post(
        f"/api/v1/labs/{lab}/kb/feedback",
        json={"chunk_id": asked.json()["hits"][0]["chunk_id"], "useful": True, "q": "attention"},
        headers=h,
    )
    assert fb.status_code == 200, fb.text
