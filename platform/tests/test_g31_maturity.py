from __future__ import annotations

from fengsuan.app.services.insight import kb_ask_empty_next


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


def test_g31_unread_count_is_number(client):
    token = _admin(client)
    h = _h(token)
    got = client.get("/api/v1/notifications/unread-count", headers=h)
    assert got.status_code == 200, got.text
    body = got.json()
    assert "unread" in body
    assert isinstance(body["unread"], int)
    assert body["unread"] >= 0


def test_g32_notes_empty_stays_list(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g32nt")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G32 Note Paper", "abstract": "x"},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    notes = client.get(f"/api/v1/literature/{paper.json()['id']}/notes", headers=h)
    assert notes.status_code == 200, notes.text
    assert isinstance(notes.json(), list)
    assert notes.json() == []


def test_g33_labs_list_stays_array(client):
    token = _admin(client)
    h = _h(token)
    listed = client.get("/api/v1/labs", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    lab = _lab(client, h, "g33lb")
    filled = client.get("/api/v1/labs", headers=h)
    assert isinstance(filled.json(), list)
    assert any(x.get("id") == lab for x in filled.json())


def test_g34_kb_docs_empty_next(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g34kb")
    listed = client.get(f"/api/v1/labs/{lab}/kb/documents", headers=h)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert isinstance(body, dict)
    assert body.get("items") == []
    assert body.get("total") == 0
    assert body.get("empty_kind") == "library"
    hrefs = " ".join(s.get("href") or "" for s in (body.get("empty_next") or {}).get("steps") or [])
    assert f"/labs/{lab}/kb" in hrefs
    helper = kb_ask_empty_next(lab, "no_indexed")
    assert helper["steps"][0]["href"] == f"/labs/{lab}/kb?tab=docs"
    up = client.post(
        f"/api/v1/labs/{lab}/kb/documents",
        files={"file": ("g34.txt", b"G34 token methods.", "text/plain")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    filled = client.get(f"/api/v1/labs/{lab}/kb/documents", headers=h)
    assert filled.status_code == 200, filled.text
    pack = filled.json()
    assert pack.get("total") >= 1
    assert "empty_kind" not in pack
    assert any(x["id"] == up.json()["id"] for x in pack.get("items") or [])
