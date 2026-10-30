from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fengsuan.app.core.concurrency import try_begin_execute
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import count_heavy, execute_research_run, read_run_messages


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


def test_h593_execute_cas_runs_graph_once(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h593cas")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Double execute abstract only", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        assert row.status == "pending"
        assert try_begin_execute(db, row) is True
        assert row.status == "running"
        assert try_begin_execute(db, row) is False
        assert row.status == "running"
        assert try_begin_execute(db, row, already_claimed=True) is True
        row.status = "pending"
        db.commit()
        db.refresh(row)
        llm = SimpleNamespace(api_key="stub", base_url="http://localhost", model="stub")
        with (
            patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
            patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=SystemExit(0)) as graph,
            patch("fengsuan.app.services.llm.resolve_llm", return_value=llm),
        ):
            execute_research_run(db, row)
            execute_research_run(db, row)
            assert graph.call_count == 1
        db.refresh(row)
        assert row.status == "succeeded", (row.status, row.error_class, row.error)
    finally:
        db.close()


def test_h594_count_heavy_still_quota(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h594qt")
    first = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Quota occupy abstract", "domain": "general"},
        headers=h,
    )
    assert first.status_code == 200, first.text
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    uid = me.json()["id"]
    db = SessionLocal()
    try:
        user_n, lab_n = count_heavy(db, uid, lab)
        assert user_n == 1
        assert lab_n == 1
    finally:
        db.close()
    second = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Quota second abstract", "domain": "general"},
        headers=h,
    )
    assert second.status_code == 429, second.text
    assert second.json().get("error_class") == "quota"
    assert "任务" in (second.json().get("next_step") or "")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    assert home.json()["queue"]["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"


def test_h595_idle_sse_keeps_feedback(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h595sse")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Idle stream abstract only", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    with (
        patch("fengsuan.app.api.v1.runs.sse_ticks", return_value=2),
        patch("fengsuan.app.api.v1.runs.read_run_messages", wraps=read_run_messages) as reader,
    ):
        stream = client.get(f"/api/v1/research-runs/{rid}/stream", headers=h)
    assert stream.status_code == 200, stream.text
    assert "feedback" in stream.text
    assert "可当场再跑" in stream.text
    assert reader.call_count == 1
