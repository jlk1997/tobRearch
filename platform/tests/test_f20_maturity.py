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
            "display_name": "生二十",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _items(payload):
    if isinstance(payload, list):
        return payload
    return payload.get("items") or []


def test_f20_create_manuscript_next_and_outline_does_not_create(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f20ms")
    before = client.get(f"/api/v1/labs/{lab}/manuscripts", headers=h)
    assert before.status_code == 200, before.text
    n0 = len(before.json() if isinstance(before.json(), list) else before.json().get("items") or before.json())
    ol = client.post(f"/api/v1/labs/{lab}/writing/outline", json={"title": "综述", "kind": "survey"}, headers=h)
    assert ol.status_code == 200, ol.text
    assert (ol.json().get("next_step") or {}).get("key") == "create_ms"
    after_ol = client.get(f"/api/v1/labs/{lab}/manuscripts", headers=h)
    assert after_ol.status_code == 200, after_ol.text
    n1 = len(after_ol.json() if isinstance(after_ol.json(), list) else after_ol.json().get("items") or after_ol.json())
    assert n1 == n0
    created = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "从大纲来的", "kind": "survey", "content_md": "## 相关工作\n待写。"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    nxt = created.json().get("next_step") or {}
    assert nxt.get("key") == "related_work"
    assert f"/labs/{lab}/writing?ms={created.json()['id']}" == nxt.get("href")


def test_f20_card_and_stats_member_scope_hrefs(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f20card")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "成员卡链", "keywords": ["cd"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f20card@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    card = next(x for x in cockpit.json().get("members") or [] if x.get("user_id") == sid)
    assert f"reader_id={sid}" in (card.get("literature_href") or "")
    assert "reading_status=unread" in (card.get("literature_href") or "")
    assert f"direction_id={did}" in (card.get("literature_href") or "")
    assert card.get("literature_label") == "该成员尚未开读"
    assert f"owner_id={sid}" in (card.get("manuscripts_href") or "")
    assert card.get("manuscripts_label") == "该成员手稿"
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    row = next(x for x in st.json().get("by_member") or [] if x.get("user_id") == sid)
    assert f"reader_id={sid}" in (row.get("literature_href") or "")
    assert "reading_status=unread" in (row.get("literature_href") or "")
    assert f"owner_id={sid}" in (row.get("manuscripts_href") or "")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F20 Member Reading TitleX", "abstract": "c", "direction_ids": [did]},
        headers=h,
    ).json()
    assert client.post(f"/api/v1/literature/{paper['id']}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    now = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    card2 = next(x for x in now.json().get("members") or [] if x.get("user_id") == sid)
    assert "reading_status=reading" in (card2.get("literature_href") or "")
    assert card2.get("literature_label") == "该成员在读"
    detail = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h)
    assert detail.status_code == 200, detail.text
    assert (detail.json().get("literature_href") or "") == (card2.get("literature_href") or "")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    reading = next((x for x in home.json().get("next_steps") or [] if x.get("key") == "reading"), None)
    if reading:
        assert "reader_id=" not in (reading.get("href") or "")
        assert "reading_status=unread" not in (reading.get("href") or "")
    other = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F20 Group Unread TitleX", "abstract": "d", "direction_ids": [did]},
        headers=h,
    ).json()
    lab_unread = {
        x["id"]
        for x in _items(client.get(f"/api/v1/labs/{lab}/literature", params={"reading_status": "unread"}, headers=h).json())
    }
    assert other["id"] in lab_unread
    assert paper["id"] not in lab_unread
