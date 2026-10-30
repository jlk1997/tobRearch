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


def test_h542_home_task_meets_minutes_line(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h542hm")
    me = client.get("/api/v1/auth/me", headers=h).json()
    notes = "讨论进度\n行动: 补一组实验\n闲聊"
    m = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "周会", "notes_raw": notes}, headers=h)
    assert m.status_code == 200, m.text
    mid = m.json()["id"]
    out = client.post(
        f"/api/v1/meetings/{mid}/minutes",
        json={"notes_raw": notes, "assign_to": [me["id"]]},
        headers=h,
    )
    assert out.status_code == 200, out.text
    created = out.json()["actions"]
    assert created
    tid = created[0]["task_id"]
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    hit = next(x for x in (home.json().get("tasks") or []) if x.get("id") == tid)
    assert hit.get("href") == f"/labs/{lab}/tasks?open={tid}"
    assert hit.get("source_line") == 2
    assert hit.get("meeting_href") == f"/labs/{lab}/meetings?open={mid}&line=2"


def test_h543_dataset_literature_and_usage_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h543ds")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Dataset Paper", "abstract": "method"},
        headers=h,
    ).json()
    ds = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "关联集", "literature_item_ids": [paper["id"]]},
        headers=h,
    )
    assert ds.status_code == 200, ds.text
    got = client.get(f"/api/v1/datasets/{ds.json()['id']}", headers=h)
    assert got.status_code == 200, got.text
    lit = next(x for x in (got.json().get("literature") or []) if x.get("id") == paper["id"])
    assert (lit.get("href") or "").startswith(f"/literature/{paper['id']}")
    assert lit.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    empty = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "empty", "domain": "general"}, headers=h)
    assert empty.status_code == 200, empty.text
    arts = client.get(f"/api/v1/research-runs/{empty.json()['id']}/artifacts", headers=h)
    assert arts.status_code == 200
    assert arts.json()["items"] == []
    cancelled = client.post(f"/api/v1/research-runs/{empty.json()['id']}/cancel", headers=h)
    assert cancelled.status_code == 200, cancelled.text
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "q", "dataset_id": ds.json()["id"], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    again = client.get(f"/api/v1/datasets/{ds.json()['id']}", headers=h)
    assert again.status_code == 200, again.text
    used = next(x for x in (again.json().get("usages") or {}).get("research_runs") or [] if x.get("id") == run.json()["id"])
    assert used.get("href") == f"/runs/{run.json()['id']}"


def test_h544_manuscript_conflict_has_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h544cf")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "冲突稿", "kind": "other", "content_md": "v1"},
        headers=h,
    ).json()
    stamp = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    ok = client.patch(
        f"/api/v1/manuscripts/{ms['id']}",
        json={"content_md": "v2", "expected_updated_at": stamp},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    stale = client.patch(
        f"/api/v1/manuscripts/{ms['id']}",
        json={"content_md": "v3", "expected_updated_at": stamp},
        headers=h,
    )
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert isinstance(detail, dict)
    assert "手稿已被更新" in (detail.get("message") or "")
    assert detail.get("href") == f"/manuscripts/{ms['id']}"
    assert detail.get("updated_at")
