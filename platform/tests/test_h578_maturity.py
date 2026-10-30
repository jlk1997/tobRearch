from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ReproduceRun, ResearchRun
from fengsuan.app.services.research_engine import execute_research_run


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


class _FakeCfg:
    def __init__(self):
        self.question = ""
        self.dataset_path = ""
        self.domain = ""
        self.language = ""
        self.save_root = ""
        self.thread_id = ""
        self.save_path = ""
        self.llm = SimpleNamespace(
            chat=SimpleNamespace(api_key="", base_url="", model=""),
            vision=SimpleNamespace(api_key="", base_url="", model=""),
        )

    def save_toml(self, path):
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("ok", encoding="utf-8")


def _park_research_awaiting(client, h, lab: str, question: str) -> str:
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": question, "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        with (
            patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
            patch("fengsuan.app.services.research_engine.current_gpu_mode", return_value="off"),
            patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=AssertionError("no graph")),
        ):
            execute_research_run(db, row)
        db.refresh(row)
        assert row.status == "awaiting_gpu", (row.status, row.error_class, row.error)
    finally:
        db.close()
    return rid


def test_h578_admin_stats_see_pending_without_worker(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h578ad")
    rid = _park_research_awaiting(client, h, lab, "Admin pending CUDA after GPU on")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    out = turned.json()
    assert out["released_research"] >= 1
    assert out["awaiting_research"] == 0
    assert not any(x.get("id") == rid for x in out.get("waiting") or [])
    queued = next(x for x in out.get("pending_without_worker") or [] if x.get("id") == rid)
    assert queued.get("can_resume") is True
    assert "工人" in (queued.get("next_step") or "")
    assert out.get("pending_research") >= 1
    profiles = client.get("/api/v1/admin/gpu-profiles", headers=h)
    assert profiles.status_code == 200, profiles.text
    row = profiles.json()[0]
    assert row["awaiting_research"] == 0
    assert any(x.get("id") == rid for x in row.get("pending_without_worker") or [])
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body.get("awaiting_research") == 0
    assert body.get("awaiting_research_href") == f"/labs/{lab}/runs?status=awaiting_gpu"
    assert body.get("awaiting_reproduce_href") == f"/labs/{lab}/reproduces?status=awaiting_gpu"
    assert body.get("pending_research") >= 1
    assert body.get("pending_research_href") == f"/labs/{lab}/runs?status=pending"
    assert body.get("pending_reproduce_href") == f"/labs/{lab}/reproduces?status=pending"


def test_h579_live_notice_and_jump_can_resume(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h579lv")
    rid = _park_research_awaiting(client, h, lab, "Live notice CUDA after enqueue")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    stale = next(x for x in notes.json() if x.get("object_id") == rid and x.get("content") == "run.awaiting_gpu")
    assert stale.get("live_status") == "pending"
    assert stale.get("can_resume") is True
    assert "工人" in (stale.get("next_step") or "")
    released = next(x for x in notes.json() if x.get("object_id") == rid and x.get("content") == "run.released")
    assert released.get("can_resume") is True
    jumped = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "入队"}, headers=h)
    assert jumped.status_code == 200, jumped.text
    hit = next(x for x in jumped.json().get("items") or [] if x.get("id") == rid)
    assert hit.get("can_resume") is True
    assert "工人" in (hit.get("next_step") or "")


def test_h580_literature_digest_health_live(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h580lt")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "H580 Latest Reproduce Paper", "abstract": "abstract only"},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    lid = lit.json()["id"]
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    db = SessionLocal()
    try:
        repro = ReproduceRun(
            lab_id=lab,
            requester_id=me.json()["id"],
            literature_item_id=lid,
            status="pending",
            current_stage="env_check",
            claimed_metrics_json="{}",
            actual_metrics_json="{}",
        )
        db.add(repro)
        db.commit()
        pid = repro.id
    finally:
        db.close()
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert listed.status_code == 200, listed.text
    items = listed.json() if isinstance(listed.json(), list) else listed.json().get("items") or []
    row = next(x for x in items if x["id"] == lid)
    latest = row.get("latest_reproduce") or {}
    assert latest.get("id") == pid
    assert latest.get("href") == f"/reproduces/{pid}"
    assert latest.get("can_resume") is True
    assert "工人" in (latest.get("next_step") or "")
    db = SessionLocal()
    try:
        done = db.get(ReproduceRun, pid)
        assert done is not None
        done.status = "succeeded"
        db.commit()
    finally:
        db.close()
    rid = _park_research_awaiting(client, h, lab, "Digest live CUDA after GPU on")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    ev = next(x for x in digest.json().get("items") or [] if x.get("object_id") == rid)
    assert ev.get("can_resume") is True
    assert ev.get("live_status") == "pending"
    assert ev.get("next_step")
    health = client.get("/health")
    assert health.status_code == 200, health.text
    worker = health.json().get("worker") or {}
    assert worker.get("enabled") is False
    assert "工人" in (worker.get("next_step") or "")
