from __future__ import annotations

from fengsuan.app.services.insight import task_list_empty_next


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


def test_g18_manuscript_empty_wrap_is_items(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g18ms")
    listed = client.get(f"/api/v1/labs/{lab}/manuscripts", headers=h)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert isinstance(body, dict)
    assert body.get("items") == []
    assert _items(body) == []


def test_g19_task_empty_library_and_filter(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g19tk")
    empty = client.get(f"/api/v1/labs/{lab}/tasks", headers=h)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert isinstance(body, dict)
    assert body.get("empty_kind") == "library"
    hrefs = " ".join(s.get("href") or "" for s in (body.get("empty_next") or {}).get("steps") or [])
    assert "/tasks" in hrefs
    created = client.post(f"/api/v1/labs/{lab}/tasks", json={"title": "G19 keep"}, headers=h)
    assert created.status_code == 200, created.text
    filled = client.get(f"/api/v1/labs/{lab}/tasks", headers=h)
    assert isinstance(filled.json(), list)
    none = client.get(f"/api/v1/labs/{lab}/tasks", params={"assignee": "none"}, headers=h)
    assert none.status_code == 200, none.text
    # unassigned create has no assignee, so none should still be a list with the task
    none_rows = _items(none.json())
    assert any(x["id"] == created.json()["id"] for x in none_rows)
    sid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    client.patch(f"/api/v1/tasks/{created.json()['id']}", json={"assignee_id": sid}, headers=h)
    after = client.get(f"/api/v1/labs/{lab}/tasks", params={"assignee": "none"}, headers=h)
    assert after.status_code == 200, after.text
    pack = after.json()
    assert pack.get("empty_kind") == "filter"
    assert "待认领" in ((pack.get("empty_next") or {}).get("title") or "")
    helper = task_list_empty_next(lab, empty_kind="filter", assignee="none")
    assert helper["steps"][0]["href"] == f"/labs/{lab}/tasks"
