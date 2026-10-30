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
    started = []

    def boom(*_a, **_k):
        started.append(1)
        raise AssertionError("发现式图不得在 GPU off 时启动")

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
        assert started == []
    finally:
        db.close()
    return rid


def test_h560_resume_stays_awaiting_if_gpu_off(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h560rs")
    rid = _park_research_awaiting(client, h, lab, "Fine-tune with CUDA kernels")
    started = []

    def boom(*_a, **_k):
        started.append(1)
        raise AssertionError("恢复时 GPU 仍 off，不得开图")

    with (
        patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
        patch("fengsuan.app.services.research_engine.current_gpu_mode", return_value="off"),
        patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=boom),
    ):
        resumed = client.post(f"/api/v1/research-runs/{rid}/resume", headers=h)
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] == "awaiting_gpu"
    assert "未填写任何伪造指标" in (body.get("error") or "")
    assert not body.get("actual_metrics")
    assert started == []
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    db = SessionLocal()
    try:
        repro = ReproduceRun(
            lab_id=lab,
            requester_id=me.json()["id"],
            status="awaiting_gpu",
            current_stage="env_check",
            claimed_metrics_json='{"acc": 0.99}',
            actual_metrics_json="{}",
        )
        db.add(repro)
        db.commit()
        pid = repro.id
    finally:
        db.close()
    resumed_repro = client.post(f"/api/v1/reproduce-runs/{pid}/resume", headers=h)
    assert resumed_repro.status_code == 200, resumed_repro.text
    actual = resumed_repro.json().get("actual_metrics") or {}
    assert actual != {"acc": 0.99}


def test_h561_admin_gpu_on_releases_waiting(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h561rl")
    rid = _park_research_awaiting(client, h, lab, "Train ResNet with NVIDIA GPU")
    me = client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 200, me.text
    db = SessionLocal()
    try:
        db.add(
            ReproduceRun(
                lab_id=lab,
                requester_id=me.json()["id"],
                status="awaiting_gpu",
                current_stage="env_check",
                claimed_metrics_json="{}",
                actual_metrics_json="{}",
            )
        )
        db.commit()
    finally:
        db.close()
    profiles = client.get("/api/v1/admin/gpu-profiles", headers=h)
    assert profiles.status_code == 200, profiles.text
    row = profiles.json()[0]
    assert row["awaiting_research"] >= 1
    assert row["awaiting_reproduce"] >= 1
    turned = client.put("/api/v1/admin/gpu-profiles", json={"mode": "internal"}, headers=h)
    assert turned.status_code == 200, turned.text
    out = turned.json()
    assert out["mode"] == "internal"
    assert out["released_research"] >= 1
    assert out["released_reproduce"] >= 1
    assert out["awaiting_research"] == 0
    assert out["awaiting_reproduce"] == 0
    parked = client.get(f"/api/v1/research-runs/{rid}", headers=h)
    assert parked.status_code == 200, parked.text
    assert parked.json()["status"] == "pending"
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    queue = home.json()["queue"]
    assert queue["research_awaiting_gpu"] == 0
    assert queue["awaiting_gpu"] == 0
    db = SessionLocal()
    try:
        run = db.get(ResearchRun, rid)
        llm = SimpleNamespace(api_key="stub", base_url="http://localhost", model="stub")
        with (
            patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
            patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=SystemExit(0)),
            patch("fengsuan.app.services.llm.resolve_llm", return_value=llm),
        ):
            execute_research_run(db, run)
        db.refresh(run)
        assert run.status == "succeeded", (run.status, run.error_class, run.error)
        assert run.error_class != "awaiting_gpu"
    finally:
        db.close()


def test_h562_literature_list_card_empty(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h562lt")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "No PDF Paper", "abstract": "abstract only"},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    lid = lit.json()["id"]
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert listed.status_code == 200, listed.text
    items = listed.json() if isinstance(listed.json(), list) else listed.json().get("items") or []
    row = next(x for x in items if x["id"] == lid)
    assert row.get("has_pdf") is False
    assert (row.get("card_empty") or {}).get("kind") == "abstract_only"
    assert "panel=card" in (row.get("card_href") or "")
    assert lid in (row.get("card_href") or "")
    filtered = client.get(f"/api/v1/labs/{lab}/literature", params={"has_pdf": "false"}, headers=h)
    assert filtered.status_code == 200, filtered.text
    fitems = filtered.json() if isinstance(filtered.json(), list) else filtered.json().get("items") or []
    assert any(x["id"] == lid for x in fitems)
    with_pdf = client.get(f"/api/v1/labs/{lab}/literature", params={"has_pdf": "true"}, headers=h)
    assert with_pdf.status_code == 200, with_pdf.text
    pitems = with_pdf.json() if isinstance(with_pdf.json(), list) else with_pdf.json().get("items") or []
    assert lid not in {x["id"] for x in pitems}
