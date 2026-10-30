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


def test_h575_pending_without_worker_can_resume(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h575nw")
    rid = _park_research_awaiting(client, h, lab, "Resume pending CUDA after GPU on")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    assert turned.json()["released_research"] >= 1
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    queue = home.json().get("queue") or {}
    assert queue.get("awaiting_gpu") == 0
    assert queue.get("research_awaiting_gpu") == 0
    assert queue.get("awaiting_gpu_href") == f"/labs/{lab}/reproduces?status=awaiting_gpu"
    assert queue.get("research_awaiting_href") == f"/labs/{lab}/runs?status=awaiting_gpu"
    hit = next(x for x in queue.get("items") or [] if x.get("id") == rid)
    assert hit.get("can_resume") is True
    assert "工人" in (hit.get("next_step") or "")
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json() if x["id"] == rid)
    assert row.get("status") == "pending"
    assert row.get("can_resume") is True
    assert "工人" in (row.get("next_step") or "")
    llm = SimpleNamespace(api_key="stub", base_url="http://localhost", model="stub")
    with (
        patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
        patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=SystemExit(0)),
        patch("fengsuan.app.services.llm.resolve_llm", return_value=llm),
    ):
        resumed = client.post(f"/api/v1/research-runs/{rid}/resume", headers=h)
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] != "awaiting_gpu"
    assert not body.get("actual_metrics")
    again = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert again.status_code == 200, again.text
    assert again.json()["status"] != "awaiting_gpu"
    assert not again.json().get("actual_metrics")


def test_h576_direction_overview_lists_member_agents(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h576ag")
    created = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "方向枢纽任务", "description": "", "keywords": ["cuda"]},
        headers=h,
    )
    assert created.status_code == 200, created.text
    did = created.json()["id"]
    picked = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=h)
    assert picked.status_code == 200, picked.text
    rid = _park_research_awaiting(client, h, lab, "Direction hub CUDA wait")
    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    agent = next(x for x in ov.json().get("agents") or [] if x["id"] == rid)
    assert agent["href"] == f"/runs/{rid}"
    assert agent.get("can_resume") is True
    assert agent.get("kind") == "research"


def test_h577_literature_jump_and_unread_next_step(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h577lt")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "H577 Latest Reproduce Paper", "abstract": "abstract only"},
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
    detail = client.get(f"/api/v1/literature/{lid}/detail", headers=h)
    assert detail.status_code == 200, detail.text
    latest = detail.json().get("latest_reproduce") or {}
    assert latest.get("id") == pid
    assert latest.get("href") == f"/reproduces/{pid}"
    assert latest.get("can_resume") is True
    assert "工人" in (latest.get("next_step") or "")
    db = SessionLocal()
    try:
        row = db.get(ReproduceRun, pid)
        assert row is not None
        row.status = "succeeded"
        db.commit()
    finally:
        db.close()
    rid = _park_research_awaiting(client, h, lab, "Jump pending CUDA after enqueue")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    jumped = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "入队"}, headers=h)
    assert jumped.status_code == 200, jumped.text
    found = [x for x in jumped.json().get("items") or [] if x.get("id") == rid]
    assert found, jumped.text
    assert found[0].get("status") == "pending"
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    unread = home.json().get("unread_items") or []
    assert unread
    assert any(x.get("next_step") for x in unread)
