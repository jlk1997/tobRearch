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
            "display_name": "生十八",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _items(payload):
    if isinstance(payload, list):
        return payload
    return payload.get("items") or []


def test_f18_reader_unread_is_personal_not_lab(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f18read")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "该生未读", "keywords": ["rd"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f18read@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    mine = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F18 Student Reading TitleX", "abstract": "a", "direction_ids": [did]},
        headers=h,
    ).json()
    peer = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F18 Teacher Reading TitleX", "abstract": "b", "direction_ids": [did]},
        headers=h,
    ).json()
    idle = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F18 Nobody Reading TitleX", "abstract": "c", "direction_ids": [did]},
        headers=h,
    ).json()
    assert client.post(f"/api/v1/literature/{mine['id']}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    assert client.post(f"/api/v1/literature/{peer['id']}/mark-read", json={"status": "reading"}, headers=h).status_code == 200
    lab_unread = {
        x["id"]
        for x in _items(client.get(f"/api/v1/labs/{lab}/literature", params={"reading_status": "unread"}, headers=h).json())
    }
    assert idle["id"] in lab_unread
    assert mine["id"] not in lab_unread
    assert peer["id"] not in lab_unread
    personal = {
        x["id"]
        for x in _items(
            client.get(
                f"/api/v1/labs/{lab}/literature",
                params={"reading_status": "unread", "reader_id": sid, "direction_id": did},
                headers=h,
            ).json()
        )
    }
    assert mine["id"] not in personal
    assert peer["id"] in personal
    assert idle["id"] in personal


def test_f18_member_stuck_reading_uses_reader_id(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f18card")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "开读链", "keywords": ["cd"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f18card@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    card = next(x for x in cockpit.json().get("members") or [] if x.get("user_id") == sid)
    step = next(x for x in (card.get("stuck") or {}).get("steps") or [] if x.get("key") == "reading")
    href = step.get("href") or ""
    assert f"reader_id={sid}" in href
    assert "reading_status=unread" in href
    assert f"direction_id={did}" in href
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    reading = next(x for x in home.json().get("next_steps") or [] if x.get("key") == "reading")
    assert "reader_id=" not in (reading.get("href") or "")
    assert "reading_status=unread" not in (reading.get("href") or "")
    assert f"direction_id={did}" in (reading.get("href") or "")


def test_f18_write_section_next_and_member_manuscripts_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f18write")
    sh = _invite_student(client, h, lab, "f18write@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "生成后写回", "kind": "other", "content_md": "## 引言\n草稿。"},
        headers=sh,
    ).json()
    with patch("fengsuan.app.api.v1.language.chat", return_value="一段相关工作草稿。"):
        sec = client.post(
            f"/api/v1/labs/{lab}/writing/section",
            json={"heading": "相关工作", "notes": "", "manuscript_id": ms["id"]},
            headers=sh,
        )
    assert sec.status_code == 200, sec.text
    nxt = sec.json().get("next_step") or {}
    assert nxt.get("key") == "writeback"
    assert f"/labs/{lab}/writing?ms={ms['id']}" == nxt.get("href")
    with patch("fengsuan.app.api.v1.language.chat", return_value="未选手稿的草稿。"):
        bare = client.post(
            f"/api/v1/labs/{lab}/writing/section",
            json={"heading": "相关工作", "notes": ""},
            headers=h,
        )
    assert bare.status_code == 200, bare.text
    assert (bare.json().get("next_step") or {}).get("key") == "pick_ms"
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h)
    assert member.status_code == 200, member.text
    assert f"owner_id={sid}" in (member.json().get("manuscripts_href") or "")
    assert member.json().get("manuscripts_label") == "该成员手稿"
