from __future__ import annotations

from pathlib import Path
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
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("ok", encoding="utf-8")


def test_h563_home_recommend_cockpit_card_empty(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h563hm")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "空态方向", "description": "", "keywords": ["graph"]}, headers=h)
    assert d.status_code == 200, d.text
    did = d.json()["id"]
    reading = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Home No PDF", "abstract": "abs", "direction_ids": [did]},
        headers=h,
    )
    assert reading.status_code == 200, reading.text
    rid = reading.json()["id"]
    unread = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Recommend No PDF", "abstract": "graph method", "direction_ids": [did]},
        headers=h,
    )
    assert unread.status_code == 200, unread.text
    uid = unread.json()["id"]
    marked = client.post(f"/api/v1/literature/{rid}/mark-read", json={"status": "reading"}, headers=h)
    assert marked.status_code == 200, marked.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    row = next(x for x in home.json().get("readings") or [] if x["id"] == rid)
    assert (row.get("card_empty") or {}).get("kind") == "abstract_only"
    assert "panel=card" in (row.get("card_href") or "")
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", params={"direction_id": did}, headers=h)
    assert rec.status_code == 200, rec.text
    unread_row = next(x for x in rec.json().get("lab_unread") or [] if x["id"] == uid)
    assert (unread_row.get("card_empty") or {}).get("kind") == "abstract_only"
    assert "panel=card" in (unread_row.get("card_href") or "")
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    me = client.get("/api/v1/auth/me", headers=h).json()
    card = next(x for x in cockpit.json().get("members") or [] if x["user_id"] == me["id"])
    preview = next(x for x in card.get("readings") or [] if x["id"] == rid)
    assert (preview.get("card_empty") or {}).get("kind") == "abstract_only"
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{me['id']}", headers=h)
    assert member.status_code == 200, member.text
    now = next(x for x in member.json().get("reading_now") or [] if x["id"] == rid)
    assert (now.get("card_empty") or {}).get("kind") == "abstract_only"


def test_h564_reproduce_list_guide(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h564gd")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "List Guide Paper", "abstract": "We propose without PDF."},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    lid = lit.json()["id"]
    run = client.post(f"/api/v1/labs/{lab}/reproduce-runs", json={"literature_item_id": lid}, headers=h)
    assert run.status_code == 200, run.text
    listed = client.get(f"/api/v1/labs/{lab}/reproduce-runs", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json() if x["id"] == run.json()["id"])
    guide = row.get("guide") or {}
    assert guide.get("kind") == "abstract_only"
    assert f"/literature/{lid}" in (guide.get("href") or "")
    assert "panel=card" in (guide.get("href") or "")


def test_h565_awaiting_gpu_is_activity_not_failed(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h565ev")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "Train with CUDA on one GPU", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        with (
            patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
            patch("fengsuan.app.services.research_engine.current_gpu_mode", return_value="off"),
            patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=AssertionError("no graph")),
        ):
            execute_research_run(db, row)
        db.refresh(row)
        assert row.status == "awaiting_gpu"
        verbs = [e.verb for e in db.query(ActivityEvent).filter(ActivityEvent.object_id == rid).all()]
        assert "run.awaiting_gpu" in verbs
        assert "run.failed" not in verbs
    finally:
        db.close()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    ev = next(x for x in cockpit.json().get("recent_events") or [] if x.get("object_id") == rid and x.get("verb") == "run.awaiting_gpu")
    assert "未填写任何伪造指标" in (ev.get("summary") or "")
    assert ev.get("object_type") == "research_run"
