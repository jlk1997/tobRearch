from __future__ import annotations

from datetime import timedelta

from fengsuan.app.core.concurrency import claim_next, keep_user_signal, reclaim_stale_jobs
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.kb import KbDocument
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.kb import get_or_create_kb


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_second_heavy_job_hits_quota(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "CQ", "code": "cqu1", "description": ""}, headers=h).json()["id"]
    first = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "one"}, headers=h)
    assert first.status_code == 200, first.text
    second = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "two"}, headers=h)
    assert second.status_code == 429, second.text
    assert second.json().get("error_class") == "quota"


def test_claim_next_second_call_loses(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "CL", "code": "clm1", "description": ""}, headers=h).json()["id"]
    created = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "claim"}, headers=h)
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        first = claim_next(db, ResearchRun)
        second = claim_next(db, ResearchRun)
        assert first is not None
        assert first.id == rid
        assert first.status == "running"
        assert second is None
    finally:
        db.close()


def test_manuscript_optimistic_lock(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "CM", "code": "cms1", "description": ""}, headers=h).json()["id"]
    ms = client.post(f"/api/v1/labs/{lab}/manuscripts", json={"title": "Draft", "kind": "other", "content_md": "v1"}, headers=h)
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    got = client.get(f"/api/v1/manuscripts/{mid}", headers=h)
    assert got.status_code == 200, got.text
    stamp = got.json()["updated_at"]
    ok = client.patch(f"/api/v1/manuscripts/{mid}", json={"content_md": "v2", "expected_updated_at": stamp}, headers=h)
    assert ok.status_code == 200, ok.text
    stale = client.patch(f"/api/v1/manuscripts/{mid}", json={"content_md": "v3", "expected_updated_at": stamp}, headers=h)
    assert stale.status_code == 409, stale.text


def test_literature_list_batches_reading(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "CT", "code": "clt1", "description": ""}, headers=h).json()["id"]
    item = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Paper A"}, headers=h).json()
    client.post(f"/api/v1/literature/{item['id']}/mark-read", json={"status": "reading"}, headers=h)
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert isinstance(rows, list)
    assert rows[0]["my_reading"] == "reading"
    paged = client.get(f"/api/v1/labs/{lab}/literature", params={"page": 1, "page_size": 10}, headers=h)
    assert paged.status_code == 200
    body = paged.json()
    assert body["total"] >= 1
    assert body["items"][0]["my_reading"] == "reading"


def test_reclaim_stale_indexing(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "CR", "code": "crk1", "description": ""}, headers=h).json()["id"]
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    uid = me.json()["id"]
    db = SessionLocal()
    try:
        kb = get_or_create_kb(db, lab)
        doc = KbDocument(
            kb_id=kb.id,
            original_filename="stuck.txt",
            status="indexing",
            uploaded_by=uid,
            updated_at=utcnow() - timedelta(hours=1),
        )
        db.add(doc)
        db.commit()
        doc_id = doc.id
        n = reclaim_stale_jobs(db)
        assert n >= 1
        fresh = db.get(KbDocument, doc_id)
        assert fresh is not None
        assert fresh.status == "pending"
    finally:
        db.close()


def test_pause_wins_over_worker_session(client):
    token = _admin(client)
    h = _h(token)
    lab = client.post("/api/v1/labs", json={"name": "CP", "code": "cpw1", "description": ""}, headers=h).json()["id"]
    created = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "hold"}, headers=h)
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    worker = SessionLocal()
    try:
        run = worker.get(ResearchRun, rid)
        assert run is not None
        run.status = "running"
        worker.commit()
        paused = client.post(f"/api/v1/research-runs/{rid}/pause", headers=h)
        assert paused.status_code == 200, paused.text
        assert paused.json()["status"] == "paused"
        assert keep_user_signal(worker, run) is True
    finally:
        worker.close()
