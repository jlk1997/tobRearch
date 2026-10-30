from __future__ import annotations


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


def test_g337_direction_list_stays_array_with_stuck(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g337dir")
    empty = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert empty.status_code == 200, empty.text
    assert isinstance(empty.json(), list)
    assert empty.json() == []

    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    row = next(x for x in listed.json() if x["id"] == did)
    assert row["name"] == "视觉大组"
    assert "stuck_count" in row
    assert "unread_count" in row
    assert "unused_research" in row
    assert row.get("unread_href")

    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    steps = home.json().get("next_steps") or []
    reading = next((s for s in steps if s.get("key") == "reading" or s.get("label") == "开始阅读"), None)
    href = (reading or {}).get("href") or ""
    if href:
        assert href.startswith(f"/labs/{lab}/literature")
        assert "unread" not in href
        if "direction_id=" in href:
            assert f"direction_id={did}" in href

    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    assert isinstance(notes.json(), list)
    mutex = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"unused": "1", "status": "pending"},
        headers=h,
    )
    assert mutex.status_code == 200, mutex.text
    nxt = (mutex.json() or {}).get("empty_next") or {}
    assert "综述没写到" in f"{nxt.get('title') or ''}{nxt.get('text') or ''}"
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", params={"direction_id": did}, headers=h)
    assert cockpit.status_code == 200, cockpit.text
    assert cockpit.json().get("events_scope") == "lab"
