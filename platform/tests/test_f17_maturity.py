from __future__ import annotations


def _items(payload):
    if isinstance(payload, list):
        return payload
    return payload.get("items") or []


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


def _invite_student(client, h, lab: str, email: str, name: str = "生十七"):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": name,
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_f17_owner_filter_and_member_hrefs(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f17own")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "成员本人", "keywords": ["own"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f17own@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    other = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "导师稿", "kind": "other", "content_md": "## 引言\n导师自己的。", "direction_ids": [did]},
        headers=h,
    )
    assert other.status_code == 200, other.text
    mine = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "学生未写", "kind": "other", "content_md": "## 引言\n没有相关工作标题。", "direction_ids": [did]},
        headers=sh,
    )
    assert mine.status_code == 200, mine.text
    listed = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"owner_id": sid, "related_work": False}, headers=h)
    assert listed.status_code == 200, listed.text
    ids = {x.get("id") for x in _items(listed.json())}
    assert mine.json()["id"] in ids
    assert other.json()["id"] not in ids
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    row = next(x for x in st.json().get("by_member") or [] if x.get("user_id") == sid)
    assert f"owner_id={sid}" in (row.get("related_work_href") or "")
    assert "related_work=0" in (row.get("related_work_href") or "")
    assert f"owner_id={sid}" in (row.get("core_missing_href") or "")
    assert f"owner_id={sid}" in (row.get("ungrounded_href") or "")


def test_f17_honesty_href_ungrounded_when_no_not_run(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f17hon")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "诚实点对", "keywords": ["hon"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F17 Honesty Core Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "诚实未测",
            "kind": "other",
            "content_md": "## 相关工作\nF17 Honesty Core Paper Title 已写入。我们成功完成实验。",
            "direction_ids": [did],
        },
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    honesty = next(x for x in (st.json().get("situation") or {}).get("lines") or [] if x.get("key") == "honesty")
    assert honesty.get("empty") is False
    assert "ungrounded=1" in (honesty.get("href") or "")
    assert "reproduces" not in (honesty.get("href") or "")


def test_f17_manuscript_page_next_and_hub_labels(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f17page")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "对象页下一步", "keywords": ["pg"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F17 Page Core Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    ).json()
    ug = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "对象未测",
            "kind": "other",
            "content_md": "## 相关工作\nF17 Page Core Paper Title 已写入。我们成功完成实验。",
            "direction_ids": [did],
        },
        headers=h,
    ).json()
    got = client.get(f"/api/v1/manuscripts/{ug['id']}", headers=h)
    assert got.status_code == 200, got.text
    nxt = got.json().get("next_step") or {}
    assert nxt.get("key") == "ungrounded"
    assert f"/manuscripts/{ug['id']}" not in (nxt.get("href") or "")
    assert "reproduces" in (nxt.get("href") or "")
    assert paper["id"] in (nxt.get("href") or "")
    claims = client.get(f"/api/v1/manuscripts/{ug['id']}/claims", headers=h)
    assert claims.status_code == 200, claims.text
    assert (claims.json().get("next_step") or {}).get("key") == "ungrounded"

    core_only = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "只缺点名", "kind": "other", "content_md": "## 相关工作\n一段没有题名。", "direction_ids": [did]},
        headers=h,
    ).json()
    cn = client.get(f"/api/v1/manuscripts/{core_only['id']}", headers=h).json().get("next_step") or {}
    assert cn.get("key") == "core"
    assert f"/labs/{lab}/writing?ms={core_only['id']}" == cn.get("href")

    client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "枢纽未写", "kind": "other", "content_md": "## 引言\n没有相关工作标题。", "direction_ids": [did]},
        headers=h,
    )
    hub = client.get(f"/api/v1/labs/{lab}/directions/{did}", headers=h)
    assert hub.status_code == 200, hub.text
    body = hub.json()
    assert body.get("manuscripts_label") == "未写相关工作"
    assert "related_work=0" in (body.get("manuscripts_href") or "")
    assert f"direction_id={did}" in (body.get("manuscripts_href") or "")


def test_f17_cockpit_member_ungrounded_opens_manuscript(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f17card")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "成员卡", "keywords": ["cd"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f17card@example.com", "卡十七")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F17 Card Core Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "成员未测",
            "kind": "other",
            "content_md": "## 相关工作\nF17 Card Core Paper Title 已写入。我们成功完成实验。",
            "direction_ids": [did],
        },
        headers=sh,
    )
    assert ms.status_code == 200, ms.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    card = next(x for x in cockpit.json().get("members") or [] if x.get("user_id") == sid)
    ug = next(x for x in (card.get("stuck") or {}).get("steps") or [] if x.get("key") == "ungrounded")
    assert ug.get("href") == f"/manuscripts/{ms.json()['id']}"
