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


def test_g254_glossary_tutor_lab_wide_and_unused_mutex(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g254gloss")
    pid, cid, sib = _tree(client, h, lab)
    added = client.post(
        f"/api/v1/labs/{lab}/glossary",
        json={"src": "AlphaTerm", "tgt": "甲词", "do_not_translate": True},
        headers=h,
    )
    assert added.status_code == 200, added.text

    listed = client.get(f"/api/v1/labs/{lab}/glossary", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    srcs = {row.get("src") for row in listed.json()}
    assert "AlphaTerm" in srcs

    filtered = client.get(f"/api/v1/labs/{lab}/glossary", params={"direction_id": cid}, headers=h)
    assert filtered.status_code == 200, filtered.text
    assert isinstance(filtered.json(), list)
    assert {row.get("src") for row in filtered.json()} == srcs

    ghost = client.get(f"/api/v1/labs/{lab}/glossary", params={"direction_id": "missing-dir"}, headers=h)
    assert ghost.status_code == 200, ghost.text
    assert isinstance(ghost.json(), list)
    assert {row.get("src") for row in ghost.json()} == srcs
    assert "AlphaTerm" in {row.get("src") for row in ghost.json()}

    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Tutor Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Tutor Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert child_lit["id"] and sib_lit["id"]

    papers = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert papers.status_code == 200, papers.text
    assert isinstance(papers.json(), list)

    detail = client.get(f"/api/v1/literature/{child_lit['id']}", params={"direction_id": pid}, headers=h)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert isinstance(body.get("tutor_sessions"), list)
    assert body.get("tutor_scope") == "paper"
    assert body.get("tutor_follows_direction") is False

    reader = client.get(
        f"/api/v1/literature/{child_lit['id']}/detail",
        params={"direction_id": sib, "include": "tutor"},
        headers=h,
    )
    assert reader.status_code == 200, reader.text
    reader_body = reader.json()
    assert isinstance(reader_body.get("tutor_sessions"), list)
    assert reader_body.get("tutor_scope") == "paper"
    assert reader_body.get("tutor_follows_direction") is False

    mutex = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"unused": "1", "status": "succeeded"},
        headers=h,
    )
    assert mutex.status_code == 200, mutex.text
    pack = mutex.json()
    assert pack.get("empty_kind") == "filter"
    assert pack.get("items") == []
    assert "没有综述没写到的文献调研" in ((pack.get("empty_next") or {}).get("title") or "")
    assert "丢掉状态" in ((pack.get("empty_next") or {}).get("text") or "")

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)
