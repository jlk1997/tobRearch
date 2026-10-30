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


def test_g296_method_plain_copy_and_home_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g296card")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Plain Method Paper", "direction_ids": [did]},
        headers=h,
    ).json()
    assert lit["id"]

    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    body = home.json()
    assert body.get("runs_label") == "文献调研"
    steps = body.get("next_steps") or []
    reading = next((s for s in steps if s.get("key") == "reading" or s.get("label") == "开始阅读"), None)
    href = (reading or {}).get("href") or ""
    if href:
        assert href.startswith(f"/labs/{lab}/literature")
        assert "unread" not in href
        if "direction_id=" in href:
            assert f"direction_id={did}" in href

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    stuck = stats.json().get("stuck_brief") or {}
    text = stuck.get("text") or ""
    assert "方法卡" not in text
    assert "不是排名" not in text
    if stuck.get("no_card"):
        assert "还没写方法" in text

    empty = client.get(f"/api/v1/labs/{lab}/literature", params={"has_card": "0"}, headers=h)
    assert empty.status_code == 200, empty.text
    pack = empty.json()
    if isinstance(pack, dict):
        title = ((pack.get("empty_next") or {}).get("title") or "")
        assert "方法卡" not in title
    else:
        assert isinstance(pack, list)

    papers = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert papers.status_code == 200, papers.text
    assert isinstance(papers.json(), list)

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)

    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    assert isinstance(notes.json(), list)
