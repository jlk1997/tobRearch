from __future__ import annotations

from fengsuan.app.services.insight import leftover_title_rows
from fengsuan.app.services.writing import is_related_work_heading


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
            "display_name": "闲读生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _tree(client, h, lab: str):
    pid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    cid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "多模态", "parent_id": pid, "keywords": ["multimodalkw"]},
        headers=h,
    ).json()["id"]
    sib = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "并列方向", "keywords": ["sibkw"]},
        headers=h,
    ).json()["id"]
    return pid, cid, sib


def _titles(rows) -> str:
    assert isinstance(rows or [], list)
    return " ".join(str((x.get("title") if isinstance(x, dict) else x) or "") for x in (rows or []))


def test_g240_idle_unread_heading_and_leftover(client):
    assert is_related_work_heading("Related Works")
    assert is_related_work_heading("相关工作")
    assert is_related_work_heading("## 相关工作")
    assert is_related_work_heading("\\section{Related Work}")
    assert not is_related_work_heading("还没有相关工作标题。")
    assert not is_related_work_heading("草稿没有相关工作。")

    rows, more = leftover_title_rows("abc")
    assert more == 0
    assert rows == [{"title": "abc", "href": ""}]
    assert leftover_title_rows(["abc"])[0] == [{"title": "abc", "href": ""}]
    triple, _ = leftover_title_rows([("id1", "Triple Title", "/open/id1")])
    assert triple == [{"title": "Triple Title", "href": "/open/id1"}]

    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g240idle")
    pid, cid, sib = _tree(client, h, lab)
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Idle Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Idle Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert child_lit["id"] and sib_lit["id"]
    marked = client.post(
        f"/api/v1/literature/{child_lit['id']}/mark-read",
        json={"status": "reading"},
        headers=h,
    )
    assert marked.status_code == 200, marked.text

    sh = _invite_student(client, h, lab, "g240stu@example.com")
    me = client.get("/api/v1/auth/me", headers=sh)
    assert me.status_code == 200, me.text
    uid = me.json()["id"]
    hung = client.patch(
        f"/api/v1/labs/{lab}/members/{uid}",
        json={"direction_ids": [cid], "direction_id": cid},
        headers=h,
    )
    assert hung.status_code == 200, hung.text

    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)

    hub = client.get(f"/api/v1/labs/{lab}/directions/{pid}", headers=h)
    assert hub.status_code == 200, hub.text
    stuck = hub.json().get("stuck") or {}
    idle = stuck.get("idle_members") or []
    assert isinstance(idle, list)
    mine = next((x for x in idle if x.get("user_id") == uid), None)
    assert mine is not None
    assert mine.get("idle_unread_titles") is not stuck.get("unread_titles")
    assert "Child Idle Paper" in _titles(mine.get("idle_unread_titles"))
    assert "Sibling Idle Paper" not in _titles(mine.get("idle_unread_titles"))
    assert "Child Idle Paper" not in _titles(stuck.get("unread_titles"))
    assert "reader_id=" in (mine.get("literature_href") or "")
    assert "reading_status=unread" in (mine.get("literature_href") or "")
    assert f"direction_id={pid}" in (mine.get("literature_href") or "")
    chip = next((x for x in mine.get("idle_unread_titles") or [] if x.get("title") == "Child Idle Paper"), {})
    assert child_lit["id"] in (chip.get("href") or "")
    assert "panel=card" not in (chip.get("href") or "")

    sib_hub = client.get(f"/api/v1/labs/{lab}/directions/{sib}", headers=h)
    assert sib_hub.status_code == 200, sib_hub.text
    sib_idle = (sib_hub.json().get("stuck") or {}).get("idle_members") or []
    assert uid not in {x.get("user_id") for x in sib_idle}
    assert "Child Idle Paper" not in _titles((sib_hub.json().get("stuck") or {}).get("unread_titles"))
    assert "Sibling Idle Paper" in _titles((sib_hub.json().get("stuck") or {}).get("unread_titles"))

    ghost = client.get(f"/api/v1/labs/{lab}/directions/missing-dir", headers=h)
    assert ghost.status_code == 404

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)

    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    reading = next((x for x in (home.json().get("next_steps") or []) if x.get("key") == "reading"), None)
    if reading:
        href = reading.get("href") or ""
        assert f"direction_id={cid}" in href
        assert f"direction_id={pid}" not in href
        assert "reader_id=" not in href
        assert "reading_status=unread" not in href
