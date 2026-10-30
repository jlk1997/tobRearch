from __future__ import annotations

from fengsuan.app.services.insight import work_log_list_empty_next


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


def test_g27_work_logs_empty_wrap_and_keep_array(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g27wl")
    empty = client.get(f"/api/v1/labs/{lab}/work-logs", headers=h)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert isinstance(body, dict)
    assert body.get("items") == []
    assert body.get("empty_kind") == "library"
    hrefs = " ".join(s.get("href") or "" for s in (body.get("empty_next") or {}).get("steps") or [])
    assert f"/labs/{lab}/work-logs" in hrefs
    created = client.post(f"/api/v1/labs/{lab}/work-logs", json={"content": "G27 keep"}, headers=h)
    assert created.status_code == 200, created.text
    filled = client.get(f"/api/v1/labs/{lab}/work-logs", headers=h)
    assert isinstance(filled.json(), list)
    assert any(x["id"] == created.json()["id"] for x in filled.json())
    helper = work_log_list_empty_next(lab)
    assert helper["steps"][0]["href"] == f"/labs/{lab}/work-logs"
    assert _items(body) == []


def test_g28_preflight_endpoints_are_dicts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g28pf")
    research = client.get(f"/api/v1/labs/{lab}/research-preflight", headers=h)
    assert research.status_code == 200, research.text
    rbody = research.json()
    assert isinstance(rbody, dict)
    assert "missing" in rbody
    repro = client.get(f"/api/v1/labs/{lab}/reproduce-preflight", headers=h)
    assert repro.status_code == 200, repro.text
    pbody = repro.json()
    assert isinstance(pbody, dict)
    assert "missing" in pbody


def test_g29_gpu_profile_has_mode(client):
    token = _admin(client)
    h = _h(token)
    listed = client.get("/api/v1/admin/gpu-profiles", headers=h)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert isinstance(rows, list)
    assert rows
    assert "mode" in rows[0]


def test_g30_glossary_empty_stays_list(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g30gl")
    listed = client.get(f"/api/v1/labs/{lab}/glossary", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    assert listed.json() == []
    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)
    assert members.json()
