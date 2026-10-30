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


def test_g8_digest_has_delta_and_keeps_situation(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g8dg")
    digest = client.get(f"/api/v1/labs/{lab}/digest", params={"days": 7}, headers=h)
    assert digest.status_code == 200, digest.text
    body = digest.json()
    assert body.get("situation")
    delta = body.get("delta") or {}
    assert "reading_started" in delta
    assert "submitted" in delta
    assert "reading_started_delta" in delta
    assert delta.get("text")
    assert "不含核心" in (body.get("stuck_count_note") or "")


def test_g8_stats_stuck_note(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g8st")
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    assert "不含核心" in (stats.json().get("stuck_count_note") or "")
