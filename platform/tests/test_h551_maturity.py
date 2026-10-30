from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.analysis import card_empty_state
from fengsuan.app.services.research_engine import subgraph_progress, subgraph_progress_meta
from fengsuan.app.services.worker import process_once


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


def _items(payload):
    return payload if isinstance(payload, list) else payload.get("items") or []


def test_h551_card_empty_kinds_and_reader_detail(client):
    assert card_empty_state(files=[{"parse_status": "unusable"}], card={})["kind"] == "unusable"
    none = card_empty_state(files=[], card={})
    assert none["kind"] == "abstract_only"
    assert "上传" in (none.get("next_step") or "")
    only = card_empty_state(files=[], card={"source": "abstract_only", "methods": {"text": "abs"}})
    assert only["kind"] == "abstract_only"
    weak = card_empty_state(
        files=[{"parse_status": "ok"}],
        card={"source": "pdf", "methods": {"text": "", "ungrounded": True}},
    )
    assert weak["kind"] == "ungrounded"
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h551ce")
    lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Empty Card Paper", "abstract": "We propose a stub method on graphs."},
        headers=h,
    )
    assert lit.status_code == 200, lit.text
    iid = lit.json()["id"]
    ana = client.post(f"/api/v1/literature/{iid}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert ana.status_code == 200, ana.text
    detail = client.get(f"/api/v1/literature/{iid}/detail", headers=h)
    assert detail.status_code == 200, detail.text
    empty = detail.json().get("card_empty") or {}
    assert empty.get("kind") == "abstract_only"
    assert "PDF" in (empty.get("next_step") or "") or "上传" in (empty.get("next_step") or "")


def test_h552_progress_from_state_files(tmp_path):
    dest = tmp_path / "run"
    states = dest / "states"
    states.mkdir(parents=True)
    (states / "step_0001_literature_reviewer.json").write_text("{}", encoding="utf-8")
    (dest / "streamlit").mkdir()
    (dest / "streamlit" / "messages.json").write_text(
        '[{"node":"coder","content":"Starting coder"}]',
        encoding="utf-8",
    )
    items = subgraph_progress(str(dest), msgs=[{"node": "coder", "content": "Starting"}])
    by = {x["name"]: x for x in items}
    assert by["literature_reviewer"]["done"] is True
    assert by["data_analyzer"]["current"] is True
    assert by["coder"]["done"] is False
    meta = subgraph_progress_meta(str(dest))
    assert meta["progress_source"] == "states"
    assert meta["steps_done"] == 1
    assert meta["steps_total"] == 6


def test_h553_stats_hrefs_year_and_manuscript_status(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h553st")
    y2021 = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Year Paper", "abstract": "y", "publish_year": 2021},
        headers=h,
    )
    assert y2021.status_code == 200, y2021.text
    other = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Other Year", "abstract": "z", "publish_year": 2019},
        headers=h,
    )
    assert other.status_code == 200, other.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Submit Me", "kind": "conference", "content_md": "draft"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    patched = client.patch(f"/api/v1/manuscripts/{ms.json()['id']}", json={"status": "submitted"}, headers=h)
    assert patched.status_code == 200, patched.text
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    body = stats.json()
    tm = body.get("this_month") or {}
    assert tm.get("reading_started_href") == f"/labs/{lab}/literature?reading_status=reading&reading_scope=lab"
    assert tm.get("submitted_href") == f"/labs/{lab}/manuscripts?status=submitted"
    assert tm.get("kb_query_href") == f"/labs/{lab}/kb"
    years = {row["year"]: row for row in body.get("by_year") or []}
    assert 2021 in years
    assert years[2021]["href"] == f"/labs/{lab}/literature?year=2021"
    listed = client.get(f"/api/v1/labs/{lab}/literature", params={"year": 2021}, headers=h)
    assert listed.status_code == 200, listed.text
    items = listed.json() if isinstance(listed.json(), list) else listed.json().get("items") or []
    titles = {x.get("title") for x in items}
    assert "Year Paper" in titles
    assert "Other Year" not in titles
    submitted = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"status": "submitted"}, headers=h)
    assert submitted.status_code == 200, submitted.text
    sub_rows = submitted.json() if isinstance(submitted.json(), list) else submitted.json().get("items") or []
    assert any(x.get("title") == "Submit Me" for x in sub_rows)
    drafts = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"status": "draft"}, headers=h)
    assert drafts.status_code == 200, drafts.text
    draft_rows = drafts.json() if isinstance(drafts.json(), list) else drafts.json().get("items") or []
    assert all(x.get("title") != "Submit Me" for x in draft_rows)


def test_h554_unassigned_filter_claim_and_chip(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h554ua")
    created = client.post(f"/api/v1/labs/{lab}/tasks", json={"title": "认领我"}, headers=h)
    assert created.status_code == 200, created.text
    tid = created.json()["id"]
    inv = client.post(
        f"/api/v1/labs/{lab}/invites",
        json={"email": "h554stu@example.com", "role": "student"},
        headers=h,
    ).json()
    client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv["token"],
            "email": "h554stu@example.com",
            "password": "student99",
            "consent": True,
            "display_name": "H554 Student",
        },
    )
    stu = client.post("/api/v1/auth/login", json={"email": "h554stu@example.com", "password": "student99"}).json()["access"]
    hs = _h(stu)
    sid = client.get("/api/v1/auth/me", headers=hs).json()["id"]
    hidden = client.get(f"/api/v1/labs/{lab}/tasks", headers=hs)
    assert hidden.status_code == 200, hidden.text
    assert all(x.get("id") != tid for x in _items(hidden.json()))
    open_list = client.get(f"/api/v1/labs/{lab}/tasks", params={"assignee": "none"}, headers=hs)
    assert open_list.status_code == 200, open_list.text
    assert any(x.get("id") == tid and x.get("assignee_id") is None for x in _items(open_list.json()))
    claimed = client.patch(f"/api/v1/tasks/{tid}", json={"assignee_id": sid}, headers=hs)
    assert claimed.status_code == 200, claimed.text
    after = client.get(f"/api/v1/labs/{lab}/tasks", params={"assignee": "none"}, headers=hs)
    assert after.status_code == 200, after.text
    assert all(x.get("id") != tid for x in _items(after.json()))
    mine = client.get(f"/api/v1/labs/{lab}/tasks", headers=hs)
    assert any(x.get("id") == tid for x in _items(mine.json()))
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    pack = home.json().get("unassigned") or {}
    assert pack.get("href") == f"/labs/{lab}/tasks?assignee=none"
    assert pack.get("count", 0) == 0
    extra = client.post(f"/api/v1/labs/{lab}/tasks", json={"title": "还没人认"}, headers=h)
    assert extra.status_code == 200, extra.text
    home2 = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h).json()
    assert (home2.get("unassigned") or {}).get("count", 0) >= 1
    cock = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cock.status_code == 200, cock.text
    assert (cock.json().get("unassigned") or {}).get("href") == f"/labs/{lab}/tasks?assignee=none"
    assert (cock.json().get("unassigned") or {}).get("count", 0) >= 1


def test_h555_health_db_and_optional_redis(client):
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert (body.get("db") or {}).get("ok") is True
    redis = body.get("redis") or {}
    assert redis.get("configured") is False
    assert redis.get("ok") is None


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


@pytest.mark.nightly
def test_nightly_worker_stub_graph_succeeds(client, monkeypatch):
    if os.environ.get("FENGSUN_NIGHTLY") != "1":
        pytest.skip("FENGSUN_NIGHTLY=1 才跑工人 stub 收尾")
    monkeypatch.setenv("FENGSUN_SKIP_HEAVY", "0")
    monkeypatch.setenv("FENGSUN_LLM_STUB", "0")
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h556ny")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "nightly stub", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    llm = SimpleNamespace(api_key="stub", base_url="http://localhost", model="stub")
    with (
        patch("fengsuan.app.services.research_engine.load_engine_config", return_value=_FakeCfg()),
        patch("fengsuan.app.services.research_engine.run_openlens_graph", side_effect=SystemExit(0)),
        patch("fengsuan.app.services.llm.resolve_llm", return_value=llm),
    ):
        process_once()
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert row is not None
        assert row.status == "succeeded", (row.status, row.error_class, row.error)
    finally:
        db.close()
