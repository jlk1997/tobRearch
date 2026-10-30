from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.activity import ActivityEvent
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
        raise AssertionError("GPU 关闭时不得写引擎配置")


def test_h557_research_awaits_gpu_without_starting_graph(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h557gp")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Train the baseline with CUDA on a single GPU", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    started = []

    def boom(*_a, **_k):
        started.append(1)
        raise AssertionError("发现式图不得在 awaiting_gpu 时启动")

    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        with (
            patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
            patch("fengsuan.app.services.research_engine.current_gpu_mode", return_value="off"),
            patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=boom),
        ):
            execute_research_run(db, row)
        db.refresh(row)
        assert row.status == "awaiting_gpu", (row.status, row.error_class, row.error)
        assert row.error_class == "awaiting_gpu"
        assert "未填写任何伪造指标" in (row.error or "")
        assert started == []
        failed = (
            db.query(ActivityEvent)
            .filter(ActivityEvent.object_id == rid, ActivityEvent.verb == "run.failed")
            .count()
        )
        assert failed == 0
    finally:
        db.close()

    listed = client.get(f"/api/v1/labs/{lab}/research-runs", params={"status": "awaiting_gpu"}, headers=h)
    assert listed.status_code == 200, listed.text
    listed_rows = listed.json() if isinstance(listed.json(), list) else listed.json().get("items") or []
    ids = {x["id"] for x in listed_rows}
    assert rid in ids
    pending = client.get(f"/api/v1/labs/{lab}/research-runs", params={"status": "pending"}, headers=h)
    assert pending.status_code == 200, pending.text
    pending_rows = pending.json() if isinstance(pending.json(), list) else pending.json().get("items") or []
    assert rid not in {x["id"] for x in pending_rows}
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    queue = home.json()["queue"]
    assert queue["research_awaiting_gpu"] >= 1
    assert queue["research_awaiting_href"] == f"/labs/{lab}/runs?status=awaiting_gpu"
    assert queue["awaiting_gpu_href"] == f"/labs/{lab}/reproduces?status=awaiting_gpu"
    assert queue["awaiting_gpu"] == 0
    detail = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "awaiting_gpu"
    assert "未填写任何伪造指标" in (body.get("error") or "")
    assert not body.get("actual_metrics")
    assert (body.get("artifacts") or []) == []
    second = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "another", "domain": "general"}, headers=h)
    assert second.status_code == 429, second.text
    assert second.json().get("error_class") == "quota"
    cancelled = client.post(f"/api/v1/research-runs/{rid}/cancel", headers=h)
    assert cancelled.status_code == 200, cancelled.text
    third = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "after cancel", "domain": "general"}, headers=h)
    assert third.status_code == 200, third.text


def test_h558_dataset_list_usage_and_literature(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h558ds")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Linked Dataset Paper", "abstract": "abs"},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    lid = lit.json()["id"]
    ds = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "UsageSet", "source_note": "来自公开镜像", "literature_item_ids": [lid]},
        headers=h,
    )
    assert ds.status_code == 200, ds.text
    did = ds.json()["id"]
    listed = client.get(f"/api/v1/labs/{lab}/datasets", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json() if x["id"] == did)
    assert (row.get("description") or "") == ""
    assert row.get("source_note") == "来自公开镜像"
    assert row["research_count"] == 0
    assert row["reproduce_count"] == 0
    assert row["research_href"] == f"/labs/{lab}/runs?dataset_id={did}"
    assert row["reproduce_href"] == f"/labs/{lab}/reproduces?dataset_id={did}"
    titles = [p.get("title") for p in row.get("literature") or []]
    assert "Linked Dataset Paper" in titles
    hrefs = [p.get("href") or "" for p in row.get("literature") or []]
    assert any(lid in (href or "") for href in hrefs)
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "use this set", "dataset_id": did, "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    cancel = client.post(f"/api/v1/research-runs/{run.json()['id']}/cancel", headers=h)
    assert cancel.status_code == 200, cancel.text
    repro = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": lid, "dataset_id": did},
        headers=h,
    )
    assert repro.status_code == 200, repro.text
    again = client.get(f"/api/v1/labs/{lab}/datasets", headers=h)
    assert again.status_code == 200, again.text
    row2 = next(x for x in again.json() if x["id"] == did)
    assert row2["research_count"] == 1
    assert row2["reproduce_count"] == 1
    assert row2["usage_count"] == 2


def test_h559_reproduce_guide_back_to_reader(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h559gd")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Abstract Only Paper", "abstract": "We propose a method without a PDF."},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    lid = lit.json()["id"]
    run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": lid},
        headers=h,
    )
    assert run.status_code == 200, run.text
    body = run.json()
    guide = body.get("guide") or {}
    assert guide.get("kind") == "abstract_only"
    assert f"/literature/{lid}" in (guide.get("href") or "")
    assert "panel=card" in (guide.get("href") or "")
    detail = client.get(f"/api/v1/reproduce-runs/{body['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    again = detail.json().get("guide") or {}
    assert again.get("kind") == "abstract_only"
    assert again.get("href") == f"/literature/{lid}?panel=card"
    assert not (detail.json().get("actual_metrics") or {})
