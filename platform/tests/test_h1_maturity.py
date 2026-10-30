from __future__ import annotations


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_invite_url_and_home_heartbeat(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "H", "code": "home1", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "stu2@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200
    assert "/invite/" in inv.json()["invite_url"]

    hb = client.post("/api/v1/auth/heartbeat", json={"lab_id": lab}, headers=h)
    assert hb.status_code == 200
    assert hb.json().get("last_seen_at")
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert me["id"]
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200
    assert home.json()["lab_id"] == lab


def test_literature_patch_direction_kb_stats(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "LD", "code": "litd1", "description": ""}, headers=h).json()["id"]
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉", "keywords": ["vision"]},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "A Vision Paper", "authors": ["A"], "publish_year": 2024, "direction_ids": [did]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    iid = lit.json()["id"]
    patched = client.patch(f"/api/v1/literature/{iid}", json={"title": "Renamed Vision Paper", "direction_ids": [did]}, headers=h)
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"].startswith("Renamed")
    kb = client.post(f"/api/v1/literature/{iid}/add-to-kb", headers=h)
    assert kb.status_code == 200, kb.text
    listed = client.get(f"/api/v1/labs/{lab}/literature", params={"direction_id": did}, headers=h)
    assert listed.status_code == 200
    assert any(x["id"] == iid for x in listed.json())
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200
    dirs = st.json()["by_direction"]
    assert any(x["direction_id"] == did and x["literature_count"] >= 1 for x in dirs)


def test_admin_users_student_forbidden_and_task_notify(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "N", "code": "ntf1", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "stu3@example.com", "role": "student"}, headers=h).json()
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv["token"], "email": "stu3@example.com", "password": "student99", "consent": True, "display_name": "学生乙"},
    )
    assert reg.status_code == 200, reg.text
    stu = reg.json()["access"]
    sh = _h(stu)
    forbidden = client.get("/api/v1/admin/users", headers=sh)
    assert forbidden.status_code == 403
    me = client.get("/api/v1/auth/me", headers=sh).json()
    task = client.post(
        f"/api/v1/labs/{lab}/tasks",
        json={"title": "读综述", "assignee_id": me["id"]},
        headers=h,
    )
    assert task.status_code == 200, task.text
    ntf = client.get("/api/v1/notifications", headers=sh)
    assert ntf.status_code == 200
    assert any("读综述" in (x["title"] + x["content"]) for x in ntf.json())


def test_compare_alias_and_cockpit_member(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "C2", "code": "cmp1", "description": ""}, headers=h).json()["id"]
    a = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Paper A", "abstract": "method a"}, headers=h).json()["id"]
    b = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Paper B", "abstract": "method b"}, headers=h).json()["id"]
    cmp = client.post("/api/v1/literature/compare", json={"lab_id": lab, "item_ids": [a, b]}, headers=h)
    assert cmp.status_code == 200, cmp.text
    me = client.get("/api/v1/auth/me", headers=h).json()
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{me['id']}", headers=h)
    assert member.status_code == 200
    assert "timeline" in member.json()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200
    assert cockpit.json()["members"]
