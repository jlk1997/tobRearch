from __future__ import annotations


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_upload_filename_cannot_escape_file_root(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "SEC", "code": "sec1", "description": ""}, headers=h).json()["id"]
    r = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        headers=h,
        files=[("files", ("../../evil.pdf", b"%PDF-1.4 test", "application/pdf"))],
    )
    assert r.status_code == 200, r.text
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h).json()
    items = listed.get("items") if isinstance(listed, dict) else listed
    assert items
    iid = items[0]["id"]
    detail = client.get(f"/api/v1/literature/{iid}/detail", headers=h)
    assert detail.status_code == 200, detail.text
    names = [f.get("filename") for f in detail.json().get("files") or []]
    assert names == ["evil.pdf"]
    pdf = client.get(f"/api/v1/literature/{iid}/pdf", headers=h)
    assert pdf.status_code == 200


def test_cross_lab_assignee_rejected(client):
    token = _admin(client)
    h = _h(token)
    lab_a = client.post("/api/v1/labs", json={"name": "SA", "code": "seca", "description": ""}, headers=h).json()["id"]
    lab_b = client.post("/api/v1/labs", json={"name": "SB", "code": "secb", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab_b}/invites", json={"email": "out@example.com", "role": "student"}, headers=h).json()
    client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv["token"], "email": "out@example.com", "password": "student99", "consent": True},
    )
    outsider = client.post("/api/v1/auth/login", json={"email": "out@example.com", "password": "student99"}).json()["access"]
    oid = client.get("/api/v1/auth/me", headers=_h(outsider)).json()["id"]
    bad = client.post(f"/api/v1/labs/{lab_a}/tasks", json={"title": "leak", "assignee_id": oid}, headers=h)
    assert bad.status_code == 400, bad.text


def test_deleted_lab_is_gone(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "DEL", "code": "del1", "description": ""}, headers=h).json()["id"]
    assert client.delete(f"/api/v1/labs/{lab}", headers=h).status_code == 200
    assert client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).status_code == 404


def test_cannot_demote_last_teacher(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "T1", "code": "tch1", "description": ""}, headers=h).json()["id"]
    me = client.get("/api/v1/auth/me", headers=h).json()["id"]
    r = client.patch(f"/api/v1/labs/{lab}/members/{me}", json={"role": "student"}, headers=h)
    assert r.status_code == 400, r.text


def test_admin_cannot_disable_self(client):
    token = _admin(client)
    h = _h(token)
    me = client.get("/api/v1/auth/me", headers=h).json()["id"]
    r = client.patch(f"/api/v1/admin/users/{me}", json={"is_active": False}, headers=h)
    assert r.status_code == 400, r.text


def test_artifact_path_traversal_404(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "RUN", "code": "runx", "description": ""}, headers=h).json()["id"]
    created = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "q"}, headers=h)
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    r = client.get(f"/api/v1/research-runs/{rid}/artifacts/file", params={"path": "../secret.txt"}, headers=h)
    assert r.status_code in {404, 400}


def test_inactive_user_cannot_login(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "IN", "code": "inac", "description": ""}, headers=h).json()["id"]
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "off@example.com", "role": "student"}, headers=h).json()
    client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv["token"], "email": "off@example.com", "password": "student99", "consent": True},
    )
    stu = client.post("/api/v1/auth/login", json={"email": "off@example.com", "password": "student99"}).json()["access"]
    sid = client.get("/api/v1/auth/me", headers=_h(stu)).json()["id"]
    patched = client.patch(f"/api/v1/admin/users/{sid}", json={"is_active": False}, headers=h)
    assert patched.status_code == 200, patched.text
    again = client.post("/api/v1/auth/login", json={"email": "off@example.com", "password": "student99"})
    assert again.status_code == 401
