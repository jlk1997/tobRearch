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


def test_f9_list_filters_match_stuck_hrefs(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f9flt")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "过滤", "keywords": ["flt"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    unread = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F9 Unread Only", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    assert unread.status_code == 200, unread.text
    unread_id = unread.json()["id"]
    reading = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F9 Being Read", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    assert reading.status_code == 200, reading.text
    reading_id = reading.json()["id"]
    mark = client.post(f"/api/v1/literature/{reading_id}/mark-read", json={"status": "reading"}, headers=h)
    assert mark.status_code == 200, mark.text
    missing = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F9缺相关", "kind": "other", "content_md": "## 引言\n没有相关工作。", "direction_ids": [did]},
        headers=h,
    )
    assert missing.status_code == 200, missing.text
    written = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F9已写", "kind": "other", "content_md": "## 相关工作\n已写。", "direction_ids": [did]},
        headers=h,
    )
    assert written.status_code == 200, written.text

    def _items(body):
        return body if isinstance(body, list) else body.get("items") or []

    lit = client.get(f"/api/v1/labs/{lab}/literature", params={"reading_status": "unread", "direction_id": did}, headers=h)
    assert lit.status_code == 200, lit.text
    unread_ids = {x["id"] for x in _items(lit.json())}
    assert unread_id in unread_ids
    assert reading_id not in unread_ids

    no_card = client.get(f"/api/v1/labs/{lab}/literature", params={"has_card": False, "direction_id": did}, headers=h)
    assert no_card.status_code == 200, no_card.text
    assert {x["id"] for x in _items(no_card.json())} >= {unread_id, reading_id}

    ms_missing = client.get(
        f"/api/v1/labs/{lab}/manuscripts",
        params={"related_work": False, "direction_id": did},
        headers=h,
    )
    assert ms_missing.status_code == 200, ms_missing.text
    missing_ids = {x["id"] for x in ms_missing.json()}
    assert missing.json()["id"] in missing_ids
    assert written.json()["id"] not in missing_ids
    assert any(x.get("related_work_written") is False for x in ms_missing.json())

    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    hrefs = {x["key"]: x.get("href") or "" for x in (ov.json().get("stuck") or {}).get("lines") or []}
    assert "reading_status=unread" in hrefs.get("unread", "")
    assert "has_card=0" in hrefs.get("no_card", "")
    assert "related_work=0" in hrefs.get("related_work", "")

    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    drow = next(x for x in st.json().get("by_direction") or [] if x.get("direction_id") == did)
    assert "reading_status=unread" in (drow.get("unread_href") or "")
    assert "related_work=0" in (drow.get("related_work_missing_href") or "")
    brief = st.json().get("stuck_brief") or {}
    assert "reading_status=unread" in (brief.get("unread_href") or "")
    assert "related_work=0" in (brief.get("related_work_href") or "")
