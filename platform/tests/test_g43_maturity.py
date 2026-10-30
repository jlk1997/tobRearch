from __future__ import annotations


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str) -> str:
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_g43_empty_lab_recommend_skips_survey(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g43sv")
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h)
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body.get("query") in {"", None}
    assert body.get("items") == []
    assert body.get("lab_unread") == []
    assert body.get("lab_related") == []
    nxt = body.get("empty_next") or {}
    assert nxt.get("steps")
    keys = [s.get("key") for s in nxt.get("steps") or []]
    assert "keywords" in keys
    used = body.get("sources_used") or []
    assert "medrxiv" in used
    assert "biorxiv" in used


def test_g43_named_direction_keeps_query(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g43nm")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "图网络", "keywords": ["gnn"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    rec = client.post(
        f"/api/v1/labs/{lab}/literature/recommend",
        params={"direction_id": d.json()["id"]},
        headers=h,
    )
    assert rec.status_code == 200, rec.text
    q = rec.json().get("query") or ""
    assert "survey" != q.strip().lower()
    assert "图网络" in q or "gnn" in q.lower()
