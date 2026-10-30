from __future__ import annotations

import json
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


def test_h587_resume_rejects_running_and_allows_blocked(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h587gd")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Resume guard CUDA", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        row.status = "running"
        db.commit()
    finally:
        db.close()
    resumed = client.post(f"/api/v1/research-runs/{rid}/resume", headers=h)
    assert resumed.status_code == 409, resumed.text
    assert "运行" in (resumed.json().get("detail") or "")
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        assert row.status == "running"
        row.status = "cancelled"
        db.commit()
    finally:
        db.close()
    again = client.post(f"/api/v1/research-runs/{rid}/resume", headers=h)
    assert again.status_code == 409, again.text
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "H587 Blocked Paper", "abstract": "abstract only"},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    db = SessionLocal()
    try:
        repro = ReproduceRun(
            lab_id=lab,
            requester_id=me.json()["id"],
            literature_item_id=lit.json()["id"],
            status="blocked",
            error_class="need_data",
            current_stage="env_check",
            claimed_metrics_json="{}",
            actual_metrics_json="{}",
        )
        db.add(repro)
        db.commit()
        pid = repro.id
    finally:
        db.close()
    detail = client.get(f"/api/v1/reproduce-runs/{pid}", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json().get("can_resume") is True
    assert (detail.json().get("feedback") or {}).get("title")
    with patch("fengsuan.app.api.v1.runs.execute_reproduce_run"):
        unblocked = client.post(f"/api/v1/reproduce-runs/{pid}/resume", headers=h)
    assert unblocked.status_code == 200, unblocked.text
    assert unblocked.json()["status"] == "pending"
    assert not (unblocked.json().get("actual_metrics") or {})


def test_h588_pause_awaiting_gpu_is_honest(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h588ps")
    rid = _park_research_awaiting(client, h, lab, "Pause awaiting CUDA")
    paused = client.post(f"/api/v1/research-runs/{rid}/pause", headers=h)
    assert paused.status_code == 409, paused.text
    detail = paused.json().get("detail") or ""
    assert "结束" not in detail
    assert "GPU" in detail or "暂停" in detail
    got = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "awaiting_gpu"
    assert got.json().get("can_pause") is False
    assert got.json().get("can_cancel") is True
    assert got.json().get("can_resume") is True


def test_h589_stale_diagnosis_and_sse_follow_live(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h589st")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Stale diagnosis CUDA", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        dest = Path(row.save_path or "")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "diagnosis.json").write_text(
            json.dumps(
                {
                    "kind": "gpu",
                    "next_step": "联系管理员开启 GPU，再点恢复。不会伪造指标。",
                    "suggested_action": "ask_admin_gpu",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    finally:
        db.close()
    got = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert got.status_code == 200, got.text
    fb = got.json().get("feedback") or {}
    assert fb.get("tone") == "warning"
    assert "当场" in (fb.get("title") or "")
    assert "工人" in (fb.get("next_step") or got.json().get("next_step") or "")
    stream = client.get(f"/api/v1/research-runs/{rid}/stream", headers=h)
    assert stream.status_code == 200, stream.text
    assert "feedback" in stream.text
    assert "可当场再跑" in stream.text
    assert "can_resume" in stream.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    assert home.json()["queue"]["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"
