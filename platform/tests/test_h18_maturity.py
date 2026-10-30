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


def test_restore_owner_only_and_conflict(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "rsow")
    sh = _invite_student(client, h, lab, "rsow@example.com")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Stu", "kind": "other", "content_md": "v1-body"},
        headers=sh,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    patched = client.patch(f"/api/v1/manuscripts/{mid}", json={"content_md": "v2-body"}, headers=sh)
    assert patched.status_code == 200, patched.text
    vers = client.get(f"/api/v1/manuscripts/{mid}/versions", headers=sh).json()
    first = min(vers, key=lambda x: x["version"])
    as_teacher = client.post(f"/api/v1/manuscripts/{mid}/versions/{first['id']}/restore", headers=h)
    assert as_teacher.status_code == 403, as_teacher.text
    stale = client.post(
        f"/api/v1/manuscripts/{mid}/versions/{first['id']}/restore",
        params={"expected_updated_at": "2000-01-01T00:00:00+00:00"},
        headers=sh,
    )
    assert stale.status_code == 409, stale.text
    stamp = client.get(f"/api/v1/manuscripts/{mid}", headers=sh).json()["updated_at"]
    ok = client.post(
        f"/api/v1/manuscripts/{mid}/versions/{first['id']}/restore",
        params={"expected_updated_at": stamp},
        headers=sh,
    )
    assert ok.status_code == 200, ok.text
    assert "v1-body" in ok.json()["content_md"]


def test_writeback_cross_lab_forbidden(client):
    token = _admin(client)
    h = _h(token)
    lab_a = _lab(client, h, "wba1")
    lab_b = _lab(client, h, "wba2")
    ms = client.post(
        f"/api/v1/labs/{lab_a}/manuscripts",
        json={"title": "A", "kind": "other", "content_md": "BODY"},
        headers=h,
    ).json()
    stamp = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()["updated_at"]
    denied = client.post(
        f"/api/v1/labs/{lab_b}/writing/writeback",
        json={
            "manuscript_id": ms["id"],
            "text": "SHOULD_NOT",
            "write_mode": "append",
            "expected_updated_at": stamp,
        },
        headers=h,
    )
    assert denied.status_code == 403, denied.text
    body = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "SHOULD_NOT" not in body["content_md"]


def test_digest_hrefs_writing_dataset_meeting(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "dghf")
    ol = client.post(f"/api/v1/labs/{lab}/writing/outline", json={"title": "综述", "kind": "survey"}, headers=h)
    assert ol.status_code == 200, ol.text
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "DigestSet"}, headers=h)
    assert ds.status_code == 200, ds.text
    meeting = client.post(f"/api/v1/labs/{lab}/meetings", json={"title": "周会", "notes_raw": "行动: 写摘要"}, headers=h)
    assert meeting.status_code == 200, meeting.text
    minutes = client.post(f"/api/v1/meetings/{meeting.json()['id']}/minutes", json={"notes_raw": "行动: 写摘要"}, headers=h)
    assert minutes.status_code == 200, minutes.text
    d = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert d.status_code == 200, d.text
    items = d.json().get("items") or []
    by_verb = {x["verb"]: x.get("href") or "" for x in items}
    assert "/writing" in by_verb.get("writing.outline", "")
    assert ds.json()["id"] in by_verb.get("dataset.created", "")
    meet_href = by_verb.get("meeting.minutes_ready", "")
    assert f"/labs/{lab}/meetings" in meet_href
    assert f"open={meeting.json()['id']}" in meet_href


def test_improve_repro_keeps_linked_titles(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "impr")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "GPU Paper", "abstract": "Training uses CUDA GPU.", "authors": ["A"]},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    ds_row = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "DemoSet"}, headers=h)
    assert ds_row.status_code == 200, ds_row.text
    ds = os.environ.get("FILE_ROOT") or "."
    os.makedirs(ds, exist_ok=True)
    run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={
            "literature_item_id": lit.json()["id"],
            "dataset_id": ds_row.json()["id"],
            "dataset_path": ds,
            "claimed_metrics": {"acc": 0.9},
        },
        headers=h,
    )
    assert run.status_code == 200, run.text
    rid = run.json()["id"]
    improved = client.post(f"/api/v1/reproduce-runs/{rid}/improve", headers=h)
    assert improved.status_code == 200, improved.text
    body = improved.json()
    assert body.get("literature_title") == "GPU Paper"
    assert (body.get("dataset") or {}).get("name") == "DemoSet"
    assert (body.get("dataset") or {}).get("id") == ds_row.json()["id"]


def test_literature_get_matches_detail_without_touching_opened(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "ltgt")
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Open Me", "authors": []}, headers=h).json()
    pid = paper["id"]
    marked = client.post(f"/api/v1/literature/{pid}/mark-read", json={"status": "reading"}, headers=h)
    assert marked.status_code == 200, marked.text
    light = client.get(f"/api/v1/literature/{pid}", headers=h)
    assert light.status_code == 200, light.text
    body = light.json()
    assert "files" in body
    assert "analyses" in body
    assert "notes" in body
    before = body.get("last_opened_at")
    again = client.get(f"/api/v1/literature/{pid}", headers=h)
    assert again.json().get("last_opened_at") == before
    detail = client.get(f"/api/v1/literature/{pid}/detail", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json().get("last_opened_at")
    assert "files" in detail.json()
    assert "analyses" in detail.json()


def test_kb_docs_expose_source_literature(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "kbsrc")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "KB Source Paper", "abstract": "methods"},
        headers=h,
    ).json()
    added = client.post(f"/api/v1/literature/{lit['id']}/add-to-kb", headers=h)
    assert added.status_code == 200, added.text
    listed = client.get(f"/api/v1/labs/{lab}/kb/documents", headers=h)
    assert listed.status_code == 200, listed.text
    hit = next(x for x in listed.json()["items"] if x.get("literature_item_id") == lit["id"])
    assert hit.get("literature_title") == "KB Source Paper"
