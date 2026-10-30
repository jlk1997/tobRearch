from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
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


def test_h584_create_research_feedback_without_worker(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h584cr")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Create feedback CUDA later", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "pending"
    fb = body.get("feedback") or {}
    assert fb.get("tone") == "warning"
    assert "当场" in (fb.get("title") or "")
    assert fb.get("can_resume") is True
    assert "工人" in (fb.get("next_step") or body.get("next_step") or "")
    assert not body.get("actual_metrics")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    assert home.json()["queue"]["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"


def test_h585_pause_cancel_feedback(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h585pc")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Pause cancel feedback CUDA", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    paused = client.post(f"/api/v1/research-runs/{rid}/pause", headers=h)
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    pfb = paused.json().get("feedback") or {}
    assert pfb.get("tone") == "info"
    assert "暂停" in (pfb.get("title") or "")
    assert pfb.get("can_resume") is True
    cancelled = client.post(f"/api/v1/research-runs/{rid}/cancel", headers=h)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    cfb = cancelled.json().get("feedback") or {}
    assert "取消" in (cfb.get("title") or "")
    assert cfb.get("can_resume") is False
    assert not cancelled.json().get("actual_metrics")


def test_h586_gpu_put_and_quota_feedback(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h586gp")
    rid = _park_research_awaiting(client, h, lab, "GPU put feedback CUDA")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    assert turned.json()["released_research"] >= 1
    fb = turned.json().get("feedback") or {}
    assert fb.get("tone") == "warning"
    assert "入队" in (fb.get("title") or "")
    assert "工人" in (fb.get("next_step") or "")
    assert fb.get("can_resume") is True
    assert turned.json().get("awaiting_research") == 0
    profiles = client.get("/api/v1/admin/gpu-profiles", headers=h)
    assert profiles.status_code == 200, profiles.text
    row = profiles.json()[0]
    assert (row.get("feedback") or {}).get("title")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    assert home.json()["queue"]["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"
    hit = next(x for x in (home.json().get("queue") or {}).get("items") or [] if x.get("id") == rid)
    assert (hit.get("feedback") or {}).get("can_resume") is True
    second = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Quota second CUDA", "domain": "general"},
        headers=h,
    )
    assert second.status_code == 429, second.text
    assert second.json().get("error_class") == "quota"
    assert "任务" in (second.json().get("next_step") or "")
