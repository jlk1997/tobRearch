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


def test_g9_unpinned_core_matches_all_direction_papers(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g9all")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "G9", "keywords": ["g9"], "description": ""}, headers=h)
    did = d.json()["id"]
    a = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G9 Core Paper Alpha TitleX", "abstract": "a", "direction_ids": [did]},
        headers=h,
    ).json()
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G9 Core Paper Beta TitleX", "abstract": "b", "direction_ids": [did]},
        headers=h,
    )
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "G9 未钉", "kind": "other", "content_md": f"## 相关工作\n点名 {a['title']}", "direction_ids": [did]},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    listed = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"core_missing": True, "direction_id": did}, headers=h)
    assert listed.status_code == 200, listed.text
    rows = listed.json() if isinstance(listed.json(), list) else listed.json().get("items") or []
    row = next(x for x in rows if x["id"] == ms.json()["id"])
    assert int(row.get("core_missing_count") or 0) == 1


def test_g9_pin_changes_denominator(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g9pin")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "G9p", "keywords": ["p"], "description": ""}, headers=h)
    did = d.json()["id"]
    a = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G9 Pin Alpha Mentioned TitleX", "abstract": "a", "direction_ids": [did]},
        headers=h,
    ).json()
    b = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G9 Pin Beta Missing TitleX", "abstract": "b", "direction_ids": [did]},
        headers=h,
    ).json()
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G9 Pin Gamma Ignored TitleX", "abstract": "c", "direction_ids": [did]},
        headers=h,
    )
    pin_a = client.patch(
        f"/api/v1/labs/{lab}/literature/{a['id']}/core",
        json={"direction_id": did, "is_core": True},
        headers=h,
    )
    pin_b = client.patch(
        f"/api/v1/labs/{lab}/literature/{b['id']}/core",
        json={"direction_id": did, "is_core": True},
        headers=h,
    )
    assert pin_a.status_code == 200, pin_a.text
    assert pin_b.status_code == 200, pin_b.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "G9 已钉",
            "kind": "other",
            "content_md": f"## 相关工作\n{a['title']}",
            "direction_ids": [did],
        },
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    listed = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"core_missing": True, "direction_id": did}, headers=h)
    rows = listed.json() if isinstance(listed.json(), list) else listed.json().get("items") or []
    row = next(x for x in rows if x["id"] == mid)
    assert int(row.get("core_missing_count") or 0) == 1
    cores = client.get(
        f"/api/v1/labs/{lab}/literature",
        params={"direction_id": did, "core": True, "page": 1},
        headers=h,
    )
    ids = {x["id"] for x in (cores.json().get("items") if isinstance(cores.json(), dict) else cores.json())}
    assert ids == {a["id"], b["id"]}
