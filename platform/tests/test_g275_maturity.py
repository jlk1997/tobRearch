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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": email.split("@")[0],
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_g275_stats_lab_wide_surfaces(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g275scope")
    pid, cid, sib = _tree(client, h, lab)
    admin_id = client.get("/api/v1/auth/me", headers=h).json()["id"]
    assert (
        client.put(
            f"/api/v1/labs/{lab}/me/direction",
            json={"direction_id": pid, "direction_ids": [pid]},
            headers=h,
        ).status_code
        == 200
    )

    child_h = _invite_student(client, h, lab, "g275child@example.com")
    child_id = client.get("/api/v1/auth/me", headers=child_h).json()["id"]
    assert (
        client.put(
            f"/api/v1/labs/{lab}/me/direction",
            json={"direction_id": cid, "direction_ids": [cid]},
            headers=child_h,
        ).status_code
        == 200
    )

    sib_h = _invite_student(client, h, lab, "g275sib@example.com")
    sib_id = client.get("/api/v1/auth/me", headers=sib_h).json()["id"]
    assert (
        client.put(
            f"/api/v1/labs/{lab}/me/direction",
            json={"direction_id": sib, "direction_ids": [sib]},
            headers=sib_h,
        ).status_code
        == 200
    )

    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Scope Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Scope Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert child_lit["id"] and sib_lit["id"]

    parent = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": pid}, headers=h)
    assert parent.status_code == 200, parent.text
    body = parent.json()
    assert body.get("ai_scope") == "lab"
    assert body.get("ai_follows_direction") is False
    assert body.get("directions_chart_scope") == "lab"
    assert body.get("directions_follow_filter") is False
    assert body.get("manuscripts_scope") == "lab"
    assert body.get("gpu_scope") == "lab"
    assert body.get("years_scope") == "lab"
    assert body.get("years_follows_direction") is False
    assert body.get("status_scope") == "lab"
    assert "不是本方向" in (body.get("years_note") or "")
    assert body.get("members_scope") == "direction"
    assert body.get("members_follows_direction") is True
    assert body.get("members_counts_scope") == "lab"
    assert "不是只计本方向" in (body.get("members_counts_note") or "")
    assert isinstance(body.get("by_member"), list)
    member_ids = {row.get("user_id") for row in body.get("by_member") or []}
    assert admin_id in member_ids
    assert child_id in member_ids
    assert sib_id not in member_ids
    ai = body.get("ai") or {}
    assert ai.get("follows_direction") is False
    assert ai.get("scope") == "lab"
    assert isinstance(body.get("by_direction"), list)
    assert any((row.get("direction_id") or row.get("id")) == sib for row in body.get("by_direction") or [])

    child = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": cid}, headers=h)
    assert child.status_code == 200, child.text
    child_body = child.json()
    child_ids = {row.get("user_id") for row in child_body.get("by_member") or []}
    assert child_id in child_ids
    assert sib_id not in child_ids
    assert child_body.get("ai_follows_direction") is False
    assert isinstance(child_body.get("by_member"), list)

    ghost = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": "missing-dir"}, headers=h)
    assert ghost.status_code == 200, ghost.text
    ghost_body = ghost.json()
    assert ghost_body.get("stats_direction_valid") is False
    assert ghost_body.get("by_member") == []
    assert isinstance(ghost_body.get("by_member"), list)
    assert ghost_body.get("members_empty_kind") == "filter"
    assert "不是全实验室" in (ghost_body.get("members_empty_text") or "")
    assert ghost_body.get("ai_follows_direction") is False

    whole = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert whole.status_code == 200, whole.text
    whole_body = whole.json()
    assert whole_body.get("members_scope") == "lab"
    assert whole_body.get("members_follows_direction") is False
    whole_ids = {row.get("user_id") for row in whole_body.get("by_member") or []}
    assert {admin_id, child_id, sib_id} <= whole_ids

    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", params={"direction_id": cid}, headers=h)
    assert cockpit.status_code == 200, cockpit.text
    cockpit_body = cockpit.json()
    assert cockpit_body.get("events_scope") == "lab"
    assert cockpit_body.get("events_follows_direction") is False
    cockpit_ids = {row.get("user_id") for row in cockpit_body.get("by_member") or []}
    assert {admin_id, child_id, sib_id} <= cockpit_ids

    digest = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": cid}, headers=h)
    assert digest.status_code == 200, digest.text
    digest_body = digest.json()
    assert digest_body.get("ai_scope") == "lab"
    assert digest_body.get("ai_follows_direction") is False
    digest_ai = digest_body.get("ai") or {}
    assert digest_ai.get("follows_direction") is False
    assert "不是本方向" in (digest_ai.get("direction_note") or "")

    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", params={"direction_id": pid, "days": 7}, headers=h)
    assert csv.status_code == 200, csv.text
    csv_text = csv.text
    assert "ai_scope" in csv_text
    assert "members_scope" in csv_text
    assert "directions_chart_scope" in csv_text
    assert sib_id not in csv_text

    papers = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert papers.status_code == 200, papers.text
    assert isinstance(papers.json(), list)
    assert any(row.get("title") == "Child Scope Paper" for row in papers.json())

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)
    assert {row.get("user_id") for row in members.json()} >= {admin_id, child_id, sib_id}

    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    assert isinstance(notes.json(), list)
