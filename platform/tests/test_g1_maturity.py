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


def _items(payload):
    if isinstance(payload, list):
        return payload
    return payload.get("items") or []


def test_g1_filter_empty_is_not_library(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g1flt")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G1 Only Paper TitleX", "abstract": "a"},
        headers=h,
    ).json()
    assert client.post(f"/api/v1/literature/{paper['id']}/mark-read", json={"status": "reading"}, headers=h).status_code == 200
    empty = client.get(
        f"/api/v1/labs/{lab}/literature",
        params={"reading_status": "unread", "page": 1},
        headers=h,
    )
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert _items(body) == []
    assert body.get("empty_kind") == "filter"
    nxt = body.get("empty_next") or {}
    assert "还没有文献" not in (nxt.get("title") or "")
    assert nxt.get("steps")


def test_g1_vacuum_library_empty_kind(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g1vac")
    listed = client.get(f"/api/v1/labs/{lab}/literature", params={"page": 1}, headers=h)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert _items(body) == []
    assert body.get("empty_kind") == "library"
    assert "还没有文献" in ((body.get("empty_next") or {}).get("title") or "")


def test_g1_manuscripts_and_repro_filter_empty(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g1ms")
    client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G1 Draft", "kind": "other", "content_md": "## 相关工作\n已写。"},
        headers=h,
    )
    empty = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"related_work": False}, headers=h)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body.get("empty_kind") == "filter"
    assert body.get("empty_next")
    repro = client.get(f"/api/v1/labs/{lab}/reproduce-runs", params={"status": "awaiting_gpu"}, headers=h)
    assert repro.status_code == 200, repro.text
    rbody = repro.json()
    if isinstance(rbody, dict):
        assert rbody.get("empty_kind") in {"filter", "library"}
        assert rbody.get("empty_next")
