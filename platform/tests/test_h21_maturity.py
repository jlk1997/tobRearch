from __future__ import annotations


def _items(payload):
    return payload if isinstance(payload, list) else payload.get("items") or []


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


def test_digest_omits_kb_query_keeps_stats(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "kbns")
    up = client.post(
        f"/api/v1/labs/{lab}/kb/documents",
        files={"file": ("a.txt", b"AlphaOnlyToken appears in methods.", "text/plain")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    q = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "AlphaOnlyToken"}, headers=h)
    assert q.status_code == 200, q.text
    d = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert d.status_code == 200, d.text
    verbs = {x.get("verb") for x in d.json().get("items") or []}
    assert "kb.query" not in verbs
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    assert all(e.get("verb") != "kb.query" for e in cockpit.json().get("recent_events") or [])
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    assert (stats.json().get("this_month") or {}).get("kb_query", 0) >= 1


def test_language_done_skipped_when_writeback(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "lgdn")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Noise Paper"}, headers=h).json()
    wrote = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "NOTE_BODY", "literature_item_id": lit["id"], "write_note": True},
        headers=h,
    )
    assert wrote.status_code == 200, wrote.text
    d = client.get(f"/api/v1/labs/{lab}/digest", headers=h).json()
    verbs = [x.get("verb") for x in d.get("items") or []]
    assert "note.created" in verbs
    assert "language.done" not in verbs
    plain = client.post(f"/api/v1/labs/{lab}/language/polish", json={"text": "just polish"}, headers=h)
    assert plain.status_code == 200, plain.text
    d2 = client.get(f"/api/v1/labs/{lab}/digest", headers=h).json()
    assert "language.done" in {x.get("verb") for x in d2.get("items") or []}


def test_work_log_and_file_digest_open(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "wlfl")
    log = client.post(f"/api/v1/labs/{lab}/work-logs", json={"content": "本周写了摘要"}, headers=h)
    assert log.status_code == 200, log.text
    listed = client.get(f"/api/v1/labs/{lab}/work-logs", headers=h)
    assert listed.status_code == 200, listed.text
    hit = next(x for x in _items(listed.json()) if x["id"] == log.json()["id"])
    assert hit.get("display_name")
    up = client.post(f"/api/v1/labs/{lab}/files", files={"file": ("brief.txt", b"hello", "text/plain")}, headers=h)
    assert up.status_code == 200, up.text
    d = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert d.status_code == 200, d.text
    by_verb = {x["verb"]: x.get("href") or "" for x in d.json().get("items") or []}
    log_href = by_verb.get("work_log.created", "")
    assert f"/labs/{lab}/work-logs" in log_href
    assert f"open={log.json()['id']}" in log_href
    file_href = by_verb.get("file.uploaded", "")
    assert f"/labs/{lab}/files" in file_href
    assert f"open={up.json()['id']}" in file_href


def test_note_updated_notifies_teacher(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "ntup")
    sh = _invite_student(client, h, lab, "ntup@example.com")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Notify Note"}, headers=h).json()
    note = client.post(f"/api/v1/literature/{lit['id']}/notes", json={"content_md": "v1"}, headers=sh)
    assert note.status_code == 200, note.text
    pol = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "v2 polished", "note_id": note.json()["id"]},
        headers=sh,
    )
    assert pol.status_code == 200, pol.text
    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    hit = [n for n in notes.json() if n.get("content") == "note.updated" and n.get("object_id") == lit["id"]]
    assert hit, notes.json()
