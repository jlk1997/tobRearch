from __future__ import annotations

from fengsuan.app.services.writing import is_related_work_heading


def test_f8_related_work_heading_not_prose():
    assert is_related_work_heading("Related Works")
    assert is_related_work_heading("## 相关工作\n后文")
    assert is_related_work_heading("\\section{Related Work}")
    assert not is_related_work_heading("## 引言\n没有相关工作。")
    assert not is_related_work_heading("引言\n还没有相关工作。")


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
            "display_name": "生八",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_f8_cockpit_member_stuck(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f8stk")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "图网络", "keywords": ["gnn"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f8stu@example.com")
    me = client.get("/api/v1/auth/me", headers=sh)
    assert me.status_code == 200, me.text
    sid = me.json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F8 Unread Paper", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F8稿", "kind": "other", "content_md": "## 引言\n没有相关工作。", "direction_ids": [did]},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    cards = {c["user_id"]: c for c in cockpit.json().get("members") or []}
    stu = cards[sid]
    assert stu.get("stuck", {}).get("empty") is False
    keys = {x.get("key") for x in stu.get("stuck", {}).get("steps") or []}
    assert "reading" in keys
    teacher_id = client.get("/api/v1/auth/me", headers=h).json()["id"]
    tcard = cards[teacher_id]
    assert any(m.get("related_work_written") is False for m in tcard.get("manuscripts") or [])
    assert any(x.get("key") == "related_work" for x in (tcard.get("stuck") or {}).get("steps") or [])
    detail = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h)
    assert detail.status_code == 200, detail.text
    assert any(x.get("key") == "reading" for x in (detail.json().get("stuck") or {}).get("steps") or [])


def test_f8_stats_digest_stuck_brief(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f8stt")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉", "keywords": ["cv"], "description": ""},
        headers=h,
    )
    did = d.json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F8 Vision Paper", "abstract": "cnn", "direction_ids": [did]},
        headers=h,
    )
    client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F8统计稿", "kind": "other", "content_md": "## 引言\n草稿。", "direction_ids": [did]},
        headers=h,
    )
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    body = st.json()
    brief = body.get("stuck_brief") or {}
    assert "卡住" in (brief.get("text") or "") or brief.get("unread", 0) >= 1
    assert brief.get("related_work_missing", 0) >= 1
    drow = next(x for x in body.get("by_direction") or [] if x.get("direction_id") == did)
    assert drow.get("unread_count", 0) >= 1
    assert drow.get("related_work_missing", 0) >= 1
    assert drow.get("stuck_count", 0) >= 1
    assert any(int(x.get("stuck_count") or 0) >= 0 for x in body.get("by_member") or [])
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "stuck" in csv.text
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    assert "卡住" in (digest.json().get("stuck_brief_md") or "")
    assert "排行" in (digest.json().get("stuck_brief_md") or "")
