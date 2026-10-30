from __future__ import annotations

from fengsuan.app.services.insight import build_member_stuck, pick_direction_href


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


def test_g35_pick_direction_href_helper():
    assert pick_direction_href("lab1", role="student", surface="home") == "/labs/lab1"
    assert pick_direction_href("lab1", role="student", surface="guide") == "/labs/lab1"
    assert pick_direction_href("lab1", role="teacher", surface="guide") == "/labs/lab1/directions"
    assert pick_direction_href("lab1", surface="member") == "/labs/lab1/members"


def test_g35_student_home_and_guide_stay_on_workbench(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g35hm")
    client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "图网络", "keywords": ["gnn"], "description": ""},
        headers=h,
    )
    sh = _invite_student(client, h, lab, "g35a@example.com")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    body = home.json()
    step = next(x for x in (body.get("next_steps") or []) if x.get("key") == "direction")
    assert step["href"] == f"/labs/{lab}"
    assert "/directions" not in (step.get("href") or "")
    guide = {x["key"]: x for x in body.get("guide") or []}
    assert guide["pick_direction"]["href"] == f"/labs/{lab}"
    teacher_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert teacher_home.status_code == 200, teacher_home.text
    tguide = {x["key"]: x for x in teacher_home.json().get("guide") or []}
    assert tguide["pick_direction"]["href"] == f"/labs/{lab}/directions"


def test_g36_member_stuck_and_self_pick(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g36pk")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉", "keywords": ["cv"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "g36b@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    stuck = build_member_stuck(lab, role="student", direction_id=None, reading_count=0, manuscripts=[])
    direction = next(x for x in stuck["steps"] if x.get("key") == "direction")
    assert direction["href"] == f"/labs/{lab}/members"
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    cards = {c["user_id"]: c for c in cockpit.json().get("members") or []}
    hrefs = " ".join(x.get("href") or "" for x in (cards[sid].get("stuck") or {}).get("steps") or [])
    assert f"/labs/{lab}/members" in hrefs
    picked = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    assert picked.status_code == 200, picked.text
    assert picked.json().get("direction_id") == did
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    assert home.json().get("my_direction_id") == did
    assert not any(x.get("key") == "direction" for x in home.json().get("next_steps") or [])
    members = client.get(f"/api/v1/labs/{lab}/members", headers=sh)
    assert members.status_code == 200, members.text
    rows = members.json() if isinstance(members.json(), list) else members.json().get("items") or []
    mine = next(x for x in rows if x.get("user_id") == sid)
    assert mine.get("direction_id") == did


def test_g37_home_directions_listed_for_select(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g37sl")
    empty_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert empty_home.status_code == 200, empty_home.text
    assert empty_home.json().get("directions") == []
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "语音", "keywords": ["asr"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    names = [x.get("name") for x in home.json().get("directions") or []]
    assert "语音" in names
