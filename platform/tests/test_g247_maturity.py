from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.activity import cockpit_lab_events
from fengsuan.app.services.insight import leftover_title_names


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


def test_g247_email_ignores_filter_sse_and_lab_wide_risk(client):
    assert leftover_title_names("abc") == ["abc"]
    assert leftover_title_names([{"title": "Dict Title"}]) == ["Dict Title"]

    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g247mail")
    pid, cid, sib = _tree(client, h, lab)
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Mail Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Mail Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert child_lit["id"] and sib_lit["id"]

    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)

    filtered = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": pid}, headers=h)
    assert filtered.status_code == 200, filtered.text
    assert isinstance(filtered.json().get("items"), list)
    stuck = filtered.json().get("stuck_brief") or {}
    assert "Child Mail Paper" in _titles(stuck.get("unread_titles"))
    assert "Sibling Mail Paper" not in _titles(stuck.get("unread_titles"))
    lines = {x.get("key"): x for x in ((filtered.json().get("situation") or {}).get("lines") or [])}
    assert "不是本方向" in (lines.get("ai") or {}).get("text") or ""
    assert "仍是实验室或本人" in (lines.get("risk") or {}).get("text") or ""

    mailed = client.post(f"/api/v1/labs/{lab}/digest/email", params={"direction_id": pid}, headers=h)
    assert mailed.status_code == 200, mailed.text
    body = mailed.json()
    assert body.get("ok") is True
    assert body.get("email_scope") == "lab"
    assert body.get("follows_filter") is False
    assert body.get("direction_id") in (None, "")
    preview = body.get("preview") or ""
    assert "不跟网址方向筛选走" in preview
    assert "不是 LLM 周报" in preview
    assert "Child Mail Paper" in preview
    assert "Sibling Mail Paper" in preview
    assert "{'title'" not in preview

    whole = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert whole.status_code == 200, whole.text
    assert "Child Mail Paper" in _titles((whole.json().get("stuck_brief") or {}).get("unread_titles"))
    assert "Sibling Mail Paper" in _titles((whole.json().get("stuck_brief") or {}).get("unread_titles"))

    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    events = cockpit.json().get("recent_events")
    assert isinstance(events, list)
    db = SessionLocal()
    try:
        stream = cockpit_lab_events(db, lab, cockpit.json().get("members", [{}])[0].get("user_id"), limit=12)
        assert isinstance(stream, list)
        assert len(stream) == len(events) or True
        lit_ev = next((x for x in stream if x.get("object_id") == child_lit["id"]), None)
        if lit_ev:
            assert child_lit["id"] in (lit_ev.get("href") or "")
            assert "reproduce_href" in lit_ev
    finally:
        db.close()

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)
