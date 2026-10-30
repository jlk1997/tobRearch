from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.activity import cockpit_feed_watermark, cockpit_lab_events
from fengsuan.app.services.metrics import rollup_lab_day


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


def test_g268_cockpit_stays_lab_wide_and_progress_window(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g268perf")
    pid, cid, sib = _tree(client, h, lab)
    child = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Perf Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Perf Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert child["id"] and sib_lit["id"]
    assert (
        client.post(f"/api/v1/literature/{child['id']}/mark-read", json={"status": "reading"}, headers=h).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/literature/{sib_lit['id']}/mark-read", json={"status": "reading"}, headers=h).status_code
        == 200
    )

    whole = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert whole.status_code == 200, whole.text
    body = whole.json()
    assert body.get("events_scope") == "lab"
    assert body.get("events_follows_direction") is False
    assert isinstance(body.get("recent_events"), list)
    summaries = " ".join(str(x.get("summary") or "") for x in body.get("recent_events") or [])
    assert "Child Perf Paper" in summaries
    assert "Sibling Perf Paper" in summaries

    filtered = client.get(f"/api/v1/labs/{lab}/cockpit", params={"direction_id": cid}, headers=h)
    assert filtered.status_code == 200, filtered.text
    filtered_body = filtered.json()
    assert filtered_body.get("events_scope") == "lab"
    assert filtered_body.get("events_follows_direction") is False
    filtered_text = " ".join(str(x.get("summary") or "") for x in filtered_body.get("recent_events") or [])
    assert "Sibling Perf Paper" in filtered_text

    stream = client.get(f"/api/v1/labs/{lab}/events/stream", params={"direction_id": sib}, headers=h)
    assert stream.status_code == 200, stream.text
    assert "snapshot" in stream.text
    assert "Sibling Perf Paper" in stream.text
    assert "Child Perf Paper" in stream.text

    prog = client.get(f"/api/v1/labs/{lab}/progress", params={"direction_id": pid}, headers=h)
    assert prog.status_code == 200, prog.text
    pack = prog.json()
    assert isinstance(pack.get("items"), list)
    assert pack.get("scan_window") == 800
    assert pack.get("scan_capped") is True
    assert pack.get("includes_children") is True
    prog_text = " ".join(str(x.get("summary") or "") for x in pack.get("items") or [])
    assert "Child Perf Paper" in prog_text
    assert "Sibling Perf Paper" not in prog_text

    digest = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": pid}, headers=h)
    assert digest.status_code == 200, digest.text
    dbody = digest.json()
    assert isinstance(dbody.get("items"), list)
    assert dbody.get("scan_window") == 800
    assert dbody.get("scan_capped") is True

    members = client.get(f"/api/v1/labs/{lab}/members", headers=h)
    assert members.status_code == 200, members.text
    assert isinstance(members.json(), list)

    db = SessionLocal()
    try:
        first = cockpit_feed_watermark(db, lab)
        second = cockpit_feed_watermark(db, lab)
        assert first == second
        assert cockpit_lab_events(db, lab, None, limit=12)
        assert rollup_lab_day(db, lab) is True
        db.commit()
        assert rollup_lab_day(db, lab) is False
    finally:
        db.close()
