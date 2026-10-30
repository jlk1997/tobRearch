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


def test_h569_queue_items_name_the_waiting_job(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h569qu")
    rid = _park_research_awaiting(client, h, lab, "Train CUDA attention")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    queue = home.json().get("queue") or {}
    assert queue["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"
    assert queue["research_awaiting_href"] == f"/labs/{lab}/runs?status=awaiting_gpu"
    assert queue["research_awaiting_gpu"] >= 1
    hit = next(x for x in queue.get("items") or [] if x.get("id") == rid)
    assert hit["kind"] == "research"
    assert hit["href"] == f"/runs/{rid}"
    assert hit["status"] == "awaiting_gpu"
    assert "恢复" in (hit.get("next_step") or "")
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    q2 = cockpit.json().get("queue") or {}
    assert any(x.get("id") == rid for x in q2.get("items") or [])


def test_h570_admin_gpu_lists_waiting_then_releases(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h570gp")
    rid = _park_research_awaiting(client, h, lab, "Fine-tune CUDA kernels")
    profiles = client.get("/api/v1/admin/gpu-profiles", headers=h)
    assert profiles.status_code == 200, profiles.text
    row = profiles.json()[0]
    assert row["awaiting_research"] >= 1
    waiting = next(x for x in row.get("waiting") or [] if x.get("id") == rid)
    assert waiting["href"] == f"/runs/{rid}"
    assert waiting["kind"] == "research"
    assert "恢复" in (waiting.get("next_step") or "")
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    out = turned.json()
    assert out["released_research"] >= 1
    assert out["awaiting_research"] == 0
    assert not any(x.get("id") == rid for x in out.get("waiting") or [])


def test_h571_research_diagnosis_and_cockpit_agent_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h571dg")
    rid = _park_research_awaiting(client, h, lab, "Run CUDA graph search")
    detail = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "awaiting_gpu"
    assert (body.get("diagnosis") or {}).get("suggested_action") == "ask_admin_gpu"
    assert "恢复" in (body.get("next_step") or body.get("diagnosis", {}).get("next_step") or "")
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json() if x["id"] == rid)
    assert (row.get("diagnosis") or {}).get("suggested_action") == "ask_admin_gpu"
    me = client.get("/api/v1/auth/me", headers=h).json()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    card = next(x for x in cockpit.json().get("members") or [] if x["user_id"] == me["id"])
    agent = next(x for x in card.get("agents") or [] if x["id"] == rid)
    assert agent["href"] == f"/runs/{rid}"
    assert agent["status"] == "awaiting_gpu"
    assert "恢复" in (agent.get("next_step") or "")
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    ev = next(x for x in digest.json().get("items") or [] if x.get("object_id") == rid and x.get("verb") == "run.awaiting_gpu")
    assert ev.get("href") == f"/runs/{rid}"
    assert "恢复" in (ev.get("next_step") or "")
    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    note = next(x for x in notes.json() if x.get("object_id") == rid and x.get("content") == "run.awaiting_gpu")
    assert note.get("href") == f"/runs/{rid}"
    assert "恢复" in (note.get("next_step") or "")
