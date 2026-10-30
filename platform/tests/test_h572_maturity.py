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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": email, "password": "student99", "display_name": "生", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


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


def test_h572_gpu_on_notifies_actor_released(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h572rl")
    sh = _invite_student(client, h, lab, "h572s@example.com")
    rid = _park_research_awaiting(client, sh, lab, "Train CUDA released")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    assert turned.json()["released_research"] >= 1
    notes = client.get("/api/v1/notifications", headers=sh)
    assert notes.status_code == 200, notes.text
    mine = [x for x in notes.json() if x.get("content") == "run.released" and x.get("object_id") == rid]
    assert mine, notes.text
    assert mine[0].get("href") == f"/runs/{rid}"
    assert "重新入队" in (mine[0].get("next_step") or mine[0].get("title") or "")
    assert all(x.get("content") != "run.failed" for x in notes.json())
    teacher = client.get("/api/v1/notifications", headers=h)
    assert teacher.status_code == 200, teacher.text
    assert any(x.get("content") == "run.released" and x.get("href") == f"/runs/{rid}" for x in teacher.json())


def test_h573_stats_member_agents_and_notice_next_step(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h573st")
    rid = _park_research_awaiting(client, h, lab, "Stats CUDA wait")
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body.get("awaiting_research") >= 1
    assert body.get("awaiting_research_href") == f"/labs/{lab}/runs?status=awaiting_gpu"
    assert body.get("awaiting_reproduce_href") == f"/labs/{lab}/reproduces?status=awaiting_gpu"
    me = client.get("/api/v1/auth/me", headers=h).json()
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{me['id']}", headers=h)
    assert member.status_code == 200, member.text
    agent = next(x for x in member.json().get("agents") or [] if x["id"] == rid)
    assert agent["href"] == f"/runs/{rid}"
    assert agent.get("can_resume") is True
    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    note = next(x for x in notes.json() if x.get("object_id") == rid and x.get("content") == "run.awaiting_gpu")
    assert "恢复" in (note.get("next_step") or "")


def test_h574_queue_and_list_can_resume(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h574rs")
    rid = _park_research_awaiting(client, h, lab, "Resume CUDA from bar")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    hit = next(x for x in (home.json().get("queue") or {}).get("items") or [] if x.get("id") == rid)
    assert hit.get("can_resume") is True
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json() if x["id"] == rid)
    assert row.get("can_resume") is True
    resumed = client.post(f"/api/v1/research-runs/{rid}/resume", headers=h)
    assert resumed.status_code == 200, resumed.text
    again = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "awaiting_gpu"
    assert not again.json().get("actual_metrics")
