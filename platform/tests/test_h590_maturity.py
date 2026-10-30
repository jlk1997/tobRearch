from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ReproduceRun, ResearchRun


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str) -> str:
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_h590_improve_rejects_cancelled_and_running(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h590im")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "H590 Improve Paper", "abstract": "abstract only"},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    uid = me.json()["id"]
    db = SessionLocal()
    try:
        dead = ReproduceRun(
            lab_id=lab,
            requester_id=uid,
            literature_item_id=lit.json()["id"],
            status="cancelled",
            current_stage="compare",
            claimed_metrics_json="{}",
            actual_metrics_json="{}",
        )
        live = ReproduceRun(
            lab_id=lab,
            requester_id=uid,
            literature_item_id=lit.json()["id"],
            status="running",
            current_stage="runExp",
            claimed_metrics_json="{}",
            actual_metrics_json="{}",
        )
        db.add_all([dead, live])
        db.commit()
        did, lid = dead.id, live.id
    finally:
        db.close()
    cancelled = client.post(f"/api/v1/reproduce-runs/{did}/improve", headers=h)
    assert cancelled.status_code == 409, cancelled.text
    assert "取消" in (cancelled.json().get("detail") or "")
    running = client.post(f"/api/v1/reproduce-runs/{lid}/improve", headers=h)
    assert running.status_code == 409, running.text
    assert "运行" in (running.json().get("detail") or "")
    db = SessionLocal()
    try:
        assert db.get(ReproduceRun, did).status == "cancelled"
        assert db.get(ReproduceRun, lid).status == "running"
    finally:
        db.close()
    dead_dto = client.get(f"/api/v1/reproduce-runs/{did}", headers=h)
    assert dead_dto.status_code == 200, dead_dto.text
    assert dead_dto.json().get("can_improve") is False
    assert (dead_dto.json().get("feedback") or {}).get("can_improve") is False


def test_h591_reenter_heavy_hits_quota(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h591qt")
    first = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Quota occupy CUDA", "domain": "general"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    occupy = first.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, occupy)
        assert row is not None
        row.status = "failed"
        row.error_class = "llm"
        db.commit()
    finally:
        db.close()
    second = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Quota second CUDA", "domain": "general"},
        headers=h,
    )
    assert second.status_code == 200, second.text
    resumed = client.post(f"/api/v1/research-runs/{occupy}/resume", headers=h)
    assert resumed.status_code == 429, resumed.text
    assert resumed.json().get("error_class") == "quota"
    assert "任务" in (resumed.json().get("next_step") or "")
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, occupy)
        assert row is not None
        assert row.status == "failed"
    finally:
        db.close()
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "H591 Done Paper", "abstract": "abstract only"},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    db = SessionLocal()
    try:
        done = ReproduceRun(
            lab_id=lab,
            requester_id=me.json()["id"],
            literature_item_id=lit.json()["id"],
            status="succeeded",
            current_stage="succeed",
            claimed_metrics_json="{}",
            actual_metrics_json="{}",
        )
        db.add(done)
        db.commit()
        pid = done.id
    finally:
        db.close()
    improved = client.post(f"/api/v1/reproduce-runs/{pid}/improve", headers=h)
    assert improved.status_code == 429, improved.text
    assert improved.json().get("error_class") == "quota"
    db = SessionLocal()
    try:
        assert db.get(ReproduceRun, pid).status == "succeeded"
    finally:
        db.close()


def test_h592_cockpit_lists_queued(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h592qu")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Queued cockpit CUDA", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        row.status = "queued"
        db.commit()
    finally:
        db.close()
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{me.json()['id']}", headers=h)
    assert member.status_code == 200, member.text
    agents = member.json().get("agents") or []
    hit = next(x for x in agents if x.get("id") == rid)
    assert hit.get("status") == "queued"
    assert (hit.get("feedback") or {}).get("title")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    assert home.json()["queue"]["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"
