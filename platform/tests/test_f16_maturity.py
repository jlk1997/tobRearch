from __future__ import annotations

from unittest.mock import patch


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
            "display_name": "生十六",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_f16_writeback_next_step_core_then_ungrounded(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f16wb")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "写回下一步", "keywords": ["nx"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F16 Must Cite Next Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    ).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "下一步稿", "kind": "other", "content_md": "## 引言\n还没有节。", "direction_ids": [did]},
        headers=h,
    ).json()
    core = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": ms["id"],
            "text": "一段没有题名的相关工作。",
            "write_mode": "append_heading",
            "heading": "相关工作",
            "expected_updated_at": ms["updated_at"],
        },
        headers=h,
    )
    assert core.status_code == 200, core.text
    nxt = core.json().get("next_step") or {}
    assert nxt.get("key") == "core"
    assert "点名" in (nxt.get("label") or "")
    assert f"/manuscripts/{ms['id']}?tab=claims" == nxt.get("href")

    stamp = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    ug = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": ms["id"],
            "text": "F16 Must Cite Next Title。我们成功完成实验。",
            "write_mode": "append_heading",
            "heading": "相关工作",
            "expected_updated_at": stamp,
            "literature_item_ids": [paper["id"]],
        },
        headers=h,
    )
    assert ug.status_code == 200, ug.text
    nxt = ug.json().get("next_step") or {}
    assert nxt.get("key") == "ungrounded"
    assert nxt.get("href") == f"/manuscripts/{ms['id']}"
    assert "ungrounded=1" not in (nxt.get("href") or "")


def test_f16_language_expand_returns_next_step(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f16lang")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "拓展下一步", "keywords": ["ex"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F16 Expand Core Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "拓展稿", "kind": "other", "content_md": "## 引言\n还没有节。", "direction_ids": [did]},
        headers=h,
    ).json()
    with patch("fengsuan.app.api.v1.language.chat", return_value="一段没有题名的拓展。"):
        exp = client.post(
            f"/api/v1/labs/{lab}/language/expand",
            json={
                "text": "补一段相关工作。",
                "expand_goal": "related_work",
                "manuscript_id": ms["id"],
                "write_mode": "append",
                "heading": "语言工作台",
                "expected_updated_at": ms["updated_at"],
            },
            headers=h,
        )
    assert exp.status_code == 200, exp.text
    body = exp.json()
    assert body.get("wrote") is True
    assert body.get("related_work_written") is True
    nxt = body.get("next_step") or {}
    assert nxt.get("key") == "core"
    assert f"/manuscripts/{ms['id']}?tab=claims" == nxt.get("href")


def test_f16_student_home_list_hrefs_keep_direction_and_stuck(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f16home")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "首页链接", "keywords": ["home"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f16home@example.com")
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "学生未写", "kind": "other", "content_md": "## 引言\n没有相关工作标题。", "direction_ids": [did]},
        headers=sh,
    )
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    data = home.json()
    assert f"direction_id={did}" in (data.get("reading_href") or "")
    assert "related_work=0" in (data.get("manuscripts_href") or "")
    assert f"direction_id={did}" in (data.get("manuscripts_href") or "")
    assert data.get("manuscripts_label") == "未写相关工作"
    assert data.get("reading_label") == "全部"


def test_f16_teacher_unread_reading_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f16teach")
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F16 Unread Paper Title", "abstract": "abs"},
        headers=h,
    )
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    data = home.json()
    assert "reading_status=unread" in (data.get("reading_href") or "")
    assert data.get("reading_label") == "无人在读"
