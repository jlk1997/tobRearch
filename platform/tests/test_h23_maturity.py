from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.kb import KbDocument


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


def test_outline_without_papers_uses_skeleton_note(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "olsk")
    ol = client.post(f"/api/v1/labs/{lab}/writing/outline", json={"title": "综述", "kind": "survey"}, headers=h)
    assert ol.status_code == 200, ol.text
    body = ol.json()
    assert body.get("notes") == "未结合文献"
    assert "相关工作" in (body.get("markdown") or "")
    heads = [s.get("heading") for s in (body.get("outline") or {}).get("sections") or []]
    assert "相关工作" in heads


def test_kb_query_empty_reason_and_reindex(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "kbrx")
    empty = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "anything"}, headers=h)
    assert empty.status_code == 200, empty.text
    assert empty.json().get("empty_reason") == "no_indexed"
    assert empty.json().get("hits") == []
    up = client.post(
        f"/api/v1/labs/{lab}/kb/documents",
        files={"file": ("a.txt", b"AlphaOnlyToken appears in methods.", "text/plain")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    doc_id = up.json()["id"]
    assert up.json()["status"] == "indexed"
    db = SessionLocal()
    try:
        row = db.get(KbDocument, doc_id)
        assert row is not None
        row.status = "failed"
        row.parse_error = "index_failed"
        db.commit()
    finally:
        db.close()
    listed = client.get(f"/api/v1/labs/{lab}/kb/documents", headers=h).json()
    assert any(x["id"] == doc_id and x["status"] == "failed" for x in listed.get("items") or [])
    rx = client.post(f"/api/v1/kb/documents/{doc_id}/reindex", headers=h)
    assert rx.status_code == 200, rx.text
    assert rx.json()["status"] == "indexed"
    q = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "AlphaOnlyToken"}, headers=h)
    assert q.status_code == 200, q.text
    assert q.json().get("empty_reason") in ("", "no_hits")
    assert any("AlphaOnlyToken" in (h.get("citation") or "") for h in q.json().get("hits") or [])


def test_kb_reindex_cross_lab_forbidden(client):
    token = _admin(client)
    h = _h(token)
    lab_a = _lab(client, h, "kba1")
    lab_b = _lab(client, h, "kbb1")
    up = client.post(
        f"/api/v1/labs/{lab_a}/kb/documents",
        files={"file": ("a.txt", b"secret chunk", "text/plain")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    inv = client.post(f"/api/v1/labs/{lab_b}/invites", json={"email": "kbr@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "kbr@example.com", "password": "student99", "display_name": "生", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    sh = _h(reg.json()["access"])
    denied = client.post(f"/api/v1/kb/documents/{up.json()['id']}/reindex", headers=sh)
    assert denied.status_code == 403, denied.text


def test_write_section_still_skips_without_mode(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "wss2")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Keep", "kind": "other", "content_md": "ORIGINAL_BODY"},
        headers=h,
    ).json()
    r = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "", "manuscript_id": ms["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    got = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert got["content_md"] == "ORIGINAL_BODY"
