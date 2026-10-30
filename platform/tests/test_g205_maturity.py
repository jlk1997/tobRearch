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


def _summaries(body: dict) -> str:
    items = body.get("items")
    assert isinstance(items, list)
    return " ".join(str(x.get("summary") or "") for x in items)


def test_g205_digest_items_follow_object_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g205digest")
    pid, cid, sib = _tree(client, h, lab)
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Digest Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Digest Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert child_lit["id"] and sib_lit["id"]
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Sibling Digest Manuscript", "content_md": "# sib", "direction_ids": [sib]},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    assert (
        client.post(
            f"/api/v1/literature/{child_lit['id']}/mark-read",
            json={"status": "reading"},
            headers=h,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/literature/{sib_lit['id']}/mark-read",
            json={"status": "reading"},
            headers=h,
        ).status_code
        == 200
    )

    parent = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": pid}, headers=h)
    assert parent.status_code == 200, parent.text
    parent_body = parent.json()
    parent_text = _summaries(parent_body)
    assert parent_body.get("includes_children") is True
    assert "Child Digest Paper" in parent_text
    assert "Sibling Digest Paper" not in parent_text
    assert "Sibling Digest Manuscript" not in parent_text
    assert parent_body.get("items_empty_kind") in ("", None)

    sib_body = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": sib}, headers=h).json()
    sib_text = _summaries(sib_body)
    assert "Sibling Digest Paper" in sib_text
    assert "Sibling Digest Manuscript" in sib_text
    assert "Child Digest Paper" not in sib_text

    whole = client.get(f"/api/v1/labs/{lab}/digest", headers=h).json()
    whole_text = _summaries(whole)
    assert "Child Digest Paper" in whole_text
    assert "Sibling Digest Paper" in whole_text

    ghost = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": "missing-dir"}, headers=h)
    assert ghost.status_code == 200, ghost.text
    ghost_body = ghost.json()
    assert ghost_body.get("items") == []
    assert ghost_body.get("items_empty_kind") == "filter"
    assert "不是全实验室" in (ghost_body.get("items_empty_text") or "")
    assert "direction_id=missing-dir" in ((ghost_body.get("items_empty_next") or {}).get("href") or "")
