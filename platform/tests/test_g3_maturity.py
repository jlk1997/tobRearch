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


def test_g3_create_manuscript_next_step(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g3ms")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G3 相关工作", "kind": "other", "content_md": "## 引言\n草稿。"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    nxt = ms.json().get("next_step") or {}
    assert nxt.get("key") == "related_work"
    assert nxt.get("href")
    assert f"/labs/{lab}/writing" in (nxt.get("href") or "")
