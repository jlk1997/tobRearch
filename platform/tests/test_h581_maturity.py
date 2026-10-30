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


def test_h581_resume_feedback_stays_awaiting_if_gpu_off(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h581fb")
    rid = _park_research_awaiting(client, h, lab, "Feedback CUDA still off")
    with (
        patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
        patch("fengsuan.app.services.research_engine.current_gpu_mode", return_value="off"),
        patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=AssertionError("no graph")),
    ):
        resumed = client.post(f"/api/v1/research-runs/{rid}/resume", headers=h)
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] == "awaiting_gpu"
    fb = body.get("feedback") or {}
    assert fb.get("tone") == "warning"
    assert "等 GPU" in (fb.get("title") or "")
    assert "恢复" in (fb.get("next_step") or body.get("next_step") or "")
    assert not body.get("actual_metrics")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    hit = next(x for x in (home.json().get("queue") or {}).get("items") or [] if x.get("id") == rid)
    assert (hit.get("feedback") or {}).get("tone") == "warning"
    assert home.json()["queue"]["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"


def test_h582_resume_feedback_after_gpu_on(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h582ok")
    rid = _park_research_awaiting(client, h, lab, "Feedback CUDA after GPU on")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    hit = next(x for x in (home.json().get("queue") or {}).get("items") or [] if x.get("id") == rid)
    assert (hit.get("feedback") or {}).get("tone") == "warning"
    assert "当场" in ((hit.get("feedback") or {}).get("title") or "")
    llm = SimpleNamespace(api_key="stub", base_url="http://localhost", model="stub")
    with (
        patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
        patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=SystemExit(0)),
        patch("fengsuan.app.services.llm.resolve_llm", return_value=llm),
    ):
        resumed = client.post(f"/api/v1/research-runs/{rid}/resume", headers=h)
    assert resumed.status_code == 200, resumed.text
    fb = resumed.json().get("feedback") or {}
    assert resumed.json()["status"] != "awaiting_gpu"
    assert fb.get("tone") == "success"
    assert "跑完" in (fb.get("title") or "")
    assert not resumed.json().get("actual_metrics")


def test_h583_live_surfaces_carry_feedback(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h583ui")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "H583 Feedback Paper", "abstract": "abstract only"},
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
    latest = next(x for x in items if x["id"] == lid).get("latest_reproduce") or {}
    assert latest.get("id") == pid
    assert (latest.get("feedback") or {}).get("tone") == "warning"
    assert (latest.get("feedback") or {}).get("can_resume") is True
    db = SessionLocal()
    try:
        done = db.get(ReproduceRun, pid)
        assert done is not None
        done.status = "succeeded"
        db.commit()
    finally:
        db.close()
    rid = _park_research_awaiting(client, h, lab, "Notice feedback CUDA after enqueue")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    stale = next(x for x in notes.json() if x.get("object_id") == rid and x.get("content") == "run.awaiting_gpu")
    assert (stale.get("feedback") or {}).get("can_resume") is True
    assert stale.get("feedback", {}).get("tone") in {"warning", "info"}
    jumped = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "入队"}, headers=h)
    assert jumped.status_code == 200, jumped.text
    hit = next(x for x in jumped.json().get("items") or [] if x.get("id") == rid)
    assert (hit.get("feedback") or {}).get("title")
    detail = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert detail.status_code == 200, detail.text
    assert (detail.json().get("feedback") or {}).get("tone")
