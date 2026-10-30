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


def test_h517_comment_notification_lands_on_comment(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h517nt")
    sh = _invite_student(client, h, lab, "h517c@example.com")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "待批", "kind": "other", "content_md": "引言段落用于定位。"},
        headers=sh,
    ).json()
    posted = client.post(
        f"/api/v1/manuscripts/{ms['id']}/comments",
        json={"body": "请补实验", "anchor_json": {"excerpt": "引言段落用于定位。"}},
        headers=h,
    )
    assert posted.status_code == 200, posted.text
    cid = posted.json()["id"]
    notes = client.get("/api/v1/notifications", headers=sh)
    assert notes.status_code == 200, notes.text
    hit = [n for n in notes.json() if n.get("object_id") == ms["id"]]
    assert hit, notes.json()
    assert hit[0]["comment_id"] == cid
    assert hit[0]["href"] == f"/manuscripts/{ms['id']}?comment={cid}"
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    ev = [x for x in digest.json().get("items") or [] if x.get("verb") == "manuscript.commented"]
    assert ev
    assert ev[0]["href"] == f"/manuscripts/{ms['id']}?comment={cid}"


def test_h518_jump_direction_meeting_dataset(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h518jp")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "JumpDir Unique", "description": ""}, headers=h)
    assert d.status_code == 200, d.text
    m = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "JumpMeet Unique", "notes_raw": "x"}, headers=h)
    assert m.status_code == 200, m.text
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "JumpSet Unique"}, headers=h)
    assert ds.status_code == 200, ds.text
    hit = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "Jump"}, headers=h)
    assert hit.status_code == 200, hit.text
    items = hit.json().get("items") or []
    types = {x["type"] for x in items}
    hrefs = {x["href"] for x in items}
    assert "direction" in types
    assert "meeting" in types
    assert "dataset" in types
    assert f"/labs/{lab}/directions/{d.json()['id']}" in hrefs
    assert f"open={m.json()['id']}" in "".join(hrefs)
    assert f"/datasets/{ds.json()['id']}" in hrefs


def test_h519_milestone_notifies_owner_and_patch(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h519ms")
    sh = _invite_student(client, h, lab, "h519s@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "视觉", "description": ""}, headers=h).json()
    created = client.post(
        f"/api/v1/labs/{lab}/directions/{d['id']}/milestones",
        json={"kind": "proposal", "title": "开题节点", "owner_id": sid},
        headers=h,
    )
    assert created.status_code == 200, created.text
    mid = created.json()["id"]
    notes = client.get("/api/v1/notifications", headers=sh).json()
    hit = [n for n in notes if n.get("content") == "milestone.created"]
    assert hit, notes
    assert f"/labs/{lab}/directions/{d['id']}" in (hit[0].get("href") or "")
    patched = client.patch(f"/api/v1/labs/{lab}/milestones/{mid}", json={"status": "in_progress"}, headers=sh)
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "in_progress"


def test_h520_student_pick_direction_is_personal(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h520gd")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "语音", "description": ""}, headers=h).json()
    sa = _invite_student(client, h, lab, "h520a@example.com")
    sb = _invite_student(client, h, lab, "h520b@example.com")
    picked = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": d["id"]}, headers=sa)
    assert picked.status_code == 200, picked.text
    ga = {x["key"]: x for x in client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sa).json()["guide"]}
    gb = {x["key"]: x for x in client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sb).json()["guide"]}
    assert ga["pick_direction"]["done"] is True
    assert gb["pick_direction"]["done"] is False


def test_h520_admin_usage_has_worker_and_time(client):
    token = _admin(client)
    h = _h(token)
    r = client.get("/api/v1/admin/usage", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("version") == "0.2.0"
    assert "lag_seconds" in (body.get("worker") or {})
    assert isinstance(body.get("items"), list)
    for row in body["items"]:
        assert "created_at" in row


def test_h521_direction_and_comment_href_helper():
    from fengsuan.app.services.insight import object_open_href

    assert object_open_href("lab1", "direction", "dir9", "milestone.created") == "/labs/lab1/directions/dir9"
    assert object_open_href("lab1", "manuscript", "ms1", "manuscript.commented", extra={"comment_id": "c1"}) == "/manuscripts/ms1?comment=c1"
