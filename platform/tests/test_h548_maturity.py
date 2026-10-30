from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import (
    classify_engine_exit,
    compose_injected_question,
    execute_research_run,
)
from openlens_ai.agents.paper_reproducer.pipeline import run_until_terminal


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


def test_h548_system_exit_zero_marks_succeeded(client):
    assert classify_engine_exit(0) == "succeeded"
    assert classify_engine_exit(None) == "succeeded"
    assert classify_engine_exit(0, interrupt_after="coder") == "paused"
    assert classify_engine_exit(1) == "failed"
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h548ex")
    created = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "exit0", "domain": "general"}, headers=h)
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        llm = SimpleNamespace(api_key="stub", base_url="http://localhost", model="stub")
        with (
            patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
            patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=SystemExit(0)),
            patch("fengsuan.app.services.llm.resolve_llm", return_value=llm),
        ):
            execute_research_run(db, row)
        db.refresh(row)
        assert row.status == "succeeded", (row.status, row.error_class, row.error)
    finally:
        db.close()


def test_h549_lab_context_workspace_and_cite(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h549cx")
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Cite Me Paper", "abstract": "m"}, headers=h).json()
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "CiteSet"}, headers=h).json()
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "q", "literature_item_ids": [paper["id"]], "dataset_id": ds["id"], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    inj = run.json().get("injected") or {}
    q = compose_injected_question("研究问题", inj)
    assert "Cite Me Paper" in q
    assert "未使用" in q
    assert "CiteSet" in q
    arts = client.get(f"/api/v1/research-runs/{run.json()['id']}/artifacts", headers=h)
    assert arts.status_code == 200, arts.text
    paths = {x.get("path") for x in arts.json().get("items") or []}
    assert "lab_context.json" in paths
    assert "workspace/lab_context.json" in paths
    assert "workspace/linked_objects.md" in paths


def test_h550_improve_uses_diagnosis_not_claimed(client, tmp_path):
    claimed = {"acc": 0.99}
    seen: dict = {}

    def impl(st):
        seen["hint"] = st.get("improve_hint") or ""
        return {"actual_metrics_json": {"acc": 0.2}, "logs": "ran improve", "error_class": None}

    out = run_until_terminal(
        {
            "current_stage": "improve",
            "method_card_json": {"title": "T", "problem": "p"},
            "dataset_path": str(tmp_path),
            "gpu_mode": "off",
            "claimed_metrics": claimed,
            "diagnosis": {"kind": "not_run", "summary": "尚未得到实测指标"},
            "logs": "上次失败 traceback",
            "implement_fn": impl,
            "status": "running",
        }
    )
    assert "尚未得到实测" in (seen.get("hint") or "") or "上次失败" in (seen.get("hint") or "")
    assert out.get("actual_metrics_json") != claimed
    assert (out.get("actual_metrics_json") or {}).get("acc") == 0.2
    assert out.get("status") == "succeeded"
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h550im")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Improve Paper", "abstract": "method only"},
        headers=h,
    ).json()
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "ImproveSet"}, headers=h).json()
    run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={
            "literature_item_id": lit["id"],
            "dataset_id": ds["id"],
            "dataset_path": str(tmp_path),
            "claimed_metrics": {"acc": 0.9},
        },
        headers=h,
    )
    assert run.status_code == 200, run.text
    improved = client.post(f"/api/v1/reproduce-runs/{run.json()['id']}/improve", headers=h)
    assert improved.status_code == 200, improved.text
    body = improved.json()
    assert body.get("current_stage") != "ingest"
    actual = body.get("actual_metrics") or {}
    if isinstance(actual, str):
        import json

        actual = json.loads(actual or "{}")
    if actual:
        assert actual != {"acc": 0.9}
