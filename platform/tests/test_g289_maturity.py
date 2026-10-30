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


def test_g289_home_copy_and_arrays(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g289hint")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Hint Tip Paper", "direction_ids": [did]},
        headers=h,
    ).json()
    assert lit["id"]

    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    body = home.json()
    assert body.get("runs_label") == "文献调研"
    steps = body.get("next_steps") or []
    reading = next((s for s in steps if s.get("key") == "reading" or s.get("label") == "开始阅读"), None)
    href = (reading or {}).get("href") or body.get("read_href") or ""
    if href:
        assert href.startswith(f"/labs/{lab}/literature")
        assert "unread" not in href
        if "direction_id=" in href:
            assert f"direction_id={did}" in href
            assert href.count("&") == 0 or "unread" not in href

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    stuck = stats.json().get("stuck_brief") or {}
    text = stuck.get("text") or ""
    assert "卡住" not in text
    assert "还没做完" in text
    assert "发现式" not in text
    assert "驾驶舱" not in (stats.json().get("stuck_count_note") or "")

    unused = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1"}, headers=h)
    assert unused.status_code == 200, unused.text
    pack = unused.json()
    title = ((pack.get("empty_next") or {}).get("title") if isinstance(pack, dict) else "") or ""
    assert "发现式" not in title
    assert "综述没写到" in title or "文献调研" in title

    papers = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert papers.status_code == 200, papers.text
    assert isinstance(papers.json(), list)

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)

    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    assert isinstance(notes.json(), list)
    if notes.json():
        jump = notes.json()[0].get("href") or notes.json()[0].get("url") or ""
        if jump:
            assert "/pi" not in jump
