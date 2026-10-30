from __future__ import annotations

import os


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


def test_research_run_list_exposes_linked_titles(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "rnls")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "List Linked Paper"}, headers=h).json()
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "ListDemoSet"}, headers=h).json()
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "q", "literature_item_ids": [lit["id"]], "dataset_id": ds["id"], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert listed.status_code == 200, listed.text
    hit = next(x for x in listed.json() if x["id"] == run.json()["id"])
    assert "List Linked Paper" in [p.get("title") for p in hit.get("literature") or []]
    assert (hit.get("dataset") or {}).get("name") == "ListDemoSet"


def test_language_write_note_owner_conflict_and_xor(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "lgnt")
    sh = _invite_student(client, h, lab, "lgnt@example.com")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Note Paper"}, headers=h).json()
    note = client.post(f"/api/v1/literature/{lit['id']}/notes", json={"content_md": "ORIGINAL_NOTE"}, headers=sh)
    assert note.status_code == 200, note.text
    nid = note.json()["id"]
    listed0 = client.get(f"/api/v1/literature/{lit['id']}/notes", headers=sh).json()
    stamp = next(n["updated_at"] for n in listed0 if n["id"] == nid)
    as_teacher = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "teacher should not", "note_id": nid},
        headers=h,
    )
    assert as_teacher.status_code == 403, as_teacher.text
    kept = client.get(f"/api/v1/literature/{lit['id']}/notes", headers=sh).json()
    assert any(n["id"] == nid and "ORIGINAL_NOTE" in (n.get("content_md") or "") for n in kept)
    ok = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "POLISHED_NOTE", "note_id": nid, "expected_updated_at": stamp},
        headers=sh,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json().get("wrote_note") is True
    after = client.get(f"/api/v1/literature/{lit['id']}/notes", headers=sh).json()
    body = next(n for n in after if n["id"] == nid)
    assert "POLISHED_NOTE" in body["content_md"]
    stale = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "TOO_LATE", "note_id": nid, "expected_updated_at": stamp},
        headers=sh,
    )
    assert stale.status_code == 409, stale.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "LangMs", "kind": "other", "content_md": "MS_BODY"},
        headers=sh,
    ).json()
    both = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "BOTH", "note_id": nid, "manuscript_id": ms["id"], "write_mode": "append"},
        headers=sh,
    )
    assert both.status_code == 400, both.text
    still = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=sh).json()
    assert "BOTH" not in still["content_md"]


def test_language_write_new_note_not_auto_and_digest_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "lgnew")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Digest Note Paper"}, headers=h).json()
    idle = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "should not become a note", "literature_item_id": lit["id"]},
        headers=h,
    )
    assert idle.status_code == 200, idle.text
    assert not idle.json().get("wrote_note")
    assert client.get(f"/api/v1/literature/{lit['id']}/notes", headers=h).json() == []
    created = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "NEW_NOTE_BODY", "literature_item_id": lit["id"], "write_note": True},
        headers=h,
    )
    assert created.status_code == 200, created.text
    assert created.json().get("wrote_note") is True
    notes = client.get(f"/api/v1/literature/{lit['id']}/notes", headers=h).json()
    assert any("NEW_NOTE_BODY" in (n.get("content_md") or "") for n in notes)
    d = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert d.status_code == 200, d.text
    hrefs = [x.get("href") or "" for x in d.json().get("items") or [] if x.get("verb") == "note.created"]
    assert any(lit["id"] in href and "/literature/" in href for href in hrefs), d.json().get("items")


def test_language_empty_write_mode_skips_manuscript(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "lgem")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "NoWrite", "kind": "other", "content_md": "KEEP_ME"},
        headers=h,
    ).json()
    pol = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "SHOULD_NOT_LAND", "manuscript_id": ms["id"], "write_mode": ""},
        headers=h,
    )
    assert pol.status_code == 200, pol.text
    assert not pol.json().get("wrote")
    body = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "SHOULD_NOT_LAND" not in body["content_md"]
    assert "KEEP_ME" in body["content_md"]


def test_language_note_cross_lab_forbidden(client):
    token = _admin(client)
    h = _h(token)
    lab_a = _lab(client, h, "ntca")
    lab_b = _lab(client, h, "ntcb")
    lit = client.post(f"/api/v1/labs/{lab_a}/literature", json={"title": "A Paper"}, headers=h).json()
    note = client.post(f"/api/v1/literature/{lit['id']}/notes", json={"content_md": "stay"}, headers=h).json()
    denied = client.post(
        f"/api/v1/labs/{lab_b}/language/polish",
        json={"text": "cross", "note_id": note["id"]},
        headers=h,
    )
    assert denied.status_code == 403, denied.text
    kept = client.get(f"/api/v1/literature/{lit['id']}/notes", headers=h).json()
    assert any(n["id"] == note["id"] and "stay" in (n.get("content_md") or "") for n in kept)


def test_home_reproduce_literature_title_and_task_digest_open(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "hmrp")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Home Repro Paper", "abstract": "Training uses CUDA GPU.", "authors": ["A"]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    ds_row = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "HomeSet"}, headers=h)
    assert ds_row.status_code == 200, ds_row.text
    root = os.environ.get("FILE_ROOT") or "."
    os.makedirs(root, exist_ok=True)
    run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={
            "literature_item_id": lit.json()["id"],
            "dataset_id": ds_row.json()["id"],
            "dataset_path": root,
            "claimed_metrics": {"acc": 0.9},
        },
        headers=h,
    )
    assert run.status_code == 200, run.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    titles = [x.get("literature_title") for x in home.json().get("reproduces") or []]
    assert "Home Repro Paper" in titles
    task = client.post(f"/api/v1/labs/{lab}/tasks", json={"title": "Open Me"}, headers=h)
    assert task.status_code == 200, task.text
    d = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert d.status_code == 200, d.text
    href = next((x.get("href") or "" for x in d.json().get("items") or [] if x.get("verb") == "task.assigned"), "")
    assert f"/labs/{lab}/tasks" in href
    assert f"open={task.json()['id']}" in href
