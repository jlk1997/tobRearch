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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "生G2",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _items(payload):
    if isinstance(payload, list):
        return payload
    return payload.get("items") or []


def test_g2_reading_scope_lab_not_my_reading(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g2scp")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "G2", "keywords": ["g2"], "description": ""}, headers=h)
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "g2scp@example.com")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G2 Student Reading TitleX", "abstract": "a", "direction_ids": [did]},
        headers=h,
    ).json()
    assert client.post(f"/api/v1/literature/{paper['id']}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    mine = client.get(
        f"/api/v1/labs/{lab}/literature",
        params={"reading_status": "reading", "page": 1},
        headers=h,
    )
    assert mine.status_code == 200, mine.text
    assert paper["id"] not in {x["id"] for x in _items(mine.json())}
    lab_scope = client.get(
        f"/api/v1/labs/{lab}/literature",
        params={"reading_status": "reading", "reading_scope": "lab", "direction_id": did, "page": 1},
        headers=h,
    )
    assert lab_scope.status_code == 200, lab_scope.text
    assert paper["id"] in {x["id"] for x in _items(lab_scope.json())}
    student_mine = client.get(
        f"/api/v1/labs/{lab}/literature",
        params={"reading_status": "reading", "page": 1},
        headers=sh,
    )
    assert paper["id"] in {x["id"] for x in _items(student_mine.json())}


def test_g2_situation_time_href_is_lab_scope(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g2sit")
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    sit = stats.json().get("situation") or {}
    time_line = next((x for x in sit.get("lines") or [] if x.get("key") == "time"), None)
    href = (time_line or {}).get("href") or (stats.json().get("this_week") or {}).get("reading_started_href") or ""
    assert "reading_scope=lab" in href
    assert "reading_status=reading" in href


def test_g2_want_read_member_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g2want")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "G2w", "keywords": ["w"], "description": ""}, headers=h)
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "g2want@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G2 Want Read TitleX", "abstract": "a", "direction_ids": [did]},
        headers=h,
    ).json()
    assert client.post(f"/api/v1/literature/{paper['id']}/mark-read", json={"status": "want_read"}, headers=sh).status_code == 200
    cards = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cards.status_code == 200, cards.text
    card = next(x for x in cards.json().get("members") or [] if x.get("user_id") == sid)
    assert "want_read" in (card.get("want_read_href") or "")
    assert f"reader_id={sid}" in (card.get("want_read_href") or "")
    assert "reading_scope=lab" not in (card.get("want_read_href") or "")
    listed = client.get(
        f"/api/v1/labs/{lab}/literature",
        params={"reader_id": sid, "reading_status": "want_read", "direction_id": did, "page": 1},
        headers=h,
    )
    assert paper["id"] in {x["id"] for x in _items(listed.json())}
