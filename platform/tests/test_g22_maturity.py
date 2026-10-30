from __future__ import annotations

from fengsuan.app.services.insight import file_list_empty_next, meeting_list_empty_next


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


def _items(payload):
    return payload if isinstance(payload, list) else payload.get("items") or []


def test_g22_direction_empty_stays_list(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g22dir")
    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    assert listed.json() == []


def test_g23_recommend_empty_next_not_on_error_shape(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g23rec")
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h)
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert "items" in body
    if not body.get("items") and not body.get("lab_unread") and not body.get("lab_related"):
        nxt = body.get("empty_next") or {}
        hrefs = " ".join(s.get("href") or "" for s in nxt.get("steps") or [])
        assert "/directions" in hrefs or "tab=search" in hrefs


def test_g24_lab_settings_and_platform_key_are_readable(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g24set")
    got = client.get(f"/api/v1/labs/{lab}", headers=h)
    assert got.status_code == 200, got.text
    assert isinstance(got.json().get("settings"), dict)
    key = client.get("/api/v1/admin/llm-credentials", headers=h)
    assert key.status_code == 200, key.text
    assert "configured" in key.json()


def test_g25_comments_empty_stays_list(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g25cmt")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G25 Comments", "kind": "other", "content_md": "x"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    comments = client.get(f"/api/v1/manuscripts/{ms.json()['id']}/comments", headers=h)
    assert comments.status_code == 200, comments.text
    assert isinstance(comments.json(), list)
    assert comments.json() == []


def test_g26_meetings_files_empty_wrap_and_keep_array(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g26mf")
    meetings = client.get(f"/api/v1/labs/{lab}/meetings", headers=h)
    assert meetings.status_code == 200, meetings.text
    mbody = meetings.json()
    assert isinstance(mbody, dict)
    assert mbody.get("items") == []
    assert mbody.get("empty_kind") == "library"
    hrefs = " ".join(s.get("href") or "" for s in (mbody.get("empty_next") or {}).get("steps") or [])
    assert f"/labs/{lab}/meetings" in hrefs
    files = client.get(f"/api/v1/labs/{lab}/files", headers=h)
    assert files.status_code == 200, files.text
    fbody = files.json()
    assert isinstance(fbody, dict)
    assert fbody.get("items") == []
    assert fbody.get("empty_kind") == "library"
    fhrefs = " ".join(s.get("href") or "" for s in (fbody.get("empty_next") or {}).get("steps") or [])
    assert f"/labs/{lab}/files" in fhrefs
    created = client.post(
        f"/api/v1/labs/{lab}/meetings",
        json={"title": "G26 keep", "notes_raw": "x"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    filled_m = client.get(f"/api/v1/labs/{lab}/meetings", headers=h)
    assert isinstance(filled_m.json(), list)
    assert any(x["id"] == created.json()["id"] for x in filled_m.json())
    up = client.post(
        f"/api/v1/labs/{lab}/files",
        files={"file": ("g26.txt", b"v1", "text/plain")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    filled_f = client.get(f"/api/v1/labs/{lab}/files", headers=h)
    assert isinstance(filled_f.json(), list)
    assert any(x["id"] == up.json()["id"] for x in filled_f.json())
    helper_m = meeting_list_empty_next(lab)
    helper_f = file_list_empty_next(lab)
    assert helper_m["steps"][0]["href"] == f"/labs/{lab}/meetings"
    assert helper_f["steps"][0]["href"] == f"/labs/{lab}/files"
    assert _items(mbody) == []
    assert _items(fbody) == []
