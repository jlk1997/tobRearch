from __future__ import annotations

from unittest.mock import patch

from fengsuan.app.services.writing import stamp_related_work_mentions


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
            "display_name": "生十一",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_stamp_related_work_mentions_adds_missing_titles():
    text = stamp_related_work_mentions("差距一句。", [{"title": "F11 Must Cite Paper Title"}])
    assert "F11 Must Cite Paper Title" in text
    again = stamp_related_work_mentions(text, [{"title": "F11 Must Cite Paper Title"}])
    assert again.count("F11 Must Cite Paper Title") == 1


def test_f11_teacher_home_and_cockpit_stuck_brief(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f11stk")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "图学习", "keywords": ["gnn"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f11stu@example.com")
    pick = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    assert pick.status_code == 200, pick.text
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F11 Unread Core Paper", "abstract": "method", "direction_ids": [did]},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F11稿", "kind": "conference", "content_md": "## 引言\n还没有点名。", "direction_ids": [did]},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    brief = cockpit.json().get("stuck_brief") or {}
    assert brief.get("unread", 0) >= 1
    assert brief.get("related_work_missing", 0) >= 1
    assert "不是排行" in (brief.get("text") or "")
    assert "reading_status=unread" in (brief.get("unread_href") or "")
    teacher_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert teacher_home.status_code == 200, teacher_home.text
    th = teacher_home.json()
    assert (th.get("stuck_brief") or {}).get("unread", 0) >= 1
    keys = {x.get("key") for x in th.get("next_steps") or []}
    assert "unread" in keys or "related_work" in keys
    assert "reading" not in keys
    assert any("相关工作" in (x.get("text") or "") for x in th.get("next_steps") or [])
    student_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert student_home.status_code == 200, student_home.text
    shome = student_home.json()
    assert not shome.get("stuck_brief")
    skeys = {x.get("key") for x in shome.get("next_steps") or []}
    assert "reading" in skeys


def test_f11_compare_writeback_names_core_literature(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f11cmp")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "对比点名", "keywords": ["cmp"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    a = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F11 Must Cite Paper Title", "abstract": "a", "direction_ids": [did]},
        headers=h,
    ).json()
    b = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F11 Other Graph Paper Name", "abstract": "b", "direction_ids": [did]},
        headers=h,
    ).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "点名稿", "kind": "other", "content_md": "## 引言\n还没有节。", "direction_ids": [did]},
        headers=h,
    ).json()
    fake = '{"table_md":"| a | b |","gap_md":"方法差距来源 %s。","json":{}}' % a["id"]
    with patch("fengsuan.app.services.analysis.chat", return_value=fake):
        cmp = client.post(
            f"/api/v1/labs/{lab}/literature/compare",
            json={"item_ids": [a["id"], b["id"]]},
            headers=h,
        )
    assert cmp.status_code == 200, cmp.text
    rw = cmp.json().get("related_work_md") or ""
    assert "F11 Must Cite Paper Title" in rw
    assert "F11 Other Graph Paper Name" in rw
    stamp = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    ok = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": ms["id"],
            "text": rw,
            "write_mode": "append_heading",
            "heading": "相关工作",
            "expected_updated_at": stamp,
        },
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json().get("related_work_written") is True
    cov = ok.json().get("core_coverage") or {}
    mentioned = {x.get("title") for x in cov.get("core_mentioned") or []}
    assert "F11 Must Cite Paper Title" in mentioned
    assert int(cov.get("core_mentioned_count") or 0) >= 2
