from __future__ import annotations

from fengsuan.app.services.insight import ai_feature_href


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


def _titles(rows) -> str:
    assert isinstance(rows or [], list)
    return " ".join(str((x.get("title") if isinstance(x, dict) else x) or "") for x in (rows or []))


def test_g233_unread_titles_ai_href_and_member_next(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g233unread")
    pid, cid, sib = _tree(client, h, lab)
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Unread Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Unread Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert child_lit["id"] and sib_lit["id"]

    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)

    parent = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": pid}, headers=h)
    assert parent.status_code == 200, parent.text
    brief = parent.json().get("stuck_brief") or {}
    assert "Child Unread Paper" in _titles(brief.get("unread_titles"))
    assert "Sibling Unread Paper" not in _titles(brief.get("unread_titles"))
    unread_chip = next((x for x in brief.get("unread_titles") or [] if x.get("title") == "Child Unread Paper"), {})
    assert child_lit["id"] in (unread_chip.get("href") or "")
    assert "panel=card" not in (unread_chip.get("href") or "")
    no_card_chip = next((x for x in brief.get("no_card_titles") or [] if x.get("title") == "Child Unread Paper"), {})
    assert "panel=card" in (no_card_chip.get("href") or "")

    sib_brief = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": sib}, headers=h).json().get(
        "stuck_brief"
    ) or {}
    assert "Sibling Unread Paper" in _titles(sib_brief.get("unread_titles"))
    assert "Child Unread Paper" not in _titles(sib_brief.get("unread_titles"))

    ghost = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": "missing-dir"}, headers=h).json()
    assert (ghost.get("stuck_brief") or {}).get("unread_titles") == []
    assert int((ghost.get("stuck_brief") or {}).get("unread") or 0) == 0

    digest = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": pid}, headers=h)
    assert digest.status_code == 200, digest.text
    assert "Child Unread Paper" in _titles((digest.json().get("stuck_brief") or {}).get("unread_titles"))

    hub = client.get(f"/api/v1/labs/{lab}/directions/{pid}", headers=h)
    assert hub.status_code == 200, hub.text
    assert "Child Unread Paper" in _titles((hub.json().get("stuck") or {}).get("unread_titles"))
    assert "Sibling Unread Paper" not in _titles((hub.json().get("stuck") or {}).get("unread_titles"))

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)
    mine = members.json()[0]
    assert "reader_id=" in (mine.get("literature_href") or "")
    assert "owner_id=" in (mine.get("manuscripts_href") or "")

    ai = (parent.json().get("situation") or {}).get("ai") or parent.json().get("ai") or {}
    assert (ai.get("keys_href") or "") == "/settings/keys"
    assert ai_feature_href(lab, "writing").endswith("/writing")
    assert ai_feature_href(lab, "tutor").endswith("/literature")
