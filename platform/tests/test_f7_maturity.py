from __future__ import annotations


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str):
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
            "display_name": "生七",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_f7_direction_stuck_and_home_next(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f7stk")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "图学习", "keywords": ["gnn"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f7stu@example.com")
    pick = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    assert pick.status_code == 200, pick.text
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F7 Core Graph Paper", "abstract": "method", "direction_ids": [did]},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F7稿", "kind": "conference", "content_md": "## 引言\n还没有点名。", "direction_ids": [did]},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    stuck = ov.json().get("stuck") or {}
    keys = {x["key"] for x in stuck.get("lines") or []}
    assert {"unread", "no_card", "related_work", "core", "not_run", "idle"} <= keys
    active_keys = {x["key"] for x in stuck.get("active") or []}
    assert "unread" in active_keys
    assert "related_work" in active_keys
    assert "core" in active_keys
    assert any(x.get("user_id") for x in stuck.get("idle_members") or [])
    assert any(m.get("related_work_written") is False for m in ov.json().get("manuscripts") or [])
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    steps = home.json().get("next_steps") or []
    assert any(x.get("key") == "reading" for x in steps)
    assert any("writing" in (x.get("href") or "") or x.get("key") == "reading" for x in steps)
    teacher_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert teacher_home.status_code == 200, teacher_home.text
    own = teacher_home.json().get("next_steps") or []
    assert any("相关工作" in (x.get("text") or "") for x in own)


def test_f7_writing_core_coverage_and_recommend_empty(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f7cov")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "空推荐", "keywords": [], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F7 Must Cite Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    ).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "覆盖稿", "kind": "other", "content_md": "## 引言\n未点名。", "direction_ids": [did]},
        headers=h,
    ).json()
    sec = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "", "literature_item_ids": [paper["id"]], "manuscript_id": ms["id"]},
        headers=h,
    )
    assert sec.status_code == 200, sec.text
    cov = sec.json().get("core_coverage") or {}
    assert cov.get("core_total", 0) >= 1
    assert cov.get("core_missing_count", 0) >= 1
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", params={"direction_id": did}, headers=h)
    assert rec.status_code == 200, rec.text
    nxt = rec.json().get("empty_next") or {}
    labels = " ".join(x.get("label") or "" for x in nxt.get("steps") or [])
    hrefs = " ".join(x.get("href") or "" for x in nxt.get("steps") or [])
    if not (rec.json().get("items") or rec.json().get("lab_unread") or rec.json().get("lab_related")):
        assert "关键词" in labels
        assert "tab=manual" in hrefs or "literature" in hrefs
