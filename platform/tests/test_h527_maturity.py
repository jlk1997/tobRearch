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


def test_h527_milestone_due_label_readable(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h527due")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "体验方向", "description": ""}, headers=h).json()
    created = client.post(
        f"/api/v1/labs/{lab}/directions/{d['id']}/milestones",
        json={"kind": "proposal", "title": "开题体验", "due_at": "2020-01-02"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["kind_label"] == "开题"
    assert body["overdue"] is True
    assert body["days_left"] is not None and body["days_left"] < 0
    assert "已过期" in (body.get("due_label") or "")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h).json()
    hit = next(x for x in home["milestones_upcoming"] if x["id"] == body["id"])
    assert "已过期" in (hit.get("due_label") or "")
    empty_lab = _lab(client, h, "h527emp")
    empty_home = client.get("/api/v1/me/home", params={"lab_id": empty_lab}, headers=h).json()
    assert empty_home["milestones_upcoming"] == []
    assert empty_home["queue"]["awaiting_gpu"] == 0
    assert empty_home["queue"]["blocked"] == 0


def test_h529_research_runs_filter_by_dataset(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h529ds")
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "筛选用集"}, headers=h).json()
    other = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "另一集"}, headers=h).json()
    a = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "用筛选用集", "dataset_id": ds["id"], "domain": "general"},
        headers=h,
    )
    assert a.status_code == 200, a.text
    client.post(f"/api/v1/research-runs/{a.json()['id']}/cancel", headers=h)
    b = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "用另一集", "dataset_id": other["id"], "domain": "general"},
        headers=h,
    )
    assert b.status_code == 200, b.text
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", params={"dataset_id": ds["id"]}, headers=h)
    assert listed.status_code == 200, listed.text
    ids = {x["id"] for x in listed.json()}
    assert a.json()["id"] in ids
    assert b.json()["id"] not in ids
