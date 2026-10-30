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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "生十二",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_f12_core_missing_filter_and_hub_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f12flt")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "核心过滤", "keywords": ["core"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F12 Must Cite Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    gap = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F12缺核心", "kind": "other", "content_md": "## 相关工作\n还没点名。", "direction_ids": [did]},
        headers=h,
    )
    assert gap.status_code == 200, gap.text
    named = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "F12已点名",
            "kind": "other",
            "content_md": "## 相关工作\nF12 Must Cite Paper Title 已写入。",
            "direction_ids": [did],
        },
        headers=h,
    )
    assert named.status_code == 200, named.text
    listed = client.get(
        f"/api/v1/labs/{lab}/manuscripts",
        params={"direction_id": did, "core_missing": True},
        headers=h,
    )
    assert listed.status_code == 200, listed.text
    ids = {x["id"] for x in _items(listed.json())}
    assert gap.json()["id"] in ids
    assert named.json()["id"] not in ids
    assert next(x for x in _items(listed.json()) if x["id"] == gap.json()["id"]).get("core_missing_count", 0) >= 1
    written = client.get(
        f"/api/v1/labs/{lab}/manuscripts",
        params={"direction_id": did, "related_work": False},
        headers=h,
    )
    assert written.status_code == 200, written.text
    assert gap.json()["id"] not in {x["id"] for x in _items(written.json())}
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    core = next(x for x in (ov.json().get("stuck") or {}).get("lines") or [] if x.get("key") == "core")
    assert "core_missing=1" in (core.get("href") or "")


def test_f12_direction_stuck_matches_stats(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f12dir")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "对齐", "keywords": ["align"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F12 Unread Align Paper", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F12对齐稿", "kind": "other", "content_md": "## 引言\n草稿。", "direction_ids": [did]},
        headers=h,
    )
    dirs = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert dirs.status_code == 200, dirs.text
    drow = next(x for x in dirs.json() if x["id"] == did)
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    srow = next(x for x in st.json().get("by_direction") or [] if x.get("direction_id") == did)
    assert drow.get("stuck_count") == srow.get("stuck_count")
    assert drow.get("unread_count") == srow.get("unread_count")
    assert drow.get("related_work_missing") == srow.get("related_work_missing")
    assert drow.get("core_missing", 0) >= 1
    assert "core_missing=1" in (drow.get("core_missing_href") or "")
    brief = st.json().get("stuck_brief") or {}
    assert brief.get("core_missing", 0) >= 1
    assert "core_missing=1" in (brief.get("core_missing_href") or "")
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", headers=h)
    assert csv.status_code == 200, csv.text
    assert "core_missing" in csv.text


def test_f12_member_stuck_core_after_related_work(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f12mem")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "成员核心", "keywords": ["mem"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    sh = _invite_student(client, h, lab, "f12stu@example.com")
    me = client.get("/api/v1/auth/me", headers=sh)
    assert me.status_code == 200, me.text
    sid = me.json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F12 Member Core Paper Title", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    mark = client.post(f"/api/v1/literature/{lit.json()['id']}/mark-read", json={"status": "reading"}, headers=sh)
    assert mark.status_code == 200, mark.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F12成员稿", "kind": "other", "content_md": "## 相关工作\n尚未点名。", "direction_ids": [did]},
        headers=sh,
    )
    assert ms.status_code == 200, ms.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    stu = next(x for x in cockpit.json().get("members") or [] if x.get("user_id") == sid)
    keys = {x.get("key") for x in (stu.get("stuck") or {}).get("steps") or []}
    assert "related_work" not in keys
    assert "core" in keys
    detail = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h)
    assert detail.status_code == 200, detail.text
    dkeys = {x.get("key") for x in (detail.json().get("stuck") or {}).get("steps") or []}
    assert "core" in dkeys
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    assert any((x.get("key") or "").startswith("core") for x in home.json().get("next_steps") or [])
