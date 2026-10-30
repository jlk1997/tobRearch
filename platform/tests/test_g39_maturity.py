from __future__ import annotations

from fengsuan.app.services.literature import recommend_empty_next


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


def test_g39_recommend_empty_next_by_role():
    stu = recommend_empty_next("lab1", "q", role="student")
    keys = [s.get("key") for s in stu.get("steps") or []]
    assert "keywords" not in keys
    hrefs = " ".join(s.get("href") or "" for s in stu.get("steps") or [])
    assert "tab=search" in hrefs
    assert "先填方向关键词" not in (stu.get("text") or "")
    tea = recommend_empty_next("lab1", "q", role="teacher")
    assert tea["steps"][0]["key"] == "keywords"
    assert tea["steps"][0]["href"] == "/labs/lab1/directions"
    hub = recommend_empty_next("lab1", "q", role="teacher", direction_id="dir9")
    assert hub["steps"][0]["href"] == "/labs/lab1/directions/dir9"


def test_g39_student_recommend_skips_keyword_step(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g39rec")
    sh = _invite_student(client, h, lab, "g39a@example.com")
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=sh)
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body.get("items") == []
    assert body.get("query") in {"", None}
    nxt = body.get("empty_next") or {}
    keys = [s.get("key") for s in nxt.get("steps") or []]
    assert "keywords" not in keys
    hrefs = " ".join(s.get("href") or "" for s in nxt.get("steps") or [])
    assert "tab=search" in hrefs
    teacher_rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h)
    assert teacher_rec.status_code == 200, teacher_rec.text
    tbody = teacher_rec.json()
    assert tbody.get("items") == []
    keys = [s.get("key") for s in (tbody.get("empty_next") or {}).get("steps") or []]
    assert "keywords" in keys


def test_g40_teacher_patches_direction_keywords(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g40kw")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "空白词", "keywords": [], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    assert ov.json().get("keywords") == []
    patched = client.patch(f"/api/v1/directions/{did}", json={"keywords": ["gnn", "graph"]}, headers=h)
    assert patched.status_code == 200, patched.text
    ov2 = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov2.status_code == 200, ov2.text
    assert "gnn" in (ov2.json().get("keywords") or [])
    sh = _invite_student(client, h, lab, "g40b@example.com")
    denied = client.patch(f"/api/v1/directions/{did}", json={"keywords": ["hack"]}, headers=sh)
    assert denied.status_code in {403, 404}


def test_g41_teacher_recommend_with_direction_goes_to_hub(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g41hb")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "指定方向", "keywords": [], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", params={"direction_id": did}, headers=h)
    assert rec.status_code == 200, rec.text
    body = rec.json()
    if not body.get("items") and not body.get("lab_unread") and not body.get("lab_related"):
        step = (body.get("empty_next") or {}).get("steps") or []
        assert step
        assert step[0]["href"] == f"/labs/{lab}/directions/{did}"
