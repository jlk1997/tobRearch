from __future__ import annotations


def _items(payload):
    return payload if isinstance(payload, list) else payload.get("items") or []


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


def test_h545_members_href_to_cockpit_and_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h545mb")
    sh = _invite_student(client, h, lab, "h545s@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "视觉", "description": ""}, headers=h)
    assert d.status_code == 200, d.text
    picked = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": d.json()["id"]}, headers=sh)
    assert picked.status_code == 200, picked.text
    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    stu = next(x for x in members.json() if x["user_id"] == sid)
    assert stu.get("href") == f"/labs/{lab}/cockpit/members/{sid}"
    assert stu.get("direction_id") == d.json()["id"]
    assert stu.get("direction_name") == "视觉"
    assert stu.get("direction_href") == f"/labs/{lab}/directions/{d.json()['id']}"
    assert stu.get("role") == "student"


def test_h546_meeting_file_log_href_and_bell_reproduce(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h546os")
    sh = _invite_student(client, h, lab, "h546s@example.com")
    notes = "讨论\n行动: 补实验\n闲聊"
    m = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "周会", "notes_raw": notes}, headers=h)
    assert m.status_code == 200, m.text
    mid = m.json()["id"]
    assert m.json().get("href") == f"/labs/{lab}/meetings?open={mid}"
    extracted = client.post(f"/api/v1/meetings/{mid}/minutes", json={"notes_raw": notes}, headers=h)
    assert extracted.status_code == 200, extracted.text
    listed = client.get(f"/api/v1/labs/{lab}/meetings", headers=h)
    assert listed.status_code == 200, listed.text
    meet = next(x for x in _items(listed.json()) if x["id"] == mid)
    assert meet.get("href") == f"/labs/{lab}/meetings?open={mid}"
    assert meet.get("action_count") == 1
    detail = client.get(f"/api/v1/meetings/{mid}", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json().get("href") == f"/labs/{lab}/meetings?open={mid}"
    up = client.post(f"/api/v1/labs/{lab}/files", files={"file": ("brief.txt", b"hello", "text/plain")}, headers=h)
    assert up.status_code == 200, up.text
    fid = up.json()["id"]
    assert up.json().get("href") == f"/labs/{lab}/files?open={fid}"
    files = client.get(f"/api/v1/labs/{lab}/files", headers=h)
    assert files.status_code == 200, files.text
    frow = next(x for x in _items(files.json()) if x["id"] == fid)
    assert frow.get("href") == f"/labs/{lab}/files?open={fid}"
    log = client.post(f"/api/v1/labs/{lab}/work-logs", json={"content": "本周写了摘要"}, headers=h)
    assert log.status_code == 200, log.text
    lid = log.json()["id"]
    assert log.json().get("href") == f"/labs/{lab}/work-logs?open={lid}"
    logs = client.get(f"/api/v1/labs/{lab}/work-logs", headers=h)
    assert logs.status_code == 200, logs.text
    lrow = next(x for x in _items(logs.json()) if x["id"] == lid)
    assert lrow.get("href") == f"/labs/{lab}/work-logs?open={lid}"
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Bell Repro Paper", "abstract": "method"},
        headers=sh,
    ).json()
    notes_list = client.get("/api/v1/notifications", headers=h)
    assert notes_list.status_code == 200, notes_list.text
    hit = next(x for x in notes_list.json() if x.get("object_id") == paper["id"])
    assert (hit.get("href") or "").startswith(f"/literature/{paper['id']}")
    assert hit.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"


def test_h547_jump_file_log_and_writing_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h547jp")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Jump Writing Unique", "kind": "other", "content_md": "x"},
        headers=h,
    ).json()
    up = client.post(
        f"/api/v1/labs/{lab}/files",
        files={"file": ("JumpFileUnique.txt", b"hello", "text/plain")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    log = client.post(f"/api/v1/labs/{lab}/work-logs", json={"content": "JumpLog Unique 本周卡在数据"}, headers=h)
    assert log.status_code == 200, log.text
    hit = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "Jump"}, headers=h)
    assert hit.status_code == 200, hit.text
    items = hit.json().get("items") or []
    types = {x["type"] for x in items}
    assert "manuscript" in types
    assert "file" in types
    assert "work_log" in types
    paper = next(x for x in items if x.get("id") == ms["id"])
    assert paper.get("href") == f"/manuscripts/{ms['id']}"
    assert paper.get("writing_href") == f"/labs/{lab}/writing?ms={ms['id']}"
    frow = next(x for x in items if x.get("id") == up.json()["id"])
    assert frow.get("href") == f"/labs/{lab}/files?open={up.json()['id']}"
    lrow = next(x for x in items if x.get("id") == log.json()["id"])
    assert lrow.get("href") == f"/labs/{lab}/work-logs?open={log.json()['id']}"
