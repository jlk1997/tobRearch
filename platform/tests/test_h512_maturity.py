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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": email, "password": "student99", "display_name": "生", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_h512_minutes_line_task_roundtrip_and_idempotent(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h512mt")
    notes = "讨论进度\n行动: 读一篇论文\n闲聊"
    m = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "周会", "notes_raw": notes}, headers=h)
    assert m.status_code == 200, m.text
    mid = m.json()["id"]
    out = client.post(f"/api/v1/meetings/{mid}/minutes", json={"notes_raw": notes}, headers=h)
    assert out.status_code == 200, out.text
    created = out.json()["actions"]
    assert len(created) == 1
    assert created[0]["title"] == "读一篇论文"
    assert created[0]["source_line"] == 2
    assert created[0]["task_href"] == f"/labs/{lab}/tasks?open={created[0]['task_id']}"
    assert created[0]["meeting_href"] == f"/labs/{lab}/meetings?open={mid}&line=2"
    again = client.post(f"/api/v1/meetings/{mid}/minutes", json={"notes_raw": notes}, headers=h)
    assert again.status_code == 200, again.text
    assert again.json()["actions"] == []
    tasks = client.get(f"/api/v1/labs/{lab}/tasks", headers=h)
    assert tasks.status_code == 200
    rows = tasks.json()
    assert len(rows) == 1
    hit = rows[0]
    assert hit["meeting_id"] == mid
    assert hit["source_line"] == 2
    assert hit["meeting_href"] == f"/labs/{lab}/meetings?open={mid}&line=2"
    detail = client.get(f"/api/v1/meetings/{mid}", headers=h).json()
    assert any(a.get("task_href") == f"/labs/{lab}/tasks?open={hit['id']}" and a.get("source_line") == 2 for a in detail["actions"])
    line2 = next(x for x in detail["lines"] if x["n"] == 2)
    assert line2["task_id"] == hit["id"]
    assert line2["task_href"] == f"/labs/{lab}/tasks?open={hit['id']}"


def test_h513_membership_title_not_a_third_role(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h513tt")
    sh = _invite_student(client, h, lab, "h513s@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    own = client.patch(f"/api/v1/labs/{lab}/members/{sid}", json={"title": "博二"}, headers=sh)
    assert own.status_code == 200, own.text
    assert own.json()["title"] == "博二"
    assert own.json()["role"] == "student"
    teach = client.patch(f"/api/v1/labs/{lab}/members/{sid}", json={"title": "研三"}, headers=h)
    assert teach.status_code == 200, teach.text
    assert teach.json()["title"] == "研三"
    denied_role = client.patch(f"/api/v1/labs/{lab}/members/{sid}", json={"role": "teacher"}, headers=sh)
    assert denied_role.status_code == 403
    admin_id = client.get("/api/v1/auth/me", headers=h).json()["id"]
    denied_title = client.patch(f"/api/v1/labs/{lab}/members/{admin_id}", json={"title": "冒充"}, headers=sh)
    assert denied_title.status_code == 403
    members = client.get(f"/api/v1/labs/{lab}/members", headers=h).json()
    stu = next(x for x in members if x["user_id"] == sid)
    assert stu["title"] == "研三"
    assert stu["role"] == "student"
    me = client.get("/api/v1/auth/me", headers=sh).json()
    lab_row = next(x for x in me["labs"] if x["lab_id"] == lab)
    assert lab_row["title"] == "研三"
    cock = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(x for x in cock["members"] if x["user_id"] == sid)
    assert card["title"] == "研三"


def test_h514_comment_href_and_activity(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h514cm")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "稿", "kind": "other", "content_md": "引言段落用于定位。"},
        headers=h,
    ).json()
    posted = client.post(
        f"/api/v1/manuscripts/{ms['id']}/comments",
        json={"body": "请补实验", "anchor_json": {"excerpt": "引言段落用于定位。"}},
        headers=h,
    )
    assert posted.status_code == 200, posted.text
    href = f"/manuscripts/{ms['id']}?comment={posted.json()['id']}"
    assert posted.json()["href"] == href
    listed = client.get(f"/api/v1/manuscripts/{ms['id']}/comments", headers=h).json()
    assert listed[0]["href"] == href
    cock = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    ev = [e for e in cock["recent_events"] if e.get("verb") == "manuscript.commented"]
    assert ev
    assert ev[0]["comment_id"] == posted.json()["id"]


def test_h516_health_request_id_version_worker(client):
    r = client.get("/health", headers={"X-Request-Id": "probe-h516"})
    assert r.status_code == 200
    assert r.headers.get("x-request-id") == "probe-h516"
    body = r.json()
    assert body["product"] == "蜂算科研"
    assert body["version"] == "0.2.0"
    worker = body.get("worker") or {}
    assert worker.get("enabled") is False
    assert worker.get("lock") == "disabled"
    assert "lag_seconds" in worker
    from fengsuan.app.services.worker import process_once

    process_once()
    after = client.get("/health").json()["worker"]
    assert after["last_tick_at"]
    assert after["lag_seconds"] is not None
    assert after["lag_seconds"] >= 0
    custom = client.get("/meta")
    assert custom.status_code == 200
    assert custom.headers.get("x-request-id")
    assert custom.json()["version"] == "0.2.0"
