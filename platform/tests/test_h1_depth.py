from __future__ import annotations


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _invite_user(client, h, lab, email, role="student", password="student99"):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": role}, headers=h).json()
    client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv["token"], "email": email, "password": password, "consent": True},
    )
    tok = client.post("/api/v1/auth/login", json={"email": email, "password": password}).json()["access"]
    return tok


def test_admin_sees_lab_after_leaving_membership(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "XA", "code": "xad1", "description": ""}, headers=h).json()["id"]
    _invite_user(client, h, lab, "tea@example.com", role="teacher", password="teacher99")
    admin_id = client.get("/api/v1/auth/me", headers=h).json()["id"]
    removed = client.delete(f"/api/v1/labs/{lab}/members/{admin_id}", headers=h)
    assert removed.status_code == 200, removed.text
    labs = client.get("/api/v1/labs", headers=h)
    assert labs.status_code == 200, labs.text
    assert any(x["id"] == lab for x in labs.json())
    me = client.get("/api/v1/auth/me", headers=h).json()
    row = next(x for x in me["labs"] if x["lab_id"] == lab)
    assert row["via_admin"] is True
    assert client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).status_code == 200
    outsider = _invite_user(
        client,
        h,
        client.post("/api/v1/labs", json={"name": "XB", "code": "xbd1", "description": ""}, headers=h).json()["id"],
        "out2@example.com",
    )
    assert client.get(f"/api/v1/labs/{lab}/cockpit", headers=_h(outsider)).status_code == 403


def test_run_json_hides_save_path_and_audits_start(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "XR", "code": "xrun", "description": ""}, headers=h).json()["id"]
    created = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "q"}, headers=h)
    assert created.status_code == 200, created.text
    body = created.json()
    assert "save_path" not in body
    audit = client.get("/api/v1/admin/audit", params={"action": "run.start"}, headers=h)
    assert audit.status_code == 200, audit.text
    assert any(x["resource_id"] == body["id"] for x in audit.json()["items"])


def test_student_cannot_delete_peer_glossary_or_notes(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "XG", "code": "xgl1", "description": ""}, headers=h).json()["id"]
    a = _invite_user(client, h, lab, "ga@example.com")
    b = _invite_user(client, h, lab, "gb@example.com")
    ha, hb = _h(a), _h(b)
    term = client.post(
        f"/api/v1/labs/{lab}/glossary",
        json={"src": "U-Net", "tgt": "", "do_not_translate": True},
        headers=ha,
    )
    assert term.status_code == 200, term.text
    tid = term.json()["id"]
    assert client.delete(f"/api/v1/glossary/{tid}", headers=hb).status_code == 403
    assert client.patch(f"/api/v1/glossary/{tid}", json={"tgt": "leak"}, headers=hb).status_code == 403
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "P"}, headers=ha)
    assert lit.status_code == 200, lit.text
    iid = lit.json()["id"]
    note = client.post(f"/api/v1/literature/{iid}/notes", json={"content_md": "mine"}, headers=ha)
    assert note.status_code == 200, note.text
    nid = note.json()["id"]
    assert client.patch(f"/api/v1/literature/notes/{nid}", json={"content_md": "hijack"}, headers=hb).status_code == 403
    assert client.delete(f"/api/v1/literature/notes/{nid}", headers=hb).status_code == 403
    assert client.patch(f"/api/v1/literature/notes/{nid}", json={"content_md": "updated"}, headers=ha).status_code == 200
    assert client.delete(f"/api/v1/literature/notes/{nid}", headers=ha).status_code == 200


def test_doi_error_does_not_leak_exception(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "XD", "code": "xdoi", "description": ""}, headers=h).json()["id"]
    r = client.post("/api/v1/literature/import-doi", json={"lab_id": lab, "doi": "http://169.254.169.254/latest"}, headers=h)
    assert r.status_code == 400, r.text
    text = r.text.lower()
    assert "169.254" not in text
    assert "traceback" not in text


def test_heartbeat_rejects_foreign_lab(client):
    token = _admin(client)
    h = _h(token)
    lab_a = client.post("/api/v1/labs", json={"name": "HA", "code": "hba", "description": ""}, headers=h).json()["id"]
    lab_b = client.post("/api/v1/labs", json={"name": "HB", "code": "hbb", "description": ""}, headers=h).json()["id"]
    stu = _invite_user(client, h, lab_a, "hb@example.com")
    bad = client.post("/api/v1/auth/heartbeat", json={"lab_id": lab_b}, headers=_h(stu))
    assert bad.status_code == 403
    ok = client.post("/api/v1/auth/heartbeat", json={"lab_id": lab_a}, headers=_h(stu))
    assert ok.status_code == 200, ok.text
