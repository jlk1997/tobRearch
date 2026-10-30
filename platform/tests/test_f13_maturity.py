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
            "display_name": "生十三",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_f13_hub_core_count_matches_manuscript_filter(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f13hub")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "核心口径", "keywords": ["core"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F13 First Core Paper Title", "abstract": "a", "direction_ids": [did]},
        headers=h,
    )
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F13 Second Core Paper Name", "abstract": "b", "direction_ids": [did]},
        headers=h,
    )
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F13一稿两核", "kind": "other", "content_md": "## 相关工作\n尚未点名。", "direction_ids": [did]},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    listed = client.get(
        f"/api/v1/labs/{lab}/manuscripts",
        params={"direction_id": did, "core_missing": True},
        headers=h,
    )
    assert listed.status_code == 200, listed.text
    assert {x["id"] for x in listed.json()} == {ms.json()["id"]}
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    core = next(x for x in (ov.json().get("stuck") or {}).get("lines") or [] if x.get("key") == "core")
    assert core.get("count") == 1
    assert core.get("core_missing_papers", 0) >= 2
    assert "1 篇手稿" in (core.get("text") or "")
    dirs = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    drow = next(x for x in dirs.json() if x["id"] == did)
    assert drow.get("core_missing") == 1


def test_f13_writing_writeback_stamps_selected_titles(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f13wrt")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "写作点名", "keywords": ["w"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F13 Must Cite Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    ).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "写作稿", "kind": "other", "content_md": "## 引言\n还没有节。", "direction_ids": [did]},
        headers=h,
    ).json()
    with patch("fengsuan.app.api.v1.language.chat", return_value="一段没有题名的综述。"):
        sec = client.post(
            f"/api/v1/labs/{lab}/writing/section",
            json={"heading": "相关工作", "notes": "", "literature_item_ids": [paper["id"]], "manuscript_id": ms["id"]},
            headers=h,
        )
    assert sec.status_code == 200, sec.text
    assert "F13 Must Cite Paper Title" in (sec.json().get("text") or "")
    stamp = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    ok = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": ms["id"],
            "text": "RELATED_NO_TITLE_F13",
            "write_mode": "append_heading",
            "heading": "相关工作",
            "expected_updated_at": stamp,
            "literature_item_ids": [paper["id"]],
        },
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json().get("related_work_written") is True
    mentioned = {x.get("title") for x in (ok.json().get("core_coverage") or {}).get("core_mentioned") or []}
    assert "F13 Must Cite Paper Title" in mentioned
    body = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "F13 Must Cite Paper Title" in (body.get("content_md") or "")


def test_f13_student_reading_href_is_direction_library(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f13read")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "开读", "keywords": ["r"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f13stu@example.com")
    me = client.get("/api/v1/auth/me", headers=sh)
    assert me.status_code == 200, me.text
    sid = me.json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    reading = next(x for x in home.json().get("next_steps") or [] if x.get("key") == "reading")
    assert "reading_status=unread" not in (reading.get("href") or "")
    assert f"direction_id={did}" in (reading.get("href") or "")
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    stu = next(x for x in cockpit.json().get("members") or [] if x.get("user_id") == sid)
    step = next(x for x in (stu.get("stuck") or {}).get("steps") or [] if x.get("key") == "reading")
    assert f"reader_id={sid}" in (step.get("href") or "")
    assert "reading_status=unread" in (step.get("href") or "")
    assert f"direction_id={did}" in (step.get("href") or "")
