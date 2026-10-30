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
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_g47_archive_clears_member_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g47ar")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "旧方向", "keywords": ["old"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "g47a@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    picked = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    assert picked.status_code == 200, picked.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.json().get("my_direction_id") == did
    gone = client.delete(f"/api/v1/directions/{did}", headers=h)
    assert gone.status_code == 200, gone.text
    home2 = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home2.status_code == 200, home2.text
    assert home2.json().get("my_direction_id") in {None, ""}
    assert any(x.get("key") == "direction" for x in home2.json().get("next_steps") or [])
    members = client.get(f"/api/v1/labs/{lab}/members", headers=sh)
    mine = next(x for x in members.json() if x.get("user_id") == sid)
    assert mine.get("direction_id") in {None, ""}
    denied = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    assert denied.status_code == 404


def test_g48_stale_recommend_direction_not_hub(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g48st")
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", params={"direction_id": "missing-dir"}, headers=h)
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body.get("items") == []
    hrefs = " ".join(s.get("href") or "" for s in (body.get("empty_next") or {}).get("steps") or [])
    assert "/directions/missing-dir" not in hrefs
    assert f"/labs/{lab}/directions" in hrefs


def test_g50_situation_empty_direction_copy(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g50st")
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    lines = {x.get("key"): x for x in (cockpit.json().get("situation") or {}).get("lines") or []}
    empty = (lines.get("direction") or {}).get("empty_text") or ""
    assert "导师" in empty
    assert "学生" not in empty or "导师" in empty
    sh = _invite_student(client, h, lab, "g50b@example.com")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    step = next(x for x in home.json().get("next_steps") or [] if x.get("key") == "direction")
    assert "导师" in (step.get("text") or "")
