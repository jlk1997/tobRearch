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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": email, "password": "student99", "display_name": "生", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_writeback_conflict_and_version(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "wbck")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "WB", "kind": "other", "content_md": "ORIGINAL"},
        headers=h,
    ).json()
    mid = ms["id"]
    stamp = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["updated_at"]
    bumped = client.patch(f"/api/v1/manuscripts/{mid}", json={"content_md": "CHANGED", "expected_updated_at": stamp}, headers=h)
    assert bumped.status_code == 200, bumped.text
    stale = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": mid,
            "text": "SHOULD_NOT",
            "write_mode": "append",
            "expected_updated_at": stamp,
        },
        headers=h,
    )
    assert stale.status_code == 409, stale.text
    fresh = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["updated_at"]
    before_vers = client.get(f"/api/v1/manuscripts/{mid}/versions", headers=h).json()
    ok = client.post(
        f"/api/v1/labs/{lab}/writing/writeback",
        json={
            "manuscript_id": mid,
            "text": "APPENDED_PARA",
            "write_mode": "append",
            "expected_updated_at": fresh,
        },
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json().get("wrote") is True
    got = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()
    assert "APPENDED_PARA" in got["content_md"]
    assert "SHOULD_NOT" not in got["content_md"]
    after_vers = client.get(f"/api/v1/manuscripts/{mid}/versions", headers=h).json()
    assert len(after_vers) == len(before_vers) + 1


def test_language_writeback_uses_timestamp(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "lgwb")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Lang", "kind": "other", "content_md": "BODY"},
        headers=h,
    ).json()
    stamp = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    pol = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={
            "text": "hello polished",
            "manuscript_id": ms["id"],
            "write_mode": "append",
            "expected_updated_at": stamp,
        },
        headers=h,
    )
    assert pol.status_code == 200, pol.text
    body = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "hello polished" in body["content_md"] or "BODY" in body["content_md"]
    stale = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={
            "text": "late",
            "manuscript_id": ms["id"],
            "write_mode": "append",
            "expected_updated_at": stamp,
        },
        headers=h,
    )
    assert stale.status_code == 409, stale.text


def test_status_change_notifies_owner(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "stnt")
    sh = _invite_student(client, h, lab, "stnt@example.com")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Need Review", "kind": "conference", "content_md": "x"},
        headers=sh,
    ).json()
    patched = client.patch(f"/api/v1/manuscripts/{ms['id']}", json={"status": "internal_review"}, headers=h)
    assert patched.status_code == 200, patched.text
    notes = client.get("/api/v1/notifications", headers=sh).json()
    hit = [n for n in notes if n.get("object_id") == ms["id"]]
    assert hit, notes
    assert hit[0]["object_type"] == "manuscript"


def test_research_run_exposes_linked_objects(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "rnlk")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Linked Paper"}, headers=h).json()
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "DemoSet"}, headers=h).json()
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "q", "literature_item_ids": [lit["id"]], "dataset_id": ds["id"], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    rid = run.json()["id"]
    assert run.json().get("dataset_id") == ds["id"]
    assert lit["id"] in (run.json().get("literature_item_ids") or [])
    detail = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json().get("dataset_id") == ds["id"]
    titles = [x.get("title") for x in detail.json().get("literature") or []]
    assert "Linked Paper" in titles
    assert (detail.json().get("dataset") or {}).get("name") == "DemoSet"


def test_cockpit_shows_finished_reading(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "ckfn")
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Finished One", "authors": []}, headers=h).json()
    marked = client.post(f"/api/v1/literature/{paper['id']}/mark-read", json={"status": "finished"}, headers=h)
    assert marked.status_code == 200, marked.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    titles = [r["title"] for c in cockpit["members"] for r in c.get("readings") or []]
    assert "Finished One" in titles
    me = client.get("/api/v1/auth/me", headers=h).json()
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{me['id']}", headers=h).json()
    assert any(r["title"] == "Finished One" and r["status"] == "finished" for r in member["readings"])
