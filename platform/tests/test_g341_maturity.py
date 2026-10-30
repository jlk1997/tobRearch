from __future__ import annotations

from fengsuan.app.api.v1.labs import jump_match_score


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


def test_g341_jump_score_prefix_beats_contains():
    assert jump_match_score("bert", "BERT survey") == 1
    assert jump_match_score("bert", "A survey of BERT") == 2
    assert jump_match_score("10.1000/x", "Silent", "10.1000/x") == 3
    assert jump_match_score("", "Anything") == 99


def test_g341_jump_hits_author_doi_and_keeps_empty(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g341jmp")
    silent = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={
            "title": "Silent Attention Paper",
            "doi": "10.1000/g341doi",
            "authors": ["VaswaniG341"],
            "venue": "NeurIPS",
        },
        headers=h,
    )
    assert silent.status_code == 200, silent.text
    prefix = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Jump Unique Prefix Paper", "authors": ["Other"]},
        headers=h,
    )
    assert prefix.status_code == 200, prefix.text
    later = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Later Jump Unique Prefix Note", "authors": ["Other"]},
        headers=h,
    )
    assert later.status_code == 200, later.text
    ds = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "AlphaSet", "description": "g341desc-only corpus"},
        headers=h,
    )
    assert ds.status_code == 200, ds.text
    direction = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["g341kw-only"], "description": ""},
        headers=h,
    )
    assert direction.status_code == 200, direction.text

    empty = client.get(f"/api/v1/labs/{lab}/jump", params={"q": ""}, headers=h)
    assert empty.status_code == 200, empty.text
    assert empty.json().get("items") == []

    miss = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "zzzz-no-such-g341"}, headers=h)
    assert miss.status_code == 200, miss.text
    assert miss.json().get("items") == []
    assert "tab=search" in (miss.json().get("search_href") or "")
    assert miss.json().get("jump_kind") == "lab_objects"
    steps = miss.json().get("empty_next", {}).get("steps") or []
    assert any("tab=manual" in (x.get("href") or "") for x in steps)

    by_doi = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "10.1000/g341doi"}, headers=h)
    assert by_doi.status_code == 200, by_doi.text
    doi_hit = next(x for x in (by_doi.json().get("items") or []) if x.get("id") == silent.json()["id"])
    assert doi_hit["type"] == "literature"
    assert silent.json()["id"] in (doi_hit.get("href") or "")

    by_author = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "VaswaniG341"}, headers=h)
    assert by_author.status_code == 200, by_author.text
    assert any(x.get("id") == silent.json()["id"] for x in (by_author.json().get("items") or []))

    ranked = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "Jump Unique Prefix"}, headers=h)
    assert ranked.status_code == 200, ranked.text
    lit_rows = [x for x in (ranked.json().get("items") or []) if x.get("type") == "literature"]
    ids = [x["id"] for x in lit_rows]
    assert prefix.json()["id"] in ids and later.json()["id"] in ids
    assert ids.index(prefix.json()["id"]) < ids.index(later.json()["id"])

    by_desc = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "g341desc-only"}, headers=h)
    assert by_desc.status_code == 200, by_desc.text
    assert any(x.get("id") == ds.json()["id"] and x.get("type") == "dataset" for x in (by_desc.json().get("items") or []))

    by_kw = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "g341kw-only"}, headers=h)
    assert by_kw.status_code == 200, by_kw.text
    assert any(
        x.get("id") == direction.json()["id"] and x.get("type") == "direction" for x in (by_kw.json().get("items") or [])
    )

    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    steps_home = home.json().get("next_steps") or []
    reading = next((s for s in steps_home if s.get("key") == "reading" or s.get("label") == "开始阅读"), None)
    href = (reading or {}).get("href") or ""
    if href:
        assert href.startswith(f"/labs/{lab}/literature")
        assert "unread" not in href
        if "direction_id=" in href:
            assert f"direction_id={direction.json()['id']}" in href

    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    assert isinstance(notes.json(), list)
    mutex = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"unused": "1", "status": "pending"},
        headers=h,
    )
    assert mutex.status_code == 200, mutex.text
    nxt = (mutex.json() or {}).get("empty_next") or {}
    assert "综述没写到" in f"{nxt.get('title') or ''}{nxt.get('text') or ''}"
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", params={"direction_id": direction.json()["id"]}, headers=h)
    assert cockpit.status_code == 200, cockpit.text
    assert cockpit.json().get("events_scope") == "lab"
