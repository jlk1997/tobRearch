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
            "display_name": "生十九",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _items(payload):
    if isinstance(payload, list):
        return payload
    return payload.get("items") or []


def test_f19_outline_next_create_or_related_work(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f19ol")
    bare = client.post(f"/api/v1/labs/{lab}/writing/outline", json={"title": "综述", "kind": "survey"}, headers=h)
    assert bare.status_code == 200, bare.text
    nxt = bare.json().get("next_step") or {}
    assert nxt.get("key") == "create_ms"
    assert f"/labs/{lab}/writing" == nxt.get("href")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "大纲后写相关工作", "kind": "other", "content_md": "## 引言\n草稿。"},
        headers=h,
    ).json()
    with_ms = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "manuscript_id": ms["id"]},
        headers=h,
    )
    assert with_ms.status_code == 200, with_ms.text
    step = with_ms.json().get("next_step") or {}
    assert step.get("key") == "related_work"
    assert f"/labs/{lab}/writing?ms={ms['id']}" == step.get("href")


def test_f19_literature_empty_next_personal_and_group(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f19emp")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "空态", "keywords": ["em"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f19emp@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F19 Only Paper TitleX", "abstract": "a", "direction_ids": [did]},
        headers=h,
    ).json()
    assert client.post(f"/api/v1/literature/{paper['id']}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    personal = client.get(
        f"/api/v1/labs/{lab}/literature",
        params={"reading_status": "unread", "reader_id": sid, "direction_id": did, "page": 1},
        headers=h,
    )
    assert personal.status_code == 200, personal.text
    body = personal.json()
    assert _items(body) == []
    nxt = body.get("empty_next") or {}
    assert "未开读" in (nxt.get("title") or "")
    keys = {x.get("key") for x in nxt.get("steps") or []}
    assert "search" in keys and "library" in keys
    assert any("tab=search" in (x.get("href") or "") for x in nxt.get("steps") or [])
    assert any(f"direction_id={did}" in (x.get("href") or "") for x in nxt.get("steps") or [])
    peer = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F19 Teacher Reading TitleX", "abstract": "b", "direction_ids": [did]},
        headers=h,
    ).json()
    assert client.post(f"/api/v1/literature/{peer['id']}/mark-read", json={"status": "reading"}, headers=h).status_code == 200
    group = client.get(
        f"/api/v1/labs/{lab}/literature",
        params={"reading_status": "unread", "direction_id": did, "page": 1},
        headers=h,
    )
    assert group.status_code == 200, group.text
    gbody = group.json()
    assert paper["id"] not in {x["id"] for x in _items(gbody)}
    assert peer["id"] not in {x["id"] for x in _items(gbody)}
    gnxt = gbody.get("empty_next") or {}
    assert "无人在读" in (gnxt.get("title") or "")
    assert [x.get("key") for x in gnxt.get("steps") or []] == ["search"]
    filled = client.get(
        f"/api/v1/labs/{lab}/literature",
        params={"reading_status": "unread", "reader_id": sid, "direction_id": did, "page": 1},
        headers=h,
    )
    assert filled.status_code == 200, filled.text
    assert peer["id"] in {x["id"] for x in _items(filled.json())}
    assert not filled.json().get("empty_next")


def test_f19_member_literature_href_and_f18_regressions(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f19mem")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "成员文献", "keywords": ["ml"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f19mem@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    idle = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h)
    assert idle.status_code == 200, idle.text
    ihref = idle.json().get("literature_href") or ""
    assert f"reader_id={sid}" in ihref
    assert "reading_status=unread" in ihref
    assert f"direction_id={did}" in ihref
    assert idle.json().get("literature_label") == "该成员尚未开读"
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    card = next(x for x in cockpit.json().get("members") or [] if x.get("user_id") == sid)
    step = next(x for x in (card.get("stuck") or {}).get("steps") or [] if x.get("key") == "reading")
    href = step.get("href") or ""
    assert f"reader_id={sid}" in href
    assert "reading_status=unread" in href
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    reading = next(x for x in home.json().get("next_steps") or [] if x.get("key") == "reading")
    assert "reader_id=" not in (reading.get("href") or "")
    assert "reading_status=unread" not in (reading.get("href") or "")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F19 Member Reading TitleX", "abstract": "c", "direction_ids": [did]},
        headers=h,
    ).json()
    assert client.post(f"/api/v1/literature/{paper['id']}/mark-read", json={"status": "reading"}, headers=sh).status_code == 200
    now = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h)
    assert now.status_code == 200, now.text
    nhref = now.json().get("literature_href") or ""
    assert f"reader_id={sid}" in nhref
    assert "reading_status=reading" in nhref
    assert f"direction_id={did}" in nhref
    assert now.json().get("literature_label") == "该成员在读"
    other = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F19 Group Unread TitleX", "abstract": "d", "direction_ids": [did]},
        headers=h,
    ).json()
    lab_unread = {
        x["id"]
        for x in _items(client.get(f"/api/v1/labs/{lab}/literature", params={"reading_status": "unread"}, headers=h).json())
    }
    assert other["id"] in lab_unread
    assert paper["id"] not in lab_unread
