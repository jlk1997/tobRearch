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


def test_f10_compare_writeback_counts_as_related_work(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f10cmp")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "对比", "keywords": ["cmp"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "对比稿", "kind": "other", "content_md": "## 引言\n还没有节。", "direction_ids": [did]},
        headers=h,
    ).json()
    stamp = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    gap = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": ms["id"],
            "text": "COMPARE_GAP_F10",
            "write_mode": "append_heading",
            "heading": "文献对比与差距",
            "expected_updated_at": stamp,
        },
        headers=h,
    )
    assert gap.status_code == 200, gap.text
    assert gap.json().get("related_work_written") is False
    listed = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"related_work": False, "direction_id": did}, headers=h)
    assert listed.status_code == 200, listed.text
    assert ms["id"] in {x["id"] for x in _items(listed.json())}
    stamp2 = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    ok = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": ms["id"],
            "text": "RELATED_FROM_COMPARE",
            "write_mode": "append_heading",
            "heading": "相关工作",
            "expected_updated_at": stamp2,
        },
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json().get("related_work_written") is True
    assert "相关工作" in ((ok.json().get("writeback") or {}).get("summary") or "")
    assert ok.json().get("href") == f"/manuscripts/{ms['id']}"
    still = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"related_work": False, "direction_id": did}, headers=h)
    assert still.status_code == 200, still.text
    assert ms["id"] not in {x["id"] for x in _items(still.json())}
    got = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "RELATED_FROM_COMPARE" in got["content_md"]
    assert "## 相关工作" in got["content_md"]


def test_f10_direction_list_stuck_counts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "f10dir")
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "列表卡住", "keywords": ["stk"], "description": ""},
        headers=h,
    )
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "F10 Unread Paper", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "F10缺", "kind": "other", "content_md": "## 引言\n没有相关工作。", "direction_ids": [did]},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    rows = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert rows.status_code == 200, rows.text
    row = next(x for x in rows.json() if x["id"] == did)
    assert row.get("unread_count", 0) >= 1
    assert row.get("related_work_missing", 0) >= 1
    assert row.get("stuck_count", 0) >= 2
    assert "reading_status=unread" in (row.get("unread_href") or "")
    assert "related_work=0" in (row.get("related_work_missing_href") or "")
    empty = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "空白方向", "keywords": [], "description": ""},
        headers=h,
    )
    assert empty.status_code == 200, empty.text
    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert listed.status_code == 200, listed.text
    ids = [x["id"] for x in listed.json()]
    assert ids.index(did) < ids.index(empty.json()["id"])
