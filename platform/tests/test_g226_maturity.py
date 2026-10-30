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


def _titles(rows) -> str:
    assert isinstance(rows or [], list)
    return " ".join(str((x.get("title") if isinstance(x, dict) else x) or "") for x in (rows or []))


def test_g226_chain_leftover_titles_follow_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g226chain")
    pid, cid, sib = _tree(client, h, lab)
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Gap Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Gap Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert (
        client.patch(
            f"/api/v1/labs/{lab}/literature/{child_lit['id']}/core",
            json={"direction_id": cid, "is_core": True},
            headers=h,
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v1/labs/{lab}/literature/{sib_lit['id']}/core",
            json={"direction_id": sib, "is_core": True},
            headers=h,
        ).status_code
        == 200
    )
    child_ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Child Gap Draft", "kind": "other", "content_md": "引言草稿。", "direction_ids": [cid]},
        headers=h,
    )
    sib_ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Sibling Gap Draft", "kind": "other", "content_md": "并列引言。", "direction_ids": [sib]},
        headers=h,
    )
    assert child_ms.status_code == 200, child_ms.text
    assert sib_ms.status_code == 200, sib_ms.text

    listed_ms = client.get(f"/api/v1/labs/{lab}/manuscripts", headers=h)
    assert listed_ms.status_code == 200, listed_ms.text
    assert isinstance(listed_ms.json(), list)

    listed_lit = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert listed_lit.status_code == 200, listed_lit.text
    assert isinstance(listed_lit.json(), list)

    parent = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": pid}, headers=h)
    assert parent.status_code == 200, parent.text
    brief = parent.json().get("stuck_brief") or {}
    assert "unused_titles" in brief
    assert brief.get("no_card_titles") is not brief.get("unused_titles")
    assert brief.get("no_card_titles") is not brief.get("not_run_titles")
    assert "Child Gap Paper" in _titles(brief.get("no_card_titles"))
    assert "Sibling Gap Paper" not in _titles(brief.get("no_card_titles"))
    assert "Child Gap Draft" in _titles(brief.get("related_work_titles"))
    assert "Sibling Gap Draft" not in _titles(brief.get("related_work_titles"))
    assert "Child Gap Draft" in _titles(brief.get("core_missing_titles"))
    assert "Sibling Gap Draft" not in _titles(brief.get("core_missing_titles"))
    child_chip = next((x for x in brief.get("no_card_titles") or [] if x.get("title") == "Child Gap Paper"), {})
    assert "panel=card" in (child_chip.get("href") or "")
    assert child_lit["id"] in (child_chip.get("href") or "")

    sib_body = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": sib}, headers=h).json()
    sib_brief = sib_body.get("stuck_brief") or {}
    assert "Sibling Gap Paper" in _titles(sib_brief.get("no_card_titles"))
    assert "Child Gap Paper" not in _titles(sib_brief.get("no_card_titles"))
    assert "Sibling Gap Draft" in _titles(sib_brief.get("related_work_titles"))
    assert "Child Gap Draft" not in _titles(sib_brief.get("related_work_titles"))

    ghost = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": "missing-dir"}, headers=h).json()
    ghost_brief = ghost.get("stuck_brief") or {}
    assert ghost_brief.get("no_card_titles") == []
    assert ghost_brief.get("related_work_titles") == []
    assert ghost_brief.get("core_missing_titles") == []
    assert int(ghost_brief.get("no_card") or 0) == 0

    digest = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": pid}, headers=h)
    assert digest.status_code == 200, digest.text
    dbrief = digest.json().get("stuck_brief") or {}
    assert "Child Gap Paper" in _titles(dbrief.get("no_card_titles"))
    assert "Sibling Gap Paper" not in _titles(dbrief.get("no_card_titles"))

    hub = client.get(f"/api/v1/labs/{lab}/directions/{pid}", headers=h)
    assert hub.status_code == 200, hub.text
    hstuck = hub.json().get("stuck") or {}
    assert "Child Gap Paper" in _titles(hstuck.get("no_card_titles"))
    assert "Sibling Gap Paper" not in _titles(hstuck.get("no_card_titles"))
    assert "Child Gap Draft" in _titles(hstuck.get("related_work_titles"))
    assert "Sibling Gap Draft" not in _titles(hstuck.get("related_work_titles"))
